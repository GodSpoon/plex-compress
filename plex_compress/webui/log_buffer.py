"""In-memory ring-buffer log handler for live UI log streaming."""

import logging
from collections import deque
from typing import List, Dict, Optional


class UILogHandler(logging.Handler):
    """Thread-safe ring buffer log handler."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self._buffer: deque = deque(maxlen=capacity)
        self._lock = __import__("threading").Lock()

    def emit(self, record: logging.LogRecord):
        with self._lock:
            self._buffer.append({
                "time": self.format_time(record),
                "level": record.levelname,
                "message": self.format(record),
                "raw": record.getMessage(),
            })

    def format_time(self, record: logging.LogRecord) -> str:
        import time
        return time.strftime("%H:%M:%S", time.localtime(record.created))

    def get_lines(self, limit: int = 100, level: Optional[str] = None) -> List[Dict]:
        with self._lock:
            lines = list(self._buffer)
        if level:
            lines = [l for l in lines if l["level"] == level.upper()]
        if limit is None or limit <= 0:
            limit = 100
        return lines[-limit:]

    def clear(self):
        with self._lock:
            self._buffer.clear()
