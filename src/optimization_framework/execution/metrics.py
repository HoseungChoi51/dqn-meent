"""Bounded, file-versioned projections of append-only worker metric journals."""
from collections import OrderedDict
import json
from threading import Lock


class MetricProjectionCache:
    """Keep just requested scalar columns, never candidates or optimizer archives.

    Stat every read, including completed trials: resumed/replaced/truncated logs
    invalidate old projections. A changing file is returned but is not cached.
    The ordinary full-metric reader remains independent of this cache.
    """

    def __init__(self, max_points=100_000, max_files=128):
        self.max_points, self.max_files = max_points, max_files
        self._entries = OrderedDict()
        self._points = 0
        self._lock = Lock()

    @staticmethod
    def _version(path):
        try:
            stat = path.stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        except FileNotFoundError:
            return None

    def read(self, path, fields):
        fields = tuple(dict.fromkeys(fields))
        key, version = (path, fields), self._version(path)
        with self._lock:
            cached = self._entries.pop(key, None)
            if cached is not None:
                if version is not None and cached[0] == version:
                    self._entries[key] = cached
                    return [dict(row) for row in cached[1]]
                self._points -= len(cached[1])
        if version is None:
            return []
        rows = []
        cacheable = True
        with path.open() as stream:
            for line in stream:
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue  # Active writers may leave an incomplete final line.
                row = tuple((field, raw[field]) for field in fields if field in raw)
                # The bound counts scalar points. Larger structures are returned
                # normally but cannot inflate or mutate a shared cached entry.
                cacheable &= all(value is None or type(value) in (bool, int, float) for _, value in row)
                rows.append(row)
        if cacheable and len(rows) <= self.max_points and self._version(path) == version:
            with self._lock:
                previous = self._entries.pop(key, None)
                if previous is not None:
                    self._points -= len(previous[1])
                self._entries[key] = (version, rows)
                self._points += len(rows)
                while self._points > self.max_points or len(self._entries) > self.max_files:
                    _, (_, evicted) = self._entries.popitem(last=False)
                    self._points -= len(evicted)
        return [dict(row) for row in rows]
