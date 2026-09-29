"""Host prerequisites are measured before reserving implementation compute."""
import threading
import time
from optimization_framework.implementations.legacy_runtime import sandbox_status

_lock = threading.Lock()
_saved = None
_checked = 0.0


def implementation_execution():
    global _saved, _checked
    with _lock:
        if _saved is None or time.monotonic() - _checked > 30:
            _saved = sandbox_status()
            _checked = time.monotonic()
        return dict(_saved)
