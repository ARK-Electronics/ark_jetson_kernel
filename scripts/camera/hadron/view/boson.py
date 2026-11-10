"""Boson control over its USB CDC-ACM serial port, using FLIR's framed command protocol.

Frame: 0x8E, then byte-stuffed [channel 0, sequence, function id, status, payload, CRC-16],
then 0xAE. The CRC is CCITT with init 0x1D0F over the unstuffed bytes after 0x8E. Function
ids are those of FLIR's Boson SDK (also used by flirpy).
"""
import binascii
import glob
import os
import select
import struct
import termios
import time
import tty

EXT_SYNC_DISABLED, EXT_SYNC_MASTER, EXT_SYNC_SLAVE = 0, 1, 2
# Colour palettes by id, FLR_COLORLUT_ID_E in the SDK.
COLOR_LUTS = ("White hot", "Black hot", "Rainbow", "Rainbow HC", "Ironbow", "Lava", "Arctic", "Globow",
              "Graded fire", "Hottest")
_GET_SERIAL, _RUN_FFC, _SET_EXT_SYNC, _GET_EXT_SYNC = 0x00050002, 0x00050007, 0x00050098, 0x00050099
_SET_COLOR_LUT, _GET_COLOR_LUT = 0x000B0003, 0x000B0004


def find_port(vendor="09cb"):
    """The ttyACM node whose USB device has FLIR's vendor id."""
    for node in sorted(glob.glob("/sys/class/tty/ttyACM*")):
        usb_dev = os.path.dirname(os.path.realpath(os.path.join(node, "device")))
        try:
            with open(os.path.join(usb_dev, "idVendor")) as f:
                if f.read().strip() == vendor:
                    return "/dev/" + os.path.basename(node)
        except OSError:
            continue
    return None


def _stuff(data):
    out = bytearray()
    for b in data:
        out += bytes((0x9E, b - 0x0D)) if b in (0x8E, 0x9E, 0xAE) else bytes((b,))
    return bytes(out)


def _unstuff(data):
    out, escaped = bytearray(), False
    for b in data:
        if escaped:
            out.append(b + 0x0D)
            escaped = False
        elif b == 0x9E:
            escaped = True
        else:
            out.append(b)
    return bytes(out)


class Boson:
    def __init__(self, port=None):
        port = port or find_port()
        if port is None:
            raise OSError("no Boson serial port")
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY)
        tty.setraw(self.fd)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        self.seq = 0

    def close(self):
        os.close(self.fd)

    def call(self, function, payload=b"", timeout=1.0):
        self.seq += 1
        body = struct.pack(">BIII", 0, self.seq, function, 0xFFFFFFFF) + payload
        os.write(self.fd, b"\x8e" + _stuff(body + struct.pack(">H", binascii.crc_hqx(body, 0x1D0F))) + b"\xae")
        buf, t_end = bytearray(), time.monotonic() + timeout
        while time.monotonic() < t_end:
            if not select.select([self.fd], [], [], 0.05)[0]:
                continue
            buf += os.read(self.fd, 4096)
            while True:
                start = buf.find(0x8E)
                end = buf.find(0xAE, start + 1) if start >= 0 else -1
                if end < 0:
                    break
                reply = _unstuff(buf[start + 1:end])
                del buf[:end + 1]
                if len(reply) < 15 or binascii.crc_hqx(reply[:-2], 0x1D0F) != struct.unpack(">H", reply[-2:])[0]:
                    raise OSError(f"Boson 0x{function:08x}: bad reply")
                _, seq, func, status = struct.unpack(">BIII", reply[:13])
                if (seq, func) != (self.seq, function):
                    continue            # the late reply to a command that timed out
                if status:
                    raise OSError(f"Boson 0x{function:08x}: status 0x{status:x}")
                return reply[13:-2]
        raise OSError(f"Boson 0x{function:08x}: no reply")

    def serial(self):
        return struct.unpack(">I", self.call(_GET_SERIAL)[:4])[0]

    def ext_sync(self):
        return struct.unpack(">I", self.call(_GET_EXT_SYNC)[:4])[0]

    def set_ext_sync(self, mode):
        """Volatile: the Boson powers up with its saved setting (disabled unless saved)."""
        self.call(_SET_EXT_SYNC, struct.pack(">I", mode))

    def color_lut(self):
        return struct.unpack(">I", self.call(_GET_COLOR_LUT)[:4])[0]

    def set_color_lut(self, lut):
        """The palette of the colourised UVC stream. Volatile, like the sync mode."""
        self.call(_SET_COLOR_LUT, struct.pack(">I", lut))

    def run_ffc(self):
        """Flat-field correction: the shutter closes for a fraction of a second."""
        self.call(_RUN_FFC)
