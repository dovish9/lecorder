from __future__ import annotations

import threading
from datetime import datetime
from typing import Any


class JobState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._value: dict[str, Any] = {
            "phase": "idle",
            "message": "강의를 선택하고 녹음하거나 기존 파일을 업로드하세요.",
            "updated_at": None,
        }

    def set(self, phase: str, message: str, **details: Any) -> None:
        with self._lock:
            self._value = {
                "phase": phase,
                "message": message,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                **details,
            }

    def get(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._value)
