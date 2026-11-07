"""Keep Windows awake for active media work without changing power settings."""
from contextlib import contextmanager
import ctypes
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@contextmanager
def keep_system_awake(*, platform=None, kernel=None):
    platform = sys.platform if platform is None else platform
    call, previous = None, 0
    if platform == 'win32':
        try:
            kernel = kernel or ctypes.windll.kernel32
            call = kernel.SetThreadExecutionState
            call.argtypes = [ctypes.c_uint]
            call.restype = ctypes.c_uint
            previous = int(call(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
        except (AttributeError, OSError, TypeError):
            call = None
    try:
        yield {'idle_sleep_prevented': bool(previous), 'lid_sleep_prevented': False}
    finally:
        if call is not None and previous:
            try:
                call(previous | ES_CONTINUOUS)
            except (AttributeError, OSError, TypeError):
                pass
