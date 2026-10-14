from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class BackgroundServiceState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: Optional[threading.Thread] = None
    stop_event: Optional[threading.Event] = None
    snapshot: Dict[str, Any] = field(default_factory=dict)

    def read(self) -> Dict[str, Any]:
        with self.lock:
            data = dict(self.snapshot)
            data["running"] = bool(self.thread is not None and self.thread.is_alive())
            return data

    def reset(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            self.snapshot = dict(snapshot)
            self.thread = None
            self.stop_event = None
            return dict(self.snapshot)
