"""
Rate limit simples em memória, thread-safe, sem dependência externa.
Não é distribuído — em múltiplas réplicas, cada processo tem seu contador.
Suficiente como primeira barreira contra brute-force de login.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: int) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def check_and_record(self, key: str) -> bool:
        """True se permitido (e registra evento); False se bloqueado."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            dq = self._events.setdefault(key, deque())
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self.max_events:
                return False
            dq.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._events.clear()


# 5 tentativas de login em 15 minutos por IP.
login_limiter = RateLimiter(max_events=5, window_seconds=15 * 60)
