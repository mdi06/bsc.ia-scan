"""Process-level helpers shared by the worker and API processes."""

import ctypes
import gc
import time


def trim_memory() -> None:
    """gc.collect() and ask glibc to return free heap pages to the OS. A no-op beyond the collect on macOS / musl."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass  # expected on non-glibc platforms (macOS, musl)


def run_memory_guard_loop(interval_sec: float = 300):
    """Every `interval_sec`, run gc.collect() and ask glibc to return free heap pages to the OS
    (limits RSS growth on the small Render instance). Blocks forever; run it in a daemon thread."""
    while True:
        time.sleep(interval_sec)
        trim_memory()
