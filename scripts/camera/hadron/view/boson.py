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
_GET_SERIAL, _SET_EXT_SYNC, _GET_EXT_SYNC = 0x00050002, 0x00050098, 0x00050099


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
            start = buf.find(0x8E)
            end = buf.find(0xAE, start + 1) if start >= 0 else -1
            if end < 0:
                continue
            reply = _unstuff(buf[start + 1:end])
            _, seq, func, status = struct.unpack(">BIII", reply[:13])
            if binascii.crc_hqx(reply[:-2], 0x1D0F) != struct.unpack(">H", reply[-2:])[0]:
                raise OSError(f"Boson 0x{function:08x}: bad reply CRC")
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
