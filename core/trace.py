# -*- coding: utf-8 -*-
"""
K1 VMS — Forensic trace helper (MEASUREMENT ONLY)

Usage:
    from core.trace import new_trace
    t = new_trace("PLAY", camera="cam1")
    t.mark("EVENT_NAME", key=value)
    t.end("FINAL_EVENT")

Uses time.monotonic() for durations. Console output only. No side effects.
"""
import time
import threading
from datetime import datetime


# ---- Global kill switch ----
TRACE_ENABLED = True


class TraceContext:
    def __init__(self, trace_id, kind, camera=""):
        self.trace_id = trace_id
        self.kind = kind
        self.camera = camera
        self.t0 = time.monotonic()
        self.events = []
        self._lock = threading.Lock()
        self._ended = False
        self._marks_seen = set()

    def mark(self, event, **ctx):
        if not TRACE_ENABLED:
            return self
        elapsed_ms = (time.monotonic() - self.t0) * 1000.0
        thread_id = threading.get_ident()
        with self._lock:
            self.events.append({
                "event": event,
                "elapsed_ms": elapsed_ms,
                "thread": thread_id,
                "ctx": dict(ctx),
                "wall": datetime.now(),
            })
        ctx_str = ""
        if ctx:
            parts = [f"{k}={v}" for k, v in ctx.items()]
            ctx_str = "  " + "  ".join(parts)
        cam_str = f" camera={self.camera}" if self.camera else ""
        print(f"[{self.kind}][{self.trace_id}][+{elapsed_ms:8.1f}ms]"
              f"[T{thread_id}]{cam_str} {event}{ctx_str}")
        return self

    def mark_once(self, event, **ctx):
        """Record only the first occurrence of event."""
        if not TRACE_ENABLED:
            return self
        with self._lock:
            if event in self._marks_seen:
                return self
            self._marks_seen.add(event)
        return self.mark(event, **ctx)

    def end(self, event="END"):
        if not TRACE_ENABLED:
            return
        with self._lock:
            if self._ended:
                return
            self._ended = True
        self.mark(event)
        if self.events:
            total = self.events[-1]["elapsed_ms"]
            print(f"[{self.kind}][{self.trace_id}] "
                  f"════ TOTAL: {total:.0f}ms ({total/1000:.2f}s) ════")

    def is_ended(self):
        return self._ended

    def summary(self):
        return {
            "trace_id": self.trace_id,
            "kind": self.kind,
            "camera": self.camera,
            "events": list(self.events),
            "total_ms": self.events[-1]["elapsed_ms"] if self.events else 0,
        }


class TraceManager:
    _lock = threading.Lock()
    _counter = 0
    _recent = {}
    _max_recent = 500

    @classmethod
    def new(cls, kind, camera=""):
        with cls._lock:
            cls._counter += 1
            n = cls._counter
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        trace_id = f"{kind}-{ts}-{n:04d}"
        ctx = TraceContext(trace_id, kind, camera)
        with cls._lock:
            cls._recent[trace_id] = ctx
            if len(cls._recent) > cls._max_recent:
                oldest = next(iter(cls._recent))
                cls._recent.pop(oldest, None)
        return ctx

    @classmethod
    def get(cls, trace_id):
        return cls._recent.get(trace_id)


def new_trace(kind, camera=""):
    return TraceManager.new(kind, camera)