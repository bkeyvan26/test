# -*- coding: utf-8 -*-
"""K1 VMS — Global Activity Bus (Phase 6.4)

Singleton signal bus. هر جای برنامه می‌تواند از آن استفاده کند:

    from ui.activity_bus import ActivityBus
    ActivityBus.instance().show("در حال سوئیچ…")
    ActivityBus.instance().hide()

اگر چند عملیات هم‌زمان باشند، از counter استفاده می‌شود.
"""
from PySide6.QtCore import QObject, Signal


class ActivityBus(QObject):
    changed = Signal(bool, str)   # (is_active, text)

    _instance = None

    def __init__(self):
        super().__init__()
        self._count = 0
        self._texts = []

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = ActivityBus()
        return cls._instance

    # ============================================================
    # Public API
    # ============================================================
    def show(self, text: str = ""):
        """فعال کردن لودینگ + متن (شمارنده را یک می‌کند)"""
        self._count = 1
        self._texts = [text or "در حال انجام…"]
        self._emit()

    def hide(self):
        """خاموش کردن کامل"""
        self._count = 0
        self._texts = []
        self._emit()

    def push(self, text: str = ""):
        """افزایش شمارنده — برای عملیات هم‌زمان"""
        self._count += 1
        self._texts.append(text or "در حال انجام…")
        self._emit()

    def pop(self):
        """کاهش شمارنده"""
        self._count = max(0, self._count - 1)
        if self._texts:
            self._texts.pop()
        self._emit()

    def set_text(self, text: str):
        """فقط متن را عوض کن (اگر active باشد)"""
        if self._count <= 0:
            return
        if self._texts:
            self._texts[-1] = text
        else:
            self._texts = [text]
        self._emit()

    def is_active(self) -> bool:
        return self._count > 0

    # ============================================================
    def _emit(self):
        try:
            if self._count > 0:
                text = self._texts[-1] if self._texts else "در حال انجام…"
                self.changed.emit(True, text)
            else:
                self.changed.emit(False, "")
        except Exception:
            pass