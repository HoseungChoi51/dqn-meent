"""Compatibility import; canonical implementation lives in the framework."""
import sys
from optimization_framework.optimizers import binary as _canonical
if __name__ == "__main__":
    raise SystemExit(_canonical.main())
sys.modules[__name__] = _canonical
