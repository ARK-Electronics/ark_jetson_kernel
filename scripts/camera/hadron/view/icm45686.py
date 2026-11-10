"""ICM-45686 FIFO reader over Linux i2c-dev, with each sample stamped on CLOCK_MONOTONIC.

INT1 is not wired on the ARK Hadron adapter, so the FIFO is polled. Sample k is assigned
t0 + k*T. Without FSYNC, T is fitted against host read times and t0 taken from the tightest
read (a sample can only be in the FIFO after it happened), which leaves t0 a few hundred us
late. With FSYNC (R12 fitted, pin 9 on the Hadron sync line), the IMU tags the first sample
after each sync pulse with the pulse-to-sample delay, so each pulse is known on the IMU's
clock to 1 us. Given the same pulses timestamped by the Jetson, a line fitted between the two
puts the samples on CLOCK_MONOTONIC to within the Jetson's interrupt jitter.
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
TMST_WOM_CONFIG, IOC_PAD_SCENARIO_OVRD = 0x23, 0x31
IREG_ADDR_15_8, IREG_DATA = 0x7C, 0x7E
SMC_CONTROL_0 = 0xA258                 # IPREG_TOP1, reached through IREG_ADDR / IREG_DATA
WHO_AM_I, REG_MISC2 = 0x72, 0x7F
WHOAMI = 0xE9
ODR_CODE = {800: 6, 400: 7, 200: 8, 100: 9, 50: 10}

# 16-byte FIFO packet (HIRES off), little endian as the device is configured by default.
PACKET = np.dtype([("hdr", "u1"), ("accel", "<i2", 3), ("gyro", "<i2", 3), ("temp", "i1"), ("tmst", "<u2")])
HDR_EXT, HDR_FSYNC = 0x80, 0x04        # extended header (not a sample), FSYNC tag in tmst
ACCEL_SCALE = 32 * 9.80665 / 32768     # m/s^2 per LSB, +-32 g
GYRO_SCALE = 4000 / 32768              # deg/s per LSB, +-4000 dps
READ_PERIOD = 0.0173                   # incommensurate with the ODR so read phases spread out
SYNC_FIT_S = 10                        # pulses fitted: short enough to follow the IMU clock warming up


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

    def ireg_read(self, reg):
        self._transfer([IREG_ADDR_15_8, reg >> 8, reg & 0xFF])
        return self.read(IREG_DATA, 1)[0]

    def ireg_write(self, reg, value):
        self._transfer([IREG_ADDR_15_8, reg >> 8, reg & 0xFF, value])


def _line(x, y):
    """Least-squares y = a + b*x in closed form; np.polyfit's LAPACK call wakes spinning BLAS threads."""
    xm, ym = x.mean(), y.mean()
    b = float(((x - xm) * (y - ym)).sum() / ((x - xm) ** 2).sum())
    return float(ym - b * xm), b


class Icm45686(threading.Thread):
    def __init__(self, bus=1, addr=0x68, odr=200, history_s=30, fsync=False, pulses=None, tag_history=600):
        super().__init__(daemon=True)
        self.dev = I2CDevice(bus, addr)
        self.odr = odr
        self.fsync = fsync
        self.pulses = pulses                           # Jetson timestamps of the sync pulses, or None
        self.stopping = threading.Event()
        # (index, ax, ay, az, gx, gy, gz) with accel in m/s^2 and gyro in deg/s
        self.samples = collections.deque(maxlen=odr * history_s)
        self.reads = collections.deque(maxlen=600)     # (index of newest sample in FIFO, host time)
        self.tags = collections.deque(maxlen=tag_history)   # sync pulse times on the IMU clock: k/odr - delay
        self.model = (0.0, 1.0 / odr)                  # t0, T: sample k at t0 + k*T
        self.synced = False                            # model fitted to the sync pulses
        self.sync_fit = None                           # (pairs used, residual std s, IMU clock ppm)
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
        if self.fsync:
            self.dev.write(IOC_PAD_SCENARIO_OVRD, 0x05)      # pin 9 = FSYNC (power-on: INT2)
            self.dev.write(TMST_WOM_CONFIG, 0x40)            # timestamp deltas, 1 us resolution
            self.dev.ireg_write(SMC_CONTROL_0, self.dev.ireg_read(SMC_CONTROL_0) | 0x03)  # tmst_en, tmst_fsync_en
        self.dev.write(FIFO_CONFIG4, 0x02)              # timestamp (or FSYNC delay) in each packet
        self.fifo_reset()

    def fifo_reset(self):
        self.dev.write(FIFO_CONFIG3, 0x00)
        self.dev.write(FIFO_CONFIG0, 0x1F)              # bypass, 8K depth
        self.dev.write(FIFO_CONFIG0, 0x9F)              # stop-on-full, 8K depth
        self.dev.write(FIFO_CONFIG3, 0x07)              # gyro + accel + FIFO enable
        self.next_index = 0
        self.reads.clear()
        self.tags.clear()
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
        valid = (pk["hdr"] & HDR_EXT) == 0
        with self.lock:
            for i in np.flatnonzero(valid):
                self.samples.append((first + i, *a[i], *g[i]))
        for i in np.flatnonzero(valid & ((pk["hdr"] & HDR_FSYNC) != 0)):
            # The delay counts the IMU's own microseconds, so the pulse is placed on its clock.
            self.tags.append((first + i) / self.odr - pk["tmst"][i] * 1e-6)
        if valid.any():
            self.temp_c = float(pk["temp"][valid][-1]) / 2 + 25
        self.next_index = first + take
        if len(self.reads) < 50 or len(self.reads) % 10 == 0:
            self._fit()

    def _fit(self):
        k, t = np.array(self.reads).T
        if len(k) >= 8:
            dk, dt = k - k.mean(), t - t.mean()
            T = float((dk * dt).sum() / (dk * dk).sum())
        else:
            T = 1.0 / self.odr
        host = (float(np.min(t - T * k)), T)
        synced = self._fit_sync(host)
        self.model, self.synced = (synced, True) if synced else (host, False)

    def _fit_sync(self, host):
        """Sample times from the FSYNC tags matched to the Jetson's pulse timestamps, or None."""
        if not self.tags or not self.pulses or len(self.tags) < 20 or len(self.pulses) < 20:
            return None
        u = np.array(list(self.tags), np.float64)
        u = u[u >= u[-1] - SYNC_FIT_S]
        pulses = np.array(list(self.pulses), np.float64)
        guess = host[0] + host[1] * self.odr * u
        j = np.clip(np.searchsorted(pulses, guess), 1, len(pulses) - 1)
        j -= ((guess - pulses[j - 1]) < (pulses[j] - guess)).astype(int)
        ok = np.abs(pulses[j] - guess) < 0.004
        if ok.sum() < 20:
            return None
        # Pulse times on the IMU's clock (sample period exactly 1/odr there) against CLOCK_MONOTONIC.
        u, p = u[ok], pulses[j[ok]]
        a, b = _line(u, p)
        r = p - (a + b * u)
        # Interrupt latency makes a few Jetson timestamps late by ~200 us; drop them and refit.
        dev = np.abs(r - np.median(r))
        keep = dev < max(5 * 1.4826 * float(np.median(dev)), 10e-6)
        a, b = _line(u[keep], p[keep])
        r = p[keep] - (a + b * u[keep])
        self.sync_fit = (int(keep.sum()), float(r.std()), (b - 1) * 1e6)
        if r.std() > 100e-6:
            return None
        return a, b / self.odr

    def times(self, index):
        t0, T = self.model
        return t0 + T * np.asarray(index, np.float64)

    def pulse_times(self):
        """The tagged sync pulses on CLOCK_MONOTONIC (by the current model), oldest first."""
        t0, T = self.model
        return t0 + T * self.odr * np.array(list(self.tags), np.float64)

    def snapshot(self, seconds):
        """Samples from the last `seconds` as an (n, 7) array: index, accel xyz, gyro xyz."""
        n = int(seconds * self.odr) + 1
        with self.lock:
            rows = list(self.samples)[-n:]
        return np.array(rows) if rows else np.zeros((0, 7))
