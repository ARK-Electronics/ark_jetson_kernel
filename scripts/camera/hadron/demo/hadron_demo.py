#!/usr/bin/env python3
"""Browser demo for the FLIR Hadron 640R on an ARK Jetson carrier.

Serves one page with the EO camera, through Argus, beside the Boson, each as an MJPEG
stream, and the settings that change them. The adapter's IMU shows which way the Hadron
tilts, and the sync line shows who sees the Boson's frame sync pulses. Run it on the
Jetson and open http://<jetson>:8642/, or start it from a PC with
scripts/camera/hadron/demo.sh.
"""
import argparse
import collections
import glob
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "view"))
import boson  # noqa: E402
from icm45686 import Icm45686, find_bus  # noqa: E402
from syncline import SyncLine  # noqa: E402

PREVIEW_W = 1280        # Argus scales each mode to this width, so the page gets the whole field of view
STREAM_FPS = 30         # frames a second sent to the browser, whatever the sensor runs at
WB_MODES = ("Off", "Auto", "Incandescent", "Fluorescent", "Warm fluorescent", "Daylight", "Cloudy", "Twilight",
            "Shade")
SINK = "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
# The camera's axes (x right, y down, z out of the lens) in the axes of the adapter's IMU.
IMU_TO_CAMERA = ((0, 1, 0), (1, 0, 0), (0, 0, -1))


def log(msg):
    # stderr: nvarguscamerasrc fills stdout with its own chatter, which the launcher drops.
    print(time.strftime("%H:%M:%S"), msg, file=sys.stderr, flush=True)


def dt_text(path):
    with open(path, "rb") as f:
        return f.read().rstrip(b"\0").decode()


def find_node(name_part):
    """The capture node (index 0) whose V4L2 name contains name_part, or None."""
    for name in sorted(glob.glob("/sys/class/video4linux/video*/name")):
        node = os.path.dirname(name)
        try:
            with open(name) as f, open(f"{node}/index") as g:
                if name_part in f.read() and g.read().strip() == "0":
                    return "/dev/" + os.path.basename(node)
        except OSError:
            continue
    return None


def sync_pad_line():
    """GPIO line name of the pad the overlay made the sync input (extperiph2_clk_pp1 is PP.01), or None."""
    for pins in glob.glob("/sys/firmware/devicetree/base/bus@0/pinmux@*/exp-header-pinmux/hadron-eo-sync/nvidia,pins"):
        port = dt_text(pins).rsplit("_", 1)[-1]
        return f"{port[:-1].upper()}.{int(port[-1]):02d}"
    return None


def sensor_modes():
    """The EO modes on this port, from the sensor's device-tree node."""
    modes = []
    for node in sorted(glob.glob("/sys/bus/i2c/drivers/ov64b40/*/of_node/mode[0-9]")):
        def num(prop):
            return int(dt_text(f"{node}/{prop}"))
        top = num("max_framerate") / 1e6
        rates = sorted({r for r in (120, 60, 30, 15, 10, 5) if r <= top} | {int(top)}, reverse=True)
        modes.append(dict(id=int(node[-1]), w=num("active_w"), h=num("active_h"), max_fps=round(top, 2), rates=rates,
                          exposure_ms=[num("min_exp_time") / 1000, num("max_exp_time") / 1000],
                          gain=[num("min_gain_val") / num("gain_factor"), num("max_gain_val") / num("gain_factor")]))
    return modes


class Stream:
    """A GStreamer pipeline that ends in JPEG frames. The latest frame is kept for the page."""

    def __init__(self, name, ended="the stream ended"):
        self.name = name
        self.ended = ended
        self.pipeline = None
        self.jpeg = None
        self.seq = 0
        self.error = None
        self.cond = threading.Condition()
        self.times = collections.deque(maxlen=STREAM_FPS * 2)

    def start(self, desc):
        self.stop()
        self.error = None
        self.times.clear()
        try:
            pipeline = Gst.parse_launch(desc)
        except Exception as e:      # GLib.Error: the description did not parse or an element is missing
            self.error = str(e)
            return
        pipeline.get_by_name("sink").connect("new-sample", self._on_sample)
        self.pipeline = pipeline
        threading.Thread(target=self._watch, args=(pipeline,), daemon=True).start()
        pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        pipeline, self.pipeline = self.pipeline, None
        if pipeline:
            pipeline.set_state(Gst.State.NULL)

    def element(self, name):
        return self.pipeline.get_by_name(name) if self.pipeline else None

    def _watch(self, pipeline):
        bus = pipeline.get_bus()
        while self.pipeline is pipeline:
            msg = bus.timed_pop_filtered(200 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                self.error = f"{err.message} ({(debug or '').splitlines()[-1][-160:]})"
            else:
                self.error = self.ended
            log(f"{self.name}: {self.error}")
            return

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            with self.cond:
                self.jpeg = bytes(info.data)
                self.seq += 1
                self.times.append(time.monotonic())
                self.cond.notify_all()
            buf.unmap(info)
        return Gst.FlowReturn.OK

    def wait(self, seq, timeout=1.0):
        """The next frame after number seq as (number, JPEG), or the same number on timeout."""
        with self.cond:
            self.cond.wait_for(lambda: self.seq != seq, timeout)
            return self.seq, self.jpeg

    def fps(self):
        t = list(self.times)
        if len(t) < 2 or time.monotonic() - t[-1] > 2:
            return 0.0
        return round((len(t) - 1) / (t[-1] - t[0]), 1)


class Motion:
    """The adapter's ICM-45686 and the Hadron sync line, which the IMU's FSYNC pin and a carrier pad both watch."""

    def __init__(self):
        self.line = self.imu = None
        self.error = None
        self.line_name = sync_pad_line()
        if self.line_name:
            try:
                self.line = SyncLine(self.line_name)
                self.line.start()
            except OSError as e:
                log(f"sync line {self.line_name}: {e}")
        bus = find_bus()
        if bus is None:
            self.error = "no ICM-45686 on any I2C bus: is the adapter's I2C cable on a carrier I2C connector?"
            return
        try:
            self.imu = Icm45686(bus=bus, fsync=True, pulses=self.line.edges if self.line else None)
            self.imu.start()
        except OSError as e:
            self.error = f"IMU on i2c-{bus}: {e}"

    def tilt(self):
        """Roll and pitch in degrees, from gravity over the last tenth of a second. None without an IMU."""
        if not self.imu or self.imu.error:
            return None
        rows = self.imu.snapshot(0.1)
        if not len(rows):
            return None
        up = rows[:, 1:4].mean(axis=0)      # at rest the accelerometer reads the force holding it up
        norm = math.sqrt(sum(v * v for v in up)) or 1.0
        x, y, z = (-sum(m * u for m, u in zip(row, up)) / norm for row in IMU_TO_CAMERA)
        return dict(roll=round(math.degrees(math.atan2(x, y)), 1),
                    pitch=round(math.degrees(math.atan2(-z, math.hypot(x, y))), 1))

    def pulses(self):
        """Sync pulses a second seen on the carrier pad and tagged by the IMU."""
        now = time.monotonic()
        seen = sum(1 for t in list(self.line.edges) if t > now - 1.0) if self.line else None
        tagged = None
        if self.imu and not self.imu.error:
            latest = getattr(self.imu, "next_index", 0) / self.imu.odr
            tagged = sum(1 for t in list(self.imu.tags) if t > latest - 1.0)
        return dict(line=self.line_name, seen=seen, tagged=tagged)

    def close(self):
        if self.imu:
            self.imu.stopping.set()
        if self.line:
            self.line.close()


class Demo:
    def __init__(self):
        self.lock = threading.Lock()
        self.modes = sensor_modes()
        self.eo_node = find_node("ov64b40")
        self.ir_node = find_node("Boson")
        # Argus reports a camera it cannot open by ending the stream.
        self.eo = Stream("EO", "Argus gave no stream: another program may hold the camera, or the module is "
                               "short of memory for this mode")
        self.ir = Stream("IR")
        self.set = dict(mode=2, fps=30, auto=True, exposure_ms=8.0, gain=4.0, wb=1, saturation=1.0)
        self.cam = None
        self.ir_info = dict(palette=None, serial=None, sync=None, error=None)
        self.palette_at_start = self.sync_at_start = None
        self.motion = Motion()
        self.sensor = {}
        self.sensor_read = 0.0
        if self.modes and self.set["mode"] >= len(self.modes):
            self.set["mode"] = 0

    # EO

    def mode(self):
        return self.modes[self.set["mode"]]

    def _exposure_props(self):
        """nvarguscamerasrc's exposure, gain and ISP gain ranges: its own for auto, one value each for manual."""
        s = self.set
        if s["auto"]:
            return {}
        ns = int(s["exposure_ms"] * 1e6)
        return {"exposuretimerange": f"{ns} {ns}", "gainrange": f"{s['gain']:g} {s['gain']:g}",
                "ispdigitalgainrange": "1 1"}

    def start_eo(self):
        if not self.modes or not self.eo_node:
            self.eo.error = "no OV64B40 found: is a Hadron EO overlay selected in jetson-io?"
            return
        s, m = self.set, self.mode()
        height = round(PREVIEW_W * m["h"] / m["w"] / 2) * 2
        props = {"sensor-id": 0, "sensor-mode": s["mode"], "wbmode": s["wb"], "saturation": s["saturation"]}
        props.update(self._exposure_props())
        src = " ".join(f'{k}="{v}"' for k, v in props.items())
        self.eo.start(f"nvarguscamerasrc name=src {src} "
                      f"! video/x-raw(memory:NVMM),width={PREVIEW_W},height={height},framerate={s['fps']}/1 "
                      f"! nvvidconv ! video/x-raw,format=I420 ! videorate drop-only=true max-rate={STREAM_FPS} "
                      f"! jpegenc quality=85 ! {SINK}")

    def start_ir(self):
        if not self.ir_node:
            self.ir.error = "no Boson found on USB"
            return
        self.ir.start(f"v4l2src device={self.ir_node} ! video/x-raw,format=I420,width=640,height=512,"
                      f"framerate={STREAM_FPS}/1 ! jpegenc quality=85 ! {SINK}")
        try:
            self.cam = boson.Boson()
            self.ir_info.update(serial=self.cam.serial(), palette=self.cam.color_lut(), sync=self.cam.ext_sync())
            self.palette_at_start, self.sync_at_start = self.ir_info["palette"], self.ir_info["sync"]
        except OSError as e:
            self.ir_info["error"] = f"Boson serial port: {e}"

    def apply(self, changes):
        """Take settings from the page."""
        with self.lock:
            if self.modes:
                self._apply_eo(changes)
            if self.cam:
                try:
                    if "palette" in changes:
                        self.cam.set_color_lut(int(changes["palette"]))
                        self.ir_info["palette"] = self.cam.color_lut()
                    if changes.get("ffc"):
                        self.cam.run_ffc()
                    if "sync" in changes:
                        self.cam.set_ext_sync(boson.EXT_SYNC_MASTER if changes["sync"] else boson.EXT_SYNC_DISABLED)
                        self.ir_info["sync"] = self.cam.ext_sync()
                    self.ir_info["error"] = None
                except OSError as e:
                    self.ir_info["error"] = str(e)

    def _apply_eo(self, changes):
        """nvarguscamerasrc takes white balance and saturation while it runs. It reads the mode, the
        frame rate and the exposure ranges only when it starts, so a change to those restarts the stream."""
        s = self.set
        before = (s["mode"], s["fps"], self._exposure_props())
        mode = int(changes.get("mode", s["mode"]))
        if 0 <= mode < len(self.modes):
            s["mode"] = mode
        m = self.mode()
        fps = int(changes.get("fps", s["fps"]))
        if fps not in m["rates"]:
            fps = max([r for r in m["rates"] if r <= 30] or m["rates"][-1:])
        s["fps"] = fps
        if "auto" in changes:
            s["auto"] = bool(changes["auto"])
        # A manual exposure stays inside the frame period and the mode's range.
        lo, hi = m["exposure_ms"]
        exposure = float(changes.get("exposure_ms", s["exposure_ms"]))
        s["exposure_ms"] = round(min(max(exposure, lo), hi, 1000 / fps), 3)
        s["gain"] = min(max(float(changes.get("gain", s["gain"])), m["gain"][0]), m["gain"][1])
        if 0 <= int(changes.get("wb", s["wb"])) < len(WB_MODES):
            s["wb"] = int(changes.get("wb", s["wb"]))
        s["saturation"] = min(max(float(changes.get("saturation", s["saturation"])), 0.0), 2.0)

        src = self.eo.element("src")
        if src is None or self.eo.error or before != (s["mode"], s["fps"], self._exposure_props()):
            self.start_eo()
        else:
            src.set_property("wbmode", s["wb"])
            src.set_property("saturation", s["saturation"])

    def read_sensor(self):
        """What the sensor runs at, from the driver's controls: under auto exposure only they know."""
        if time.monotonic() - self.sensor_read < 0.9 or not self.eo_node:
            return self.sensor
        self.sensor_read = time.monotonic()
        try:
            out = subprocess.run(["v4l2-ctl", "-d", self.eo_node, "-C", "exposure,gain,frame_rate"],
                                 capture_output=True, text=True, timeout=2).stdout
            v = {k.strip(): int(x) for k, x in (line.split(":") for line in out.splitlines() if ":" in line)}
            self.sensor = dict(exposure_ms=round(v["exposure"] / 1000, 2), gain=round(v["gain"] / 16, 2),
                               fps=round(v["frame_rate"] / 1e6, 2))
        except (OSError, subprocess.SubprocessError, KeyError, ValueError):
            self.sensor = {}
        return self.sensor

    def state(self):
        return dict(eo=dict(settings=self.set, modes=self.modes, wb_modes=WB_MODES, fps=self.eo.fps(),
                            error=self.eo.error, sensor=self.read_sensor()),
                    ir=dict(fps=self.ir.fps(), error=self.ir.error or self.ir_info["error"],
                            palette=self.ir_info["palette"], palettes=boson.COLOR_LUTS,
                            serial=self.ir_info["serial"], controls=self.cam is not None),
                    motion=dict(error=self.motion.error or (self.motion.imu.error if self.motion.imu else None),
                                master=self.ir_info["sync"] == boson.EXT_SYNC_MASTER, **self.motion.pulses()))

    def close(self):
        self.eo.stop()
        self.ir.stop()
        self.motion.close()
        if self.cam:
            try:
                if self.palette_at_start is not None:
                    self.cam.set_color_lut(self.palette_at_start)
                if self.sync_at_start is not None:
                    self.cam.set_ext_sync(self.sync_at_start)
            except OSError as e:
                log(f"Boson palette and sync mode not restored: {e}")
            self.cam.close()
            self.cam = None


class Handler(BaseHTTPRequestHandler):
    demo = None

    def log_message(self, fmt, *args):
        pass

    def _send(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(f.read(), "text/html; charset=utf-8")
        elif path == "/state":
            self._send(json.dumps(self.demo.state()).encode(), "application/json")
        elif path in ("/eo.mjpg", "/ir.mjpg"):
            self._stream(self.demo.eo if path == "/eo.mjpg" else self.demo.ir)
        elif path == "/tilt":
            self._tilt()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/set":
            self.send_error(404)
            return
        try:
            changes = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self.demo.apply(changes)
        except (ValueError, TypeError) as e:
            self.send_error(400, str(e))
            return
        self._send(json.dumps(self.demo.state()).encode(), "application/json")

    def _stream(self, stream):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        seq = -1
        try:
            while True:
                new, jpeg = stream.wait(seq)
                if new == seq or jpeg is None:
                    continue        # no frame yet: the stream is starting, restarting or failed
                seq = new
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg))
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


    def _tilt(self):
        """Server-sent events: the tilt twenty times a second."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                self.wfile.write(b"data: " + json.dumps(self.demo.motion.tilt()).encode() + b"\n\n")
                self.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8642)
    args = p.parse_args()

    Gst.init(None)
    demo = Handler.demo = Demo()
    try:
        server = ThreadingHTTPServer(("", args.port), Handler)
    except OSError as e:
        raise SystemExit(f"port {args.port}: {e}. Is the demo already running?")
    server.daemon_threads = True

    def stop(signum, frame):
        raise SystemExit(f"signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        demo.start_eo()
        demo.start_ir()
        log(f"Hadron demo on port {args.port}: EO {demo.eo_node}, IR {demo.ir_node}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        demo.close()
        log("stopped")


if __name__ == "__main__":
    main()
