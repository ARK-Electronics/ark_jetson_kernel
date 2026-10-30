"""Read the ARM generic timer (CNTVCT_EL0), which counts the Tegra TSC that VI timestamps use."""
import ctypes as C
import mmap
import time

# isb; mrs x0, cntvct_el0; ret   /   mrs x0, cntfrq_el0; ret
_CNTVCT = bytes.fromhex("df3f03d5" "40e03bd5" "c0035fd6")
_CNTFRQ = bytes.fromhex("00e03bd5" "c0035fd6")


def _function(code, mem, offset):
    mem[offset:offset + len(code)] = code
    addr = C.addressof(C.c_char.from_buffer(mem, offset))
    C.CDLL("libgcc_s.so.1").__clear_cache(C.c_void_p(addr), C.c_void_p(addr + len(code)))
    return C.CFUNCTYPE(C.c_uint64)(addr)


_mem = mmap.mmap(-1, mmap.PAGESIZE, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
cntvct = _function(_CNTVCT, _mem, 0)
cntfrq = _function(_CNTFRQ, _mem, 64)


def tsc_minus_monotonic():
    """Seconds to subtract from a TSC-based timestamp to land on CLOCK_MONOTONIC."""
    freq = cntfrq()
    best = None
    for _ in range(20):
        a = time.monotonic_ns()
        c = cntvct()
        b = time.monotonic_ns()
        if best is None or b - a < best[0]:
            best = (b - a, c / freq - (a + b) / 2e9)
    return best[1]
