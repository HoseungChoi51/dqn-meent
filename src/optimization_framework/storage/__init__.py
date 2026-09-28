"""Persistence adapters. SQLite is canonical; files are verified artifacts."""

from .sqlite import Store

__all__ = ["Store"]
