"""ICM-45686 FIFO reader over Linux i2c-dev, with each sample stamped on CLOCK_MONOTONIC.

INT1 is not wired on the ARK Hadron adapter and FSYNC needs R12 (not fitted), so the
FIFO is polled. Sample k is assigned t0 + k*T, with T fitted against host read times and
t0 taken from the tightest read (a sample can only be in the FIFO after it happened).
"""
import collections
import ctypes as C
import fcntl
import os
import threading
import time

import numpy as np

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001

PWR_MGMT0, FIFO_COUNT_0, FIFO_DATA = 0x10, 0x12, 0x14
ACCEL_CONFIG0, GYRO_CONFIG0 = 0x1B, 0x1C
FIFO_CONFIG0, FIFO_CONFIG3, FIFO_CONFIG4 = 0x1D, 0x21, 0x22
WHO_AM_I, REG_MISC2 = 0x72, 0x7F
WHOAMI = 0xE9
ODR_CODE = {800: 6, 400: 7, 200: 8, 100: 9, 50: 10}

# 16-byte FIFO packet (HIRES off), little endian as the device is configured by default.
PACKET = np.dtype([("hdr", "u1"), ("accel", "<i2", 3), ("gyro", "<i2", 3), ("temp", "i1"), ("tmst", "<u2")])
ACCEL_SCALE = 32 * 9.80665 / 32768     # m/s^2 per LSB, +-32 g
GYRO_SCALE = 4000 / 32768              # deg/s per LSB, +-4000 dps
READ_PERIOD = 0.0173                   # incommensurate with the ODR so read phases spread out


class i2c_msg(C.Structure):
    _fields_ = [("addr", C.c_uint16), ("flags", C.c_uint16), ("len", C.c_uint16), ("buf", C.POINTER(C.c_uint8))]


class i2c_rdwr_ioctl_data(C.Structure):
    _fields_ = [("msgs", C.POINTER(i2c_msg)), ("nmsgs", C.c_uint32)]


class I2CDevice:
    def __init__(self, bus, addr):
        self.fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
        self.addr = addr

    def _transfer(self, wr, nread=0):
        wbuf = (C.c_uint8 * len(wr)).from_buffer_copy(bytes(wr))
        rbuf = (C.c_uint8 * max(nread, 1))()
        msgs = (i2c_msg * 2)(i2c_msg(self.addr, 0, len(wr), wbuf),
                             i2c_msg(self.addr, I2C_M_RD, nread, rbuf))
        fcntl.ioctl(self.fd, I2C_RDWR, i2c_rdwr_ioctl_data(msgs, 2 if nread else 1))
        return bytes(rbuf)[:nread]

    def write(self, reg, value):
        self._transfer([reg, value])

    def read(self, reg, n):
        return self._transfer([reg], n)


class Icm45686(threading.Thread):
    def __init__(self, bus=1, addr=0x68, odr=200, history_s=30):
        super().__init__(daemon=True)
        self.dev = I2CDevice(bus, addr)
        self.odr = odr
        self.stopping = threading.Event()
        # (index, ax, ay, az, gx, gy, gz) with accel in m/s^2 and gyro in deg/s
        self.samples = collections.deque(maxlen=odr * history_s)
        self.reads = collections.deque(maxlen=600)     # (index of newest sample in FIFO, host time)
        self.model = (0.0, 1.0 / odr)                  # t0, T: sample k at t0 + k*T
        self.lock = threading.Lock()
        self.temp_c = float("nan")
        self.resets = 0
        self.error = None

    def configure(self):
        self.dev.write(REG_MISC2, 0x02)                 # soft reset
        time.sleep(0.005)
        for _ in range(100):
            if self.dev.read(WHO_AM_I, 1)[0] == WHOAMI and not self.dev.read(REG_MISC2, 1)[0] & 0x02:
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("ICM-45686 did not come out of reset")
        self.dev.write(PWR_MGMT0, 0x0F)                 # gyro and accel low noise
        time.sleep(0.05)
        self.dev.write(GYRO_CONFIG0, ODR_CODE[self.odr])     # +-4000 dps
        self.dev.write(ACCEL_CONFIG0, ODR_CODE[self.odr])    # +-32 g
        self.dev.write(FIFO_CONFIG4, 0x02)              # ODR timestamp in each packet
        self.fifo_reset()

    def fifo_reset(self):
        self.dev.write(FIFO_CONFIG3, 0x00)
        self.dev.write(FIFO_CONFIG0, 0x1F)              # bypass, 8K depth
        self.dev.write(FIFO_CONFIG0, 0x9F)              # stop-on-full, 8K depth
        self.dev.write(FIFO_CONFIG3, 0x07)              # gyro + accel + FIFO enable
        self.next_index = 0
        self.reads.clear()
        with self.lock:
            self.samples.clear()

    def run(self):
        try:
            self.configure()
            next_t = time.monotonic()
            while not self.stopping.is_set():
                self._poll()
                next_t += READ_PERIOD
                time.sleep(max(0.0, next_t - time.monotonic()))
        except OSError as e:
            self.error = f"I2C: {e}"
        except RuntimeError as e:
            self.error = str(e)

    def _poll(self):
        self.dev.read(FIFO_COUNT_0, 2)                  # errata: the first count read can be stale
        c = self.dev.read(FIFO_COUNT_0, 2)
        t_read = time.monotonic()                       # every counted sample happened before this
        n = c[0] | c[1] << 8
        if n == 0:
            return
        if n > 4 * self.odr * READ_PERIOD + 32:         # fell behind; restart cleanly
            self.resets += 1
            self.fifo_reset()
            return
        first = self.next_index
        self.reads.append((first + n - 1, t_read))
        take = min(n, 48)
        pk = np.frombuffer(self.dev.read(FIFO_DATA, take * PACKET.itemsize), PACKET)
        a = pk["accel"].astype(np.float64) * ACCEL_SCALE
        g = pk["gyro"].astype(np.float64) * GYRO_SCALE
        valid = (pk["hdr"] & 0x80) == 0
        with self.lock:
            for i in np.flatnonzero(valid):
                self.samples.append((first + i, *a[i], *g[i]))
        if valid.any():
            self.temp_c = float(pk["temp"][valid][-1]) / 2 + 25
        self.next_index = first + take
        if len(self.reads) < 50 or len(self.reads) % 10 == 0:
            self._fit()

    def _fit(self):
        # Closed-form least squares; np.polyfit's LAPACK call wakes spinning BLAS threads.
        k, t = np.array(self.reads).T
        if len(k) >= 8:
            dk, dt = k - k.mean(), t - t.mean()
            T = float((dk * dt).sum() / (dk * dk).sum())
        else:
            T = 1.0 / self.odr
        self.model = (float(np.min(t - T * k)), T)

    def times(self, index):
        t0, T = self.model
        return t0 + T * np.asarray(index, np.float64)

    def snapshot(self, seconds):
        """Samples from the last `seconds` as an (n, 7) array: index, accel xyz, gyro xyz."""
        n = int(seconds * self.odr) + 1
        with self.lock:
            rows = list(self.samples)[-n:]
        return np.array(rows) if rows else np.zeros((0, 7))
