"""Compatibility import; persistence is owned by the framework."""
import sys
from optimization_framework.storage import sqlite as _canonical
sys.modules[__name__] = _canonical
