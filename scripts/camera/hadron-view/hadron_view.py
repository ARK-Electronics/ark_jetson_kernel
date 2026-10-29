#!/usr/bin/env python3
"""Live view of the FLIR Hadron 640R on the ARK Hadron adapter.

EO (OV64B40 on the tegra VI) and IR (Boson over UVC) side by side, with the
adapter's ICM-45686 plotted against the camera frame timing. Every timestamp is
put on CLOCK_MONOTONIC so the panel shows where IMU samples land in each frame.

Frame sync: while the view runs, the Boson is the Hadron's ext sync master and
pulses pin 43 (EXT_VSYNC) at its 60 Hz core rate. The IMU's FSYNC input (R12)
tags each pulse on the IMU's clock and the Jetson timestamps it on the carrier's
MCLK pad, which together put the IMU samples on CLOCK_MONOTONIC. The Boson's
smart averager sends one frame per two core frames over USB, each starting 9.5 ms
after a pulse.

The EO has no Argus ISP tuning yet, so its raw Bayer frames are rendered here:
2x2 Bayer binning, gray-world white balance, auto exposure and a gamma LUT.
Quit with q, Esc or a right click.
"""
import argparse
import collections
import ctypes
import math
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# nv3dsink drives X11 from its streaming thread while GTK draws on the main one.
X11 = ctypes.CDLL("libX11.so.6")
X11.XInitThreads()
# Idle OpenBLAS workers and NVIDIA's EGL (waiting in nv3dsink) both spin on
# sched_yield and starve the capture thread.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("__GL_YIELD", "USLEEP")

import numpy as np  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gst, GstVideo, Gtk  # noqa: E402,F401

import archtimer  # noqa: E402
import boson  # noqa: E402
from icm45686 import Icm45686  # noqa: E402
from syncline import SyncLine  # noqa: E402
from v4l2cap import MEMORY_USERPTR, Capture  # noqa: E402

EO_W, EO_H, EO_MODE = 2304, 1296, 2     # OV64B40 sensor_mode 2
EO_LINE_TIME = 2974 / 679.68e6          # mode 2 line_length / pix_clk from the DT
EO_READOUT = EO_H * EO_LINE_TIME        # first to last row
OUT_W, OUT_H = EO_W // 2, EO_H // 2     # one RGB pixel per 2x2 Bayer cell
BLACK, FULL = 64, 1023                  # 10-bit black level and full scale
AE_TARGET = 0.14                        # mean green above black, linear
AE_EVERY = 8                            # frames between exposure updates
WORKERS = 4                             # threads for the EO render, which runs without the GIL
IR_W, IR_H, IR_FPS = 640, 512, 60
IR_CORE_FPS = 60                        # Boson core frames, one sync pulse each
SYNC_HISTORY = 25 * IR_CORE_FPS         # pulses kept: the phase plot spans 20 s

# 10-bit code to linear light; the render LUTs add white balance and gamma.
LIN = np.clip((np.arange(1024) - BLACK) / (FULL - BLACK), 0, None).astype(np.float32)
HERE = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(HERE, "view.pid")

COLOR = dict(bg=(0.06, 0.07, 0.09), fg=(0.90, 0.91, 0.93), dim=(0.55, 0.58, 0.62), grid=(0.18, 0.20, 0.23),
             x=(0.95, 0.42, 0.42), y=(0.40, 0.82, 0.45), z=(0.35, 0.65, 0.98), warn=(1.0, 0.45, 0.35),
             eo=(1.0, 0.75, 0.35), ir=(0.97, 0.47, 0.70), imu=(0.40, 0.85, 0.92))


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def make_lut(gain):
    return (np.clip(LIN * gain, 0, 1) ** (1 / 2.2) * 255 + 0.5).astype(np.uint8)


def find_node(name_part):
    """The capture node (index 0) whose V4L2 name contains name_part."""
    base = "/sys/class/video4linux"
    for node in sorted(os.listdir(base), key=lambda n: int(n[5:]) if n[5:].isdigit() else 99):
        try:
            with open(f"{base}/{node}/name") as f, open(f"{base}/{node}/index") as g:
                if name_part in f.read() and g.read().strip() == "0":
                    return f"/dev/{node}"
        except OSError:
            continue
    raise SystemExit(f"no V4L2 device named *{name_part}*")


def load_renderer():
    """eorender.c, built next to this file when missing or out of date."""
    src, lib = os.path.join(HERE, "eorender.c"), os.path.join(HERE, "libeorender.so")
    if not os.path.exists(lib) or os.path.getmtime(lib) < os.path.getmtime(src):
        subprocess.run(["cc", "-O3", "-shared", "-fPIC", "-o", lib, src], check=True)
    render = ctypes.CDLL(lib).render_rows      # ctypes drops the GIL for the call
    render.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    render.restype = None
    return render


def bypass_compositor(xid):
    """Ask mutter to unredirect the fullscreen window, which saves a compositing frame."""
    X11.XOpenDisplay.restype = ctypes.c_void_p
    X11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    X11.XInternAtom.restype = ctypes.c_ulong
    X11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    X11.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    X11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    dpy = X11.XOpenDisplay(None)
    if not dpy:
        return
    on = ctypes.c_long(1)
    atom = X11.XInternAtom(dpy, b"_NET_WM_BYPASS_COMPOSITOR", 0)
    X11.XChangeProperty(dpy, xid, atom, 6, 32, 0, ctypes.byref(on), 1)    # XA_CARDINAL, PropModeReplace
    X11.XCloseDisplay(dpy)


def median_ms(samples):
    return float(np.median(samples)) * 1000 if samples else float("nan")


def nice_ceil(v):
    e = 10 ** math.floor(math.log10(v))
    return next(m * e for m in (1, 2, 5, 10) if m * e >= v)


class EoCamera(threading.Thread):
    """Captures raw Bayer frames, renders RGBA for the display and runs AE/AWB."""

    def __init__(self, dev, fps, appsrc, auto_exposure):
        super().__init__(daemon=True)
        self.dev, self.target_fps, self.appsrc = dev, fps, appsrc
        self.auto_exposure = auto_exposure
        self.stopping = threading.Event()
        self.frames = collections.deque(maxlen=fps * 30)   # (sequence, SOF on CLOCK_MONOTONIC, exposure s)
        self.fps = 0.0
        self.work_ms = 0.0
        self.deliver_ms = 0.0
        self.dropped = 0
        self.error = None
        self.wb = [1.8, 1.0, 1.6]
        self.luts = [make_lut(g) for g in self.wb]
        self.exp = self.gain = None
        self.exp_rng = self.gain_rng = None
        self.tsc_offset = archtimer.tsc_minus_monotonic()
        self.render = load_renderer()
        self.pool = ThreadPoolExecutor(WORKERS)
        edges = [round(EO_H * i / WORKERS / 2) * 2 for i in range(WORKERS + 1)]
        self.bands = list(zip(edges, edges[1:]))       # even row spans, one per worker

    def run(self):
        try:
            self._stream()
        except OSError as e:
            self.error = f"EO capture: {e}"
            log(self.error)

    def _open(self):
        """Stream into cached USERPTR buffers if the VI takes them, else its uncached MMAP ones."""
        for userptr in (True, False):
            cap = Capture(self.dev)
            cap.set_ctrls(sensor_mode=EO_MODE, bypass_mode=0)  # S_FMT picks the mode from this
            cap.set_format(EO_W, EO_H, "BG10")
            try:
                cap.start(userptr=userptr)
                return cap
            except OSError as e:
                cap.close()
                if not userptr:
                    raise
                log(f"EO: USERPTR refused ({e}); copying out of the driver's uncached buffers")

    def _stream(self):
        cap = self.cap = self._open()
        out = np.empty((OUT_H, OUT_W, 4), np.uint8)
        # Uncached driver buffers are read once, sequentially, into this cached copy.
        stage = np.empty((EO_H, EO_W), np.uint16) if cap.memory != MEMORY_USERPTR else None
        n, last_sof, t_sync = 0, None, 0.0
        try:
            while not self.stopping.is_set():
                f = cap.dequeue(1.0)
                if f is None:
                    log("EO: no frame for 1 s")
                    continue
                index, seq, sof = f
                t_dq = time.monotonic()
                if n == 0:
                    self._first_frame(cap)
                n += 1
                now = time.monotonic()
                if now - t_sync > 1.0:      # NTP slews CLOCK_MONOTONIC against the TSC
                    self.tsc_offset, t_sync = archtimer.tsc_minus_monotonic(), now
                t_work = time.perf_counter()
                sof -= self.tsc_offset
                if last_sof is not None:
                    # The VI skips frames without bumping the sequence when no buffer is queued.
                    self.dropped += max(0, round((sof - last_sof) * self.target_fps) - 1)
                last_sof = sof
                exp = (self.exp or 0) * 1e-6
                self.frames.append((seq, sof, exp))
                self.deliver_ms = 0.95 * self.deliver_ms + 0.05 * (t_dq - sof) * 1000

                src, luts = cap.frame16(index), self.luts
                list(self.pool.map(lambda b: self._render_band(src, stage, out, luts, *b), self.bands))
                if n % AE_EVERY == 0:
                    self._update_ae_awb(cap, src if stage is None else stage)
                del src
                cap.queue(index)
                buf = Gst.Buffer.new_wrapped(out.tobytes())
                # Stamped with the mid-exposure instant, so the display sink can measure latency.
                mid = sof + EO_READOUT / 2 - exp / 2
                buf.pts = max(0, round(mid * 1e9) - self.appsrc.get_base_time())
                self.appsrc.emit("push-buffer", buf)
                self.work_ms = 0.95 * self.work_ms + 0.05 * (time.perf_counter() - t_work) * 1000

                if n % self.target_fps == 0:
                    sofs = [fr[1] for fr in list(self.frames)[-self.target_fps:]]
                    self.fps = (len(sofs) - 1) / (sofs[-1] - sofs[0])
        finally:
            cap.close()
            self.pool.shutdown(wait=False)

    def _render_band(self, src, stage, out, luts, r0, r1):
        rows = src[r0:r1]
        if stage is not None:
            np.copyto(stage[r0:r1], rows)
            rows = stage[r0:r1]
        self.render(rows.ctypes.data, rows.strides[0] // 2, out[r0 // 2].ctypes.data, OUT_W, (r1 - r0) // 2,
                    *(lut.ctypes.data for lut in luts))

    def _first_frame(self, cap):
        # The frame rate only sticks once streaming (set_mode loads the mode default),
        # and V4L2 skips s_ctrl when the value is unchanged from the last stream, so
        # step off the target first.
        cap.set_ctrls(frame_rate=self.target_fps * 1000000 + 1)
        cap.set_ctrls(frame_rate=self.target_fps * 1000000)
        self.exp_rng, self.gain_rng = cap.ctrl_range("exposure"), cap.ctrl_range("gain")
        self.exp = self.exp_rng[1]
        self.gain = min(max(64, self.gain_rng[0]), self.gain_rng[1])
        cap.set_ctrls(exposure=self.exp, gain=self.gain)
        log(f"EO streaming {EO_W}x{EO_H} @ {self.target_fps} fps into "
            f"{'USERPTR' if cap.memory == MEMORY_USERPTR else 'MMAP'} buffers; "
            f"exposure {self.exp_rng} us, gain {self.gain_rng}; TSC - monotonic {self.tsc_offset:.6f} s")

    def _update_ae_awb(self, cap, raw):
        # Sparse samples keep this well under a millisecond.
        b = (raw[0::16, 0::16] >> 6).astype(np.float32) - BLACK
        g = (raw[0::16, 1::16] >> 6).astype(np.float32) - BLACK
        r = (raw[1::16, 1::16] >> 6).astype(np.float32) - BLACK
        mr, mg, mb = (max(float(p.mean()), 0.5) for p in (r, g, b))

        # Gray world, smoothed so the colour doesn't pump with scene changes.
        target = [min(max(mg / mr, 0.5), 4.0), 1.0, min(max(mg / mb, 0.5), 4.0)]
        new = [0.8 * w + 0.2 * t for w, t in zip(self.wb, target)]
        if any(abs(n / w - 1) > 0.01 for n, w in zip(new, self.wb)):
            self.wb = new
            self.luts = [make_lut(w) for w in new]

        if not self.auto_exposure:
            return
        mean = mg / (FULL - BLACK)
        ratio = min(max((AE_TARGET / max(mean, 1e-4)) ** 0.5, 0.5), 2.0)
        if float((g > 0.98 * (FULL - BLACK)).mean()) > 0.03:
            ratio = min(ratio, 0.8)     # back off from clipped highlights
        if abs(math.log(ratio)) < 0.04:
            return
        (emin, emax), (gmin, gmax) = self.exp_rng, self.gain_rng
        ev = min(max(self.exp * self.gain * ratio, emin * gmin), emax * gmax)
        exp = int(min(max(ev / gmin, emin), emax))      # exposure first, then gain
        gain = int(min(max(round(ev / exp), gmin), gmax))
        if (exp, gain) != (self.exp, self.gain):
            cap.set_ctrls(exposure=exp, gain=gain)
            self.exp, self.gain = exp, gain


class FrameSync:
    """The Hadron sync line: the Boson drives it as ext sync master, the IMU and the Jetson timestamp it."""

    def __init__(self, line_name):
        self.line = self.cam = self.imu = None
        self.error = None
        try:
            # Held as an input, so the Jetson can't drive the line even under an overlay
            # that leaves its MCLK pad an output.
            self.line = SyncLine(line_name, history=SYNC_HISTORY)
            self.line.start()
        except OSError as e:
            self.error = f"sync line {line_name}: {e}"
            return
        try:
            self.cam = boson.Boson()
            self.cam.set_ext_sync(boson.EXT_SYNC_MASTER)
        except OSError as e:
            self.error = f"Boson as sync master: {e}"
            self.cam = None

    def close(self):
        if self.cam:
            try:
                self.cam.set_ext_sync(boson.EXT_SYNC_DISABLED)     # its power-on setting
            except OSError as e:
                log(f"Boson ext sync not reset: {e}")
            self.cam.close()
            self.cam = None
        if self.line:
            self.line.close()
            self.line = None

    def pulses(self):
        """Pulse times on CLOCK_MONOTONIC: the IMU's tags once they are fitted to the Jetson's edges."""
        if self.imu.synced or not (self.line and self.line.edges):
            return self.imu.pulse_times()
        return np.array(list(self.line.edges), np.float64)

    @staticmethod
    def match(ts, pulses):
        """For each IR frame timestamp, the pulse it follows (NaN if that pulse is missing)."""
        ts = np.asarray(ts, np.float64)
        if not len(pulses) or not len(ts):
            return np.full(len(ts), np.nan)
        j = np.searchsorted(pulses, ts) - 1
        p = np.where(j >= 0, pulses[np.maximum(j, 0)], np.nan)
        return np.where(ts - p < 1 / IR_CORE_FPS, p, np.nan)

    def status(self):
        """One line for the panel, and whether it is a problem."""
        if self.error:
            return f"FSYNC off: {self.error}", True
        edges, tags = len(self.line.edges), len(self.imu.tags)
        if not edges and not tags:
            return "FSYNC: no pulses on Hadron pin 43 yet", True
        if not tags:
            return "FSYNC: the Jetson sees pulses, the IMU tags none (R12 fitted?)", True
        if not edges:
            return "FSYNC: IMU tags pulses, the Jetson can't read pin 43 (overlay?)", True
        if not self.imu.synced:
            return "FSYNC: fitting the IMU clock to the pulses", False
        n, std, ppm = self.imu.sync_fit
        return f"FSYNC  IMU timed to {std * 1e6:.0f} us rms by {n} Boson pulses, clock {ppm:+.0f} ppm", False


class SyncPanel:
    """IMU traces plus the frame/IMU timing, all on CLOCK_MONOTONIC."""

    STRIP_S = 4.0       # seconds of IMU history
    TIMELINE_S = 0.16   # zoomed frame timing window
    PHASE_S = 20.0      # history of the per-frame phase
    LAG_S = 0.05        # draw slightly behind now so every source has delivered

    def __init__(self, imu, eo, ir_frames, latency, imu_desc, sync):
        self.imu, self.eo, self.ir_frames, self.imu_desc = imu, eo, ir_frames, imu_desc
        self.latency, self.sync = latency, sync
        self.pulses = None          # sync pulses on CLOCK_MONOTONIC, taken once per draw
        self.draw_ms = 0.0

    def draw(self, widget, cr):
        t_draw = time.perf_counter()
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        s = h / 1080
        cr.set_source_rgb(*COLOR["bg"])
        cr.paint()
        cr.select_font_face("DejaVu Sans")
        now = time.monotonic()
        t_end = now - self.LAG_S
        x, cw = 20 * s, w - 40 * s
        self.pulses = self.sync.pulses() if self.sync else None

        imu = self.imu
        rows = imu.snapshot(self.STRIP_S + 1)
        t_imu = imu.times(rows[:, 0]) if len(rows) else np.zeros(0)

        self._text(cr, x, 34 * s, 24 * s, "IMU", COLOR["fg"], bold=True)
        self._text(cr, x + 62 * s, 34 * s, 17 * s, self.imu_desc, COLOR["dim"])
        status = imu.error or f"{1 / imu.model[1]:.2f} Hz measured   {imu.temp_c:.1f} °C"
        self._text(cr, x, 62 * s, 15 * s, status, COLOR["warn"] if imu.error else COLOR["dim"])

        self._strip(cr, x, 85 * s, cw, 235 * s, s, "Gyro  deg/s", t_imu, rows[:, 4:7], t_end, 5)
        self._strip(cr, x, 335 * s, cw, 235 * s, s, "Accel  m/s²", t_imu, rows[:, 1:4], t_end, 2)
        self._timeline(cr, x, 600 * s, cw, 230 * s, s, t_end, t_imu)
        self._phase(cr, x, 860 * s, cw, 118 * s, s, t_end)
        self._stats(cr, x, 1000 * s, s)
        self.draw_ms = 0.9 * self.draw_ms + 0.1 * (time.perf_counter() - t_draw) * 1000

    @staticmethod
    def _text(cr, x, y, size, text, color, bold=False):
        cr.select_font_face("DejaVu Sans", 0, 1 if bold else 0)
        cr.set_font_size(size)
        cr.set_source_rgb(*color)
        cr.move_to(x, y)
        cr.show_text(text)

    @staticmethod
    def _text_w(cr, size, text):
        cr.set_font_size(size)
        return cr.text_extents(text).x_advance

    def _strip(self, cr, x, y, w, h, s, title, t, vals, t_end, floor):
        self._text(cr, x, y + 17 * s, 17 * s, title, COLOR["fg"])
        lx = x + w
        for name, key in (("z", "z"), ("y", "y"), ("x", "x")):
            lx -= 34 * s
            self._text(cr, lx, y + 17 * s, 15 * s, name, COLOR[key], bold=True)
        top, ph = y + 28 * s, h - 28 * s
        ax, aw = x + 50 * s, w - 50 * s
        mask = t >= t_end - self.STRIP_S
        rng = nice_ceil(max(floor, float(np.abs(vals[mask]).max()) * 1.1 if mask.any() else floor))
        cr.set_line_width(1)
        for frac, label in ((-1, -rng), (-0.5, None), (0, 0), (0.5, None), (1, rng)):
            py = top + ph / 2 - frac * ph / 2
            cr.set_source_rgb(*COLOR["grid"])
            cr.move_to(ax, py)
            cr.line_to(ax + aw, py)
            cr.stroke()
            if label is not None:
                txt = f"{label:g}"
                self._text(cr, ax - 8 * s - self._text_w(cr, 13 * s, txt), py + 5 * s, 13 * s, txt, COLOR["dim"])
        if not mask.any():
            return
        step = max(1, int(mask.sum() // (aw / (2 * s))))
        tm, vm = t[mask][::step], vals[mask][::step]
        px = ax + (tm - (t_end - self.STRIP_S)) / self.STRIP_S * aw
        cr.save()
        cr.rectangle(ax, top, aw, ph)
        cr.clip()
        cr.set_line_width(1.4 * s)
        for i, key in enumerate(("x", "y", "z")):
            py = top + ph / 2 - vm[:, i] / rng * ph / 2
            cr.set_source_rgb(*COLOR[key])
            cr.move_to(px[0], py[0])
            for a, b in zip(px[1:], py[1:]):
                cr.line_to(a, b)
            cr.stroke()
        cr.restore()

    def _timeline(self, cr, x, y, w, h, s, t_end, t_imu):
        span = self.TIMELINE_S
        t0 = t_end - span
        self._text(cr, x, y + 17 * s, 17 * s, f"Frame timing   last {span * 1000:.0f} ms", COLOR["fg"])
        if self.sync:
            legend = "IR  thin: sync pulse  thick: frame"
            self._text(cr, x + w - self._text_w(cr, 12 * s, legend), y + 17 * s, 12 * s, legend, COLOR["dim"])
        ax, aw = x + 50 * s, w - 50 * s
        eo_top, eo_h = y + 34 * s, 92 * s
        ir_y, imu_y, lane_h = y + 140 * s, y + 172 * s, 22 * s
        axis_y = y + 212 * s

        def tx(t):
            return ax + (t - t0) / span * aw

        for label, ly, key in (("EO", eo_top + eo_h / 2, "eo"), ("IR", ir_y + lane_h / 2, "ir"),
                               ("IMU", imu_y + lane_h / 2, "imu")):
            self._text(cr, x, ly + 5 * s, 14 * s, label, COLOR[key], bold=True)
        cr.save()
        cr.rectangle(ax, y + 26 * s, aw, h - 26 * s)
        cr.clip()

        # IMU sample instants, carried up through the EO lane to show which rows they hit.
        ticks = [tx(t) for t in t_imu[(t_imu >= t0) & (t_imu <= t_end)]]
        cr.set_line_width(1)
        for a, b, alpha in ((eo_top, imu_y, 0.22), (imu_y, imu_y + lane_h, 1.0)):
            for px in ticks:
                cr.move_to(px, a)
                cr.line_to(px, b)
            cr.set_source_rgba(*COLOR["imu"], alpha)
            cr.stroke()

        # EO rolling shutter: row 0 exposes [SOF - exp, SOF], the last row EO_READOUT later.
        for _, sof, exp in list(self.eo.frames)[-12:]:
            if sof + EO_READOUT < t0 or sof - exp > t_end:
                continue
            cr.move_to(tx(sof - exp), eo_top)
            cr.line_to(tx(sof), eo_top)
            cr.line_to(tx(sof + EO_READOUT), eo_top + eo_h)
            cr.line_to(tx(sof + EO_READOUT - exp), eo_top + eo_h)
            cr.close_path()
            cr.set_source_rgba(*COLOR["eo"], 0.35)
            cr.fill_preserve()
            cr.set_source_rgb(*COLOR["eo"])
            cr.set_line_width(1.2 * s)
            cr.stroke()
            mid = sof + EO_READOUT / 2 - exp / 2
            cr.set_source_rgb(*COLOR["fg"])
            cr.arc(tx(mid), eo_top + eo_h / 2, 3.5 * s, 0, 2 * math.pi)
            cr.fill()

        # IR: without sync, each delivered frame at its UVC timestamp (first USB packet);
        # with it, every 60 Hz pulse thin and the pulse each delivered frame follows thick.
        ir_ts = list(self.ir_frames)[-20:]
        frames = ir_ts
        if self.sync:
            pulses = self.pulses
            cr.set_line_width(1.2 * s)
            cr.set_source_rgba(*COLOR["ir"], 0.85)
            for t in pulses[(pulses >= t0) & (pulses <= t_end)]:
                cr.move_to(tx(t), ir_y + lane_h * 0.35)
                cr.line_to(tx(t), ir_y + lane_h)
            cr.stroke()
            matched = FrameSync.match(ir_ts, pulses)
            frames = [p if not math.isnan(p) else t for p, t in zip(matched, ir_ts)]
        cr.set_line_width(2.5 * s)
        cr.set_source_rgb(*COLOR["ir"])
        for t in frames:
            if t0 <= t <= t_end:
                cr.move_to(tx(t), ir_y)
                cr.line_to(tx(t), ir_y + lane_h)
                cr.stroke()
        cr.restore()

        cr.set_source_rgb(*COLOR["grid"])
        cr.set_line_width(1)
        cr.move_to(ax, axis_y)
        cr.line_to(ax + aw, axis_y)
        cr.stroke()
        for ms in range(0, int(span * 1000) + 1, 20):
            px = ax + aw - ms / 1000 / span * aw
            cr.set_source_rgb(*COLOR["grid"])
            cr.move_to(px, axis_y)
            cr.line_to(px, axis_y + 5 * s)
            cr.stroke()
            txt = f"-{ms}" if ms else "0 ms"
            self._text(cr, px - self._text_w(cr, 12 * s, txt) / 2, axis_y + 18 * s, 12 * s, txt, COLOR["dim"])

    def _phase(self, cr, x, y, w, h, s, t_end):
        T = self.imu.model[1]
        self._text(cr, x, y + 17 * s, 17 * s, "Phase after the last IMU sample", COLOR["fg"])
        lx = x + w
        for key, name in (("ir", "IR sync pulse"), ("eo", "EO mid-exposure"))[0 if self.sync else 1:]:
            lx -= self._text_w(cr, 13 * s, name)
            self._text(cr, lx, y + 17 * s, 13 * s, name, COLOR[key])
            lx -= 14 * s
        top, ph = y + 28 * s, h - 28 * s
        ax, aw = x + 50 * s, w - 50 * s
        for frac, label in ((0, "0"), (0.5, None), (1, f"{T * 1000:.1f} ms")):
            py = top + ph - frac * ph
            cr.set_source_rgb(*COLOR["grid"])
            cr.set_line_width(1)
            cr.move_to(ax, py)
            cr.line_to(ax + aw, py)
            cr.stroke()
            if label:
                self._text(cr, ax - 8 * s - self._text_w(cr, 12 * s, label), py + 4 * s, 12 * s, label, COLOR["dim"])
        if not self.imu.samples:
            return
        t0, t_start = self.imu.model[0], t_end - self.PHASE_S
        series = []
        if self.eo.frames:
            frames = np.array([(sof, exp) for _, sof, exp in list(self.eo.frames)])
            frames = frames[frames[:, 0] >= t_start]
            series.append(("eo", frames[:, 0] + EO_READOUT / 2 - frames[:, 1] / 2))
        if self.sync:
            ir = np.array(list(self.ir_frames), np.float64)
            pulses = FrameSync.match(ir[ir >= t_start], self.pulses)
            series.append(("ir", pulses[~np.isnan(pulses)]))
        d = 3.5 * s
        for key, t in series:
            phase = np.mod(t - t0, T) / T
            for a, p in zip(ax + (t - t_start) / self.PHASE_S * aw, top + ph - phase * ph):
                cr.rectangle(a - d / 2, p - d / 2, d, d)
            cr.set_source_rgb(*COLOR[key])
            cr.fill()

    def _stats(self, cr, x, y, s):
        eo, T = self.eo, self.imu.model[1]
        ir = list(self.ir_frames)[-61:]
        ir_fps = (len(ir) - 1) / (ir[-1] - ir[0]) if len(ir) > 2 and ir[-1] > ir[0] else 0.0
        line1 = f"EO {eo.fps:.3f} fps"
        if eo.exp:
            line1 += f"  {eo.exp / 1000:.2f} ms  x{eo.gain / 16:.1f}"
        if eo.dropped:
            line1 += f"  ({eo.dropped} dropped)"
        self._text(cr, x, y, 15 * s, line1 + f"     IR {ir_fps:.2f} fps", COLOR["fg"])
        lat = {k: median_ms(list(v)[-30:]) for k, v in self.latency.items()}
        ir_from = "USB start"
        if self.sync:
            recent = np.array(list(self.ir_frames), np.float64)[-31:-1]     # the newest may not be tagged yet
            lag = recent - FrameSync.match(recent, self.pulses)
            if np.isfinite(lag).any():
                lat["ir"] += float(np.nanmedian(lag)) * 1000
                ir_from = "sync pulse"
        self._text(cr, x, y + 22 * s, 15 * s,
                   f"Latency  EO {lat['eo']:.1f} ms from mid-exposure,  IR {lat['ir']:.1f} ms from {ir_from}",
                   COLOR["fg"])
        if eo.fps > 0:
            ratio = 1 / eo.fps / T
            frac = ratio - round(ratio)
            slip = f"slips 1 sample every {1 / eo.fps / abs(frac):.2f} s" if abs(frac) > 1e-6 else "locked"
            self._text(cr, x, y + 44 * s, 15 * s, f"{ratio:.3f} IMU samples per EO frame, {slip}", COLOR["fg"])
        if eo.error:
            self._text(cr, x, y + 66 * s, 14 * s, eo.error, COLOR["warn"])
        elif self.sync:
            text, problem = self.sync.status()
            self._text(cr, x, y + 66 * s, 14 * s, text, COLOR["warn"] if problem else COLOR["dim"])
        else:
            self._text(cr, x, y + 66 * s, 14 * s,
                       "FSYNC off (--no-sync): IMU sample times from host reads", COLOR["dim"])


class Viewer:
    def __init__(self, args):
        self.args = args
        self.eo = None
        self.quitting = False
        self.ir_frames = collections.deque(maxlen=IR_FPS * 10)
        self.ir_lag = []
        # Seconds from capture to the display sink: EO from mid-exposure, IR from its UVC timestamp.
        self.latency = {"eo": collections.deque(maxlen=300), "ir": collections.deque(maxlen=300)}
        self.win = Gtk.Window(title="Hadron EO + IR")
        self.win.connect("key-press-event", self._on_key)
        self.win.connect("button-press-event", self._on_button)
        self.win.connect("delete-event", lambda *a: self.quit("window closed"))
        self.win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        geo, scale = mon.get_geometry(), mon.get_scale_factor()
        self.sw, self.sh = geo.width * scale, geo.height * scale
        self._layout()

        css = Gtk.CssProvider()
        css.load_from_data(b"window { background-color: black; }")
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        video = Gtk.Fixed()
        video.set_size_request(self.video_w // scale, -1)
        # Each camera renders straight into its own X window through its own sink, so
        # neither waits on the other (a compositor element holds frames to pair them).
        areas = {}
        for name, (x, y, w, h) in (("eosink", self.eo_rect), ("irsink", self.ir_rect)):
            area = areas[name] = Gtk.DrawingArea()
            area.set_size_request(w // scale, h // scale)
            area.set_double_buffered(False)         # the sink owns these pixels
            area.connect("draw", lambda *a: True)
            video.put(area, x // scale, y // scale)
        self.panel_area = Gtk.DrawingArea()
        self.panel_area.set_size_request(self.panel_w // scale, -1)
        box.pack_start(video, False, False, 0)
        box.pack_start(self.panel_area, True, True, 0)
        self.win.add(box)
        self.win.move(geo.x, geo.y)
        self.win.set_default_size(geo.width, geo.height)
        self.win.fullscreen()
        self.win.show_all()
        self.win.get_window().set_cursor(Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR))
        bypass_compositor(self.win.get_window().get_xid())
        self.xids = {}
        for name, area in areas.items():
            area.get_window().ensure_native()
            self.xids[name] = area.get_window().get_xid()

        self.sync = FrameSync(args.sync_line) if args.sync else None
        if self.sync and self.sync.error:
            log(self.sync.error)
        self.imu = Icm45686(bus=args.imu_bus, addr=args.imu_addr, odr=args.imu_odr, fsync=self.sync is not None,
                            pulses=self.sync.line.edges if self.sync and self.sync.line else None,
                            tag_history=SYNC_HISTORY)
        if self.sync:
            self.sync.imu = self.imu
        # One pipeline per camera: in a shared one, the EO stream starting up (latency
        # recalculation, sink setup) could leave the running IR branch stuck flushing.
        self.pipelines = {key: Gst.parse_launch(desc) for key, desc in self._pipeline_descs().items()}
        for key, pipeline in self.pipelines.items():
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_bus)
            # nv3dsink crashes if handed a window before it asks for one.
            bus.enable_sync_message_emission()
            bus.connect("sync-message::element", self._on_sync_message)
            pipeline.get_by_name(f"{key}sink").get_static_pad("sink").add_probe(
                Gst.PadProbeType.BUFFER, self._sink_probe, self.latency[key])
        self.pipelines["ir"].get_by_name("ir").get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._ir_probe)

    def _layout(self):
        """EO and IR at full height side by side, the IMU panel in what's left."""
        a_eo, a_ir = OUT_W / OUT_H, IR_W / IR_H
        self.panel_w = self.sw - int(self.sh * (a_eo + a_ir))
        if self.panel_w < 480:
            self.panel_w = max(480, self.sw // 4)
        self.video_w = self.sw - self.panel_w
        vh = min(self.sh, self.video_w / (a_eo + a_ir))
        eh, ew, iw = int(vh) & ~1, int(vh * a_eo) & ~1, int(vh * a_ir) & ~1
        x0, y0 = (self.video_w - ew - iw) // 2, (self.sh - eh) // 2
        self.eo_rect, self.ir_rect = (x0, y0, ew, eh), (x0 + ew, y0, iw, eh)
        log(f"screen {self.sw}x{self.sh}: EO {self.eo_rect}, IR {self.ir_rect}, panel {self.panel_w} px")

    def _pipeline_descs(self):
        a = self.args
        # The sinks render on arrival; a frame that comes in while the last one is still
        # waiting for vsync replaces it rather than queueing behind it.
        return {
            "eo": f"appsrc name=eo is-live=true format=time block=false max-buffers=1 leaky-type=downstream "
                  f"caps=video/x-raw,format=RGBA,width={OUT_W},height={OUT_H},framerate={a.eo_fps}/1 "
                  f"! nvvidconv ! video/x-raw(memory:NVMM),format=RGBA "
                  f"! nv3dsink name=eosink sync=false enable-last-sample=false",
            "ir": f"v4l2src name=ir device={a.ir} "
                  f"! video/x-raw,format=I420,width={IR_W},height={IR_H},framerate={IR_FPS}/1 "
                  f"! queue leaky=downstream max-size-buffers=1 "
                  f"! nvvidconv ! video/x-raw(memory:NVMM),format=RGBA "
                  f"! nv3dsink name=irsink sync=false enable-last-sample=false",
        }

    def _ir_probe(self, pad, info):
        buf = info.get_buffer()
        if buf.pts != Gst.CLOCK_TIME_NONE:
            # The pipeline runs on the monotonic system clock, so base time + PTS is
            # the UVC timestamp (first USB packet of the frame) on CLOCK_MONOTONIC.
            t = (pad.get_parent_element().get_base_time() + buf.pts) * 1e-9
            self.ir_frames.append(t)
            if len(self.ir_lag) < 120:
                self.ir_lag.append(time.monotonic() - t)
                if len(self.ir_lag) == 120:
                    log(f"IR timestamp to probe: median {np.median(self.ir_lag) * 1000:.2f} ms")
        return Gst.PadProbeReturn.OK

    @staticmethod
    def _sink_probe(pad, info, samples):
        buf = info.get_buffer()
        if buf.pts != Gst.CLOCK_TIME_NONE:
            samples.append(time.monotonic() - (pad.get_parent_element().get_base_time() + buf.pts) * 1e-9)
        return Gst.PadProbeReturn.OK

    def start(self):
        # Built before streaming starts (it may compile the renderer).
        self.eo = EoCamera(self.args.eo, self.args.eo_fps, self.pipelines["eo"].get_by_name("eo"),
                           not self.args.manual)
        for key, pipeline in self.pipelines.items():
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise SystemExit(f"{key} pipeline failed to start")
        self.eo.start()
        self.imu.start()
        desc = (f"ICM-45686  i2c-{self.args.imu_bus} 0x{self.args.imu_addr:02x}  {self.args.imu_odr} Hz FIFO"
                + ("  FSYNC" if self.sync else ""))
        self.panel = SyncPanel(self.imu, self.eo, self.ir_frames, self.latency, desc, self.sync)
        self.panel_area.connect("draw", self.panel.draw)
        self.ticks = 0
        GLib.timeout_add(50, self._tick)

    def _tick(self):
        self.panel_area.queue_draw()
        self.ticks += 1
        if self.ticks % 200 == 0:
            ir = list(self.ir_frames)
            ir_fps = (len(ir) - 1) / (ir[-1] - ir[0]) if len(ir) > 2 else 0.0
            log(f"EO {self.eo.fps:.2f} fps exp {self.eo.exp} gain {self.eo.gain} dropped {self.eo.dropped} "
                f"SOF to dequeue {self.eo.deliver_ms:.1f} ms work {self.eo.work_ms:.1f} ms; "
                f"latency to sink EO {median_ms(self.latency['eo']):.1f} IR {median_ms(self.latency['ir']):.1f} ms; "
                f"panel {self.panel.draw_ms:.1f} ms; "
                f"IR {ir_fps:.2f} fps; IMU {1 / self.imu.model[1]:.2f} Hz resets {self.imu.resets} "
                f"{self.imu.error or ''}")
            if self.sync:
                ir = np.array(ir[-61:-1])
                lag = ir - FrameSync.match(ir, self.sync.pulses())
                edges = np.array(list(self.sync.line.edges), np.float64) if self.sync.line else np.zeros(0)
                rate = (len(edges) - 1) / (edges[-1] - edges[0]) if len(edges) > 2 else 0.0
                log(f"sync: {self.sync.status()[0]}; Jetson edges {rate:.3f} Hz, IMU tags {len(self.imu.tags)}, "
                    f"IR UVC timestamp - pulse {np.nanmedian(lag) * 1000 if np.isfinite(lag).any() else float('nan'):.2f} ms "
                    f"({np.isnan(lag).sum()}/{len(lag)} unmatched)")
        return True

    def quit(self, reason="signal"):
        if self.quitting:
            return False
        self.quitting = True
        log(f"quitting ({reason})")
        if self.eo:
            self.eo.stopping.set()
            self.eo.join(3)
        self.imu.stopping.set()
        for pipeline in self.pipelines.values():
            pipeline.set_state(Gst.State.NULL)
        if self.sync:
            self.sync.close()
        Gtk.main_quit()
        return False

    def _on_key(self, widget, event):
        if event.keyval in (Gdk.KEY_q, Gdk.KEY_Q, Gdk.KEY_Escape):
            self.quit(f"key {Gdk.keyval_name(event.keyval)}")

    def _on_button(self, widget, event):
        if event.button == 3:
            self.quit("right click")

    def _on_sync_message(self, bus, msg):
        if GstVideo.is_video_overlay_prepare_window_handle_message(msg):
            msg.src.set_window_handle(self.xids[msg.src.get_name()])

    def _on_bus(self, bus, msg):
        if msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            log(f"GStreamer error from {msg.src.get_name()}: {err.message} ({dbg})")
            self.quit("GStreamer error")
        elif msg.type == Gst.MessageType.WARNING:
            log(f"GStreamer warning from {msg.src.get_name()}: {msg.parse_warning()[0].message}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--eo", help="tegra VI node of the OV64B40 (default: found by name)")
    p.add_argument("--ir", help="Boson UVC capture node (default: found by name)")
    p.add_argument("--eo-fps", type=int, default=25, help="25 fps puts a whole number of IMU samples in a frame")
    p.add_argument("--imu-bus", type=int, default=1)
    p.add_argument("--imu-addr", type=lambda v: int(v, 0), default=0x68)
    p.add_argument("--imu-odr", type=int, default=200, choices=(50, 100, 200, 400),
                   help="the bus runs at 100 kHz, which caps the FIFO rate")
    p.add_argument("--manual", action="store_true", help="leave EO exposure/gain alone")
    p.add_argument("--sync-line", default="PP.01",
                   help="GPIO line of the carrier MCLK pad wired to Hadron pin 43 (CAM1: PP.01, CAM0: PP.00)")
    p.add_argument("--no-sync", dest="sync", action="store_false",
                   help="leave the Boson's ext sync alone and time the IMU from host reads")
    args = p.parse_args()
    args.eo = args.eo or find_node("ov64b40")
    args.ir = args.ir or find_node("Boson")
    log(f"EO {args.eo}, IR {args.ir}")

    # The EO thread trades the GIL with the panel drawing many times a frame; the
    # default 5 ms switch interval would add up to that at every handoff.
    sys.setswitchinterval(0.0005)
    Gst.init(None)
    viewer = Viewer(args)
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, viewer.quit)
    with open(PID_FILE, "w") as f:
        f.write(f"{os.getpid()}\n")
    try:
        viewer.start()
        Gtk.main()
    finally:
        if viewer.sync:
            viewer.sync.close()
        os.unlink(PID_FILE)


if __name__ == "__main__":
    main()
