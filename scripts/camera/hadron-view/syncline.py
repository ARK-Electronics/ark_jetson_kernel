"""Rising-edge timestamps of a GPIO line through the kernel's GPIO character device (v1 ABI).

Holding the line as an input also keeps the Jetson from driving it. Edge timestamps are taken
in the GPIO interrupt on CLOCK_MONOTONIC; a few arrive late by the interrupt latency.
"""
import collections
import ctypes as C
import fcntl
import glob
import os
import select
import struct
import threading


class ChipInfo(C.Structure):
    _fields_ = [("name", C.c_char * 32), ("label", C.c_char * 32), ("lines", C.c_uint32)]


class LineInfo(C.Structure):
    _fields_ = [("line_offset", C.c_uint32), ("flags", C.c_uint32), ("name", C.c_char * 32),
                ("consumer", C.c_char * 32)]


class EventRequest(C.Structure):
    _fields_ = [("lineoffset", C.c_uint32), ("handleflags", C.c_uint32), ("eventflags", C.c_uint32),
                ("consumer_label", C.c_char * 32), ("fd", C.c_int)]


def _iowr(nr, struct_type, read_only=False):
    return (2 if read_only else 3) << 30 | C.sizeof(struct_type) << 16 | 0xB4 << 8 | nr


GET_CHIPINFO = _iowr(0x01, ChipInfo, read_only=True)
GET_LINEINFO = _iowr(0x02, LineInfo)
GET_LINEEVENT = _iowr(0x04, EventRequest)
HANDLE_REQUEST_INPUT = 1 << 0
EVENT_RISING_EDGE = 1 << 0
EVENT = struct.Struct("=QI4x")      # struct gpioevent_data: timestamp ns, id


def find_line(name):
    """(chip path, offset) of the GPIO line with this name, e.g. "PP.01"."""
    for chip in sorted(glob.glob("/dev/gpiochip*")):
        fd = os.open(chip, os.O_RDONLY)
        try:
            info = ChipInfo()
            fcntl.ioctl(fd, GET_CHIPINFO, info)
            for offset in range(info.lines):
                line = LineInfo(line_offset=offset)
                fcntl.ioctl(fd, GET_LINEINFO, line)
                if line.name.decode() == name:
                    return chip, offset
        finally:
            os.close(fd)
    raise OSError(f"no GPIO line named {name}")


class SyncLine(threading.Thread):
    def __init__(self, name, history=600):
        super().__init__(daemon=True)
        chip, offset = find_line(name)
        fd = os.open(chip, os.O_RDONLY)
        try:
            self.req = EventRequest(lineoffset=offset, handleflags=HANDLE_REQUEST_INPUT,
                                    eventflags=EVENT_RISING_EDGE, consumer_label=b"hadron-sync")
            fcntl.ioctl(fd, GET_LINEEVENT, self.req)
        finally:
            os.close(fd)
        self.name = name
        self.edges = collections.deque(maxlen=history)      # CLOCK_MONOTONIC seconds
        self.stopping = threading.Event()

    def run(self):
        while not self.stopping.is_set():
            if not select.select([self.req.fd], [], [], 0.2)[0]:
                continue
            data = os.read(self.req.fd, EVENT.size * 64)
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                self.edges.append(EVENT.unpack_from(data, i)[0] * 1e-9)

    def close(self):
        self.stopping.set()
        self.join(1)
        os.close(self.req.fd)
