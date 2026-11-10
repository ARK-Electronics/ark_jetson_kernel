"""Minimal V4L2 streaming capture over ctypes, keeping each frame's sequence and timestamp."""
import ctypes as C
import fcntl
import mmap
import os
import re
import select

import numpy as np


def _ioc(direction, nr, struct):
    return (direction << 30) | (C.sizeof(struct) << 16) | (ord("V") << 8) | nr


class timeval(C.Structure):
    _fields_ = [("tv_sec", C.c_long), ("tv_usec", C.c_long)]


class v4l2_timecode(C.Structure):
    _fields_ = [("type", C.c_uint32), ("flags", C.c_uint32), ("frames", C.c_uint8), ("seconds", C.c_uint8),
                ("minutes", C.c_uint8), ("hours", C.c_uint8), ("userbits", C.c_uint8 * 4)]


class _buffer_m(C.Union):
    _fields_ = [("offset", C.c_uint32), ("userptr", C.c_ulong), ("planes", C.c_void_p), ("fd", C.c_int32)]


class v4l2_buffer(C.Structure):
    _fields_ = [("index", C.c_uint32), ("type", C.c_uint32), ("bytesused", C.c_uint32), ("flags", C.c_uint32),
                ("field", C.c_uint32), ("timestamp", timeval), ("timecode", v4l2_timecode),
                ("sequence", C.c_uint32), ("memory", C.c_uint32), ("m", _buffer_m), ("length", C.c_uint32),
                ("reserved2", C.c_uint32), ("request_fd", C.c_int32)]


class v4l2_requestbuffers(C.Structure):
    _fields_ = [("count", C.c_uint32), ("type", C.c_uint32), ("memory", C.c_uint32),
                ("capabilities", C.c_uint32), ("flags", C.c_uint8), ("reserved", C.c_uint8 * 3)]


class v4l2_pix_format(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ("width", "height", "pixelformat", "field", "bytesperline", "sizeimage",
                                          "colorspace", "priv", "flags", "ycbcr_enc", "quantization", "xfer_func")]


class _format_u(C.Union):
    _fields_ = [("pix", v4l2_pix_format), ("raw_data", C.c_uint8 * 200), ("_align", C.c_void_p)]


class v4l2_format(C.Structure):
    _fields_ = [("type", C.c_uint32), ("fmt", _format_u)]


class v4l2_ext_control(C.Structure):
    _pack_ = 1
    _fields_ = [("id", C.c_uint32), ("size", C.c_uint32), ("reserved2", C.c_uint32), ("value64", C.c_int64)]


class v4l2_ext_controls(C.Structure):
    _fields_ = [("which", C.c_uint32), ("count", C.c_uint32), ("error_idx", C.c_uint32),
                ("request_fd", C.c_int32), ("reserved", C.c_uint32), ("controls", C.POINTER(v4l2_ext_control))]


class v4l2_query_ext_ctrl(C.Structure):
    _fields_ = [("id", C.c_uint32), ("type", C.c_uint32), ("name", C.c_char * 32), ("minimum", C.c_int64),
                ("maximum", C.c_int64), ("step", C.c_uint64), ("default_value", C.c_int64), ("flags", C.c_uint32),
                ("elem_size", C.c_uint32), ("elems", C.c_uint32), ("nr_of_dims", C.c_uint32),
                ("dims", C.c_uint32 * 4), ("reserved", C.c_uint32 * 32)]


VIDIOC_S_FMT = _ioc(3, 5, v4l2_format)
VIDIOC_REQBUFS = _ioc(3, 8, v4l2_requestbuffers)
VIDIOC_QUERYBUF = _ioc(3, 9, v4l2_buffer)
VIDIOC_QBUF = _ioc(3, 15, v4l2_buffer)
VIDIOC_DQBUF = _ioc(3, 17, v4l2_buffer)
VIDIOC_STREAMON = _ioc(1, 18, C.c_int)
VIDIOC_STREAMOFF = _ioc(1, 19, C.c_int)
VIDIOC_S_EXT_CTRLS = _ioc(3, 72, v4l2_ext_controls)
VIDIOC_QUERY_EXT_CTRL = _ioc(3, 103, v4l2_query_ext_ctrl)

BUF_TYPE_VIDEO_CAPTURE = 1
MEMORY_MMAP = 1
MEMORY_USERPTR = 2
CTRL_FLAG_NEXT_CTRL = 0x80000000
CTRL_TYPE_CTRL_CLASS = 6


def fourcc(code):
    return sum(ord(c) << (8 * i) for i, c in enumerate(code))


class Capture:
    """One capture node: controls by v4l2-ctl name, then MMAP or USERPTR streaming."""

    def __init__(self, dev):
        self.fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        self.ctrl_ids = {}
        q = v4l2_query_ext_ctrl(id=CTRL_FLAG_NEXT_CTRL)
        while True:
            try:
                fcntl.ioctl(self.fd, VIDIOC_QUERY_EXT_CTRL, q)
            except OSError:
                break
            if q.type != CTRL_TYPE_CTRL_CLASS:
                name = re.sub(r"[^a-z0-9]+", "_", q.name.decode().lower()).strip("_")
                self.ctrl_ids[name] = q.id
            q.id |= CTRL_FLAG_NEXT_CTRL
        self.maps = []
        self.ptrs = []
        self.memory = MEMORY_MMAP
        self.pix = None

    def ctrl_range(self, name):
        q = v4l2_query_ext_ctrl(id=self.ctrl_ids[name])
        fcntl.ioctl(self.fd, VIDIOC_QUERY_EXT_CTRL, q)
        return q.minimum, q.maximum

    def set_ctrls(self, **values):
        arr = (v4l2_ext_control * len(values))()
        for c, (name, v) in zip(arr, values.items()):
            c.id, c.value64 = self.ctrl_ids[name], int(v)
        ctrls = v4l2_ext_controls(count=len(values), controls=arr)
        fcntl.ioctl(self.fd, VIDIOC_S_EXT_CTRLS, ctrls)

    def set_format(self, width, height, code):
        f = v4l2_format(type=BUF_TYPE_VIDEO_CAPTURE)
        f.fmt.pix.width, f.fmt.pix.height, f.fmt.pix.pixelformat = width, height, fourcc(code)
        fcntl.ioctl(self.fd, VIDIOC_S_FMT, f)
        self.pix = f.fmt.pix
        return self.pix

    def start(self, count=4, userptr=False):
        """Queue count buffers and stream on.

        Driver (MMAP) buffers are uncached on Tegra; USERPTR buffers are ordinary
        cached pages that the kernel invalidates at DQBUF, so the CPU reads them at
        full speed. Raises OSError if the driver refuses USERPTR.
        """
        self.memory = MEMORY_USERPTR if userptr else MEMORY_MMAP
        req = v4l2_requestbuffers(count=count, type=BUF_TYPE_VIDEO_CAPTURE, memory=self.memory)
        fcntl.ioctl(self.fd, VIDIOC_REQBUFS, req)
        for i in range(req.count):
            if userptr:
                m = mmap.mmap(-1, -(-self.pix.sizeimage // mmap.PAGESIZE) * mmap.PAGESIZE)
                self.ptrs.append(C.c_char.from_buffer(m))
            else:
                b = v4l2_buffer(index=i, type=BUF_TYPE_VIDEO_CAPTURE, memory=MEMORY_MMAP)
                fcntl.ioctl(self.fd, VIDIOC_QUERYBUF, b)
                m = mmap.mmap(self.fd, b.length, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=b.m.offset)
            self.maps.append(m)
            self.queue(i)
        fcntl.ioctl(self.fd, VIDIOC_STREAMON, C.c_int(BUF_TYPE_VIDEO_CAPTURE))

    def queue(self, index):
        b = v4l2_buffer(index=index, type=BUF_TYPE_VIDEO_CAPTURE, memory=self.memory)
        if self.memory == MEMORY_USERPTR:
            b.m.userptr, b.length = C.addressof(self.ptrs[index]), len(self.maps[index])
        fcntl.ioctl(self.fd, VIDIOC_QBUF, b)

    def dequeue(self, timeout):
        """(index, sequence, CLOCK_MONOTONIC seconds), or None on timeout."""
        if not select.select([self.fd], [], [], timeout)[0]:
            return None
        b = v4l2_buffer(type=BUF_TYPE_VIDEO_CAPTURE, memory=self.memory)
        fcntl.ioctl(self.fd, VIDIOC_DQBUF, b)
        return b.index, b.sequence, b.timestamp.tv_sec + b.timestamp.tv_usec * 1e-6

    def frame16(self, index):
        """The buffer as a (height, width) uint16 view; valid until requeued."""
        p = self.pix
        words = np.frombuffer(self.maps[index], np.uint16, count=p.bytesperline // 2 * p.height)
        return words.reshape(p.height, p.bytesperline // 2)[:, :p.width]

    def close(self):
        try:
            fcntl.ioctl(self.fd, VIDIOC_STREAMOFF, C.c_int(BUF_TYPE_VIDEO_CAPTURE))
        except OSError:
            pass
        del self.ptrs[:]
        for m in self.maps:
            try:
                m.close()
            except BufferError:     # a numpy view still exists; the fd close releases it
                pass
        os.close(self.fd)
