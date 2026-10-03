# -*- coding: utf-8 -*-
"""K1Motion — Auto-detect worker (Phase 6.0)

Runs ONVIF + RTSP probe OFF the GUI thread.
Emits Qt signals; never touches UI directly.
"""
import threading
import traceback

from PySide6.QtCore import QObject, Signal, Slot


class AutoDetectWorker(QObject):
    """
    Signals:
        log(str)              — diagnostic line
        progress(int,int,str) — current/total/message
        finished_ok(dict)     — success, payload:
                                  {
                                    "probe": probe_result,
                                    "onvif_profiles": [...] or None,
                                  }
        finished_fail(str)    — error message
        cancelled()           — cooperative cancel confirmed
    """
    log = Signal(str)
    progress = Signal(int, int, str)
    finished_ok = Signal(dict)
    finished_fail = Signal(str)
    cancelled = Signal()

    def __init__(self, ip, user, password, onvif_port=80, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.user = user
        self.password = password
        self.onvif_port = onvif_port
        self._cancel_event = threading.Event()
        self._finished = False

    # ---- public API ----
    def cancel(self):
        self._cancel_event.set()
        try:
            self.log.emit("[AUTO-DETECT] CANCEL REQUESTED")
        except Exception:
            pass

    @property
    def cancelled_flag(self):
        return self._cancel_event.is_set()

    # ---- main entry (runs on worker thread) ----
    @Slot()
    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            tb = traceback.format_exc()
            try:
                self.log.emit(f"[AUTO-DETECT] EXCEPTION: {e}")
                self.log.emit(tb)
            except Exception:
                pass
            self._emit_fail(str(e))
        finally:
            self._finished = True

    def _run_inner(self):
        self.log.emit(f"[AUTO-DETECT] START ip={self.ip}")

        # ============================================================
        # Step 1: ONVIF (with cancellation)
        # ============================================================
        onvif_profiles = None
        onvif_result = None

        if not self._cancel_event.is_set():
            self.progress.emit(0, 100, "ONVIF discovery...")
            self.log.emit("[AUTO-DETECT] ONVIF START")
            try:
                from core import camera_discovery
                onvif_profiles = camera_discovery.get_onvif_stream_profiles(
                    self.ip, self.user, self.password,
                    port=self.onvif_port,
                )
            except Exception as e:
                self.log.emit(f"[AUTO-DETECT] ONVIF FAILED reason={e}")
                onvif_profiles = None

            if self._cancel_event.is_set():
                self._emit_cancel()
                return

            if onvif_profiles:
                self.log.emit(
                    f"[AUTO-DETECT] ONVIF returned "
                    f"{len(onvif_profiles)} profile(s)"
                )
                main = next(
                    (p for p in onvif_profiles
                     if p.get("url") and p.get("url").strip()),
                    None
                )
                if main:
                    sub = next(
                        (p for p in onvif_profiles
                         if p is not main and p.get("url")),
                        None
                    )
                    onvif_result = {
                        "main_url": (main.get("url") or "").strip(),
                        "sub_url": (sub.get("url") or "").strip() if sub else "",
                        "vendor": main.get("vendor", ""),
                        "model": main.get("model", ""),
                    }
            else:
                self.log.emit(
                    "[AUTO-DETECT] ONVIF unavailable, will use RTSP probe"
                )
        else:
            self._emit_cancel()
            return

        # ============================================================
        # Step 2: RTSP probe
        # ============================================================
        if self._cancel_event.is_set():
            self._emit_cancel()
            return

        self.progress.emit(15, 100, "RTSP probing...")

        try:
            from core.rtsp_probe import probe_device
        except Exception as e:
            self._emit_fail(f"rtsp_probe import failed: {e}")
            return

        def _log_cb(m):
            self.log.emit(m)

        def _prog_cb(cur, total, msg):
            # Map raw progress into [15, 95]
            if total <= 0:
                return
            pct = 15 + int((cur / total) * 80)
            self.progress.emit(pct, 100, msg)

        result = probe_device(
            ip=self.ip,
            user=self.user,
            password=self.password,
            onvif_port=self.onvif_port,
            onvif_result=onvif_result,
            log=_log_cb,
            progress=_prog_cb,
            cancel_event=self._cancel_event,
        )

        if self._cancel_event.is_set():
            self._emit_cancel()
            return

        if not result.get("success"):
            self.progress.emit(100, 100, "failed")
            self._emit_fail(result.get("error", "No RTSP URL matched"))
            return

        self.progress.emit(95, 100, "applying profiles...")

        payload = {
            "probe": result,
            "onvif_profiles": onvif_profiles,
        }
        self.progress.emit(100, 100, "done")
        self.finished_ok.emit(payload)

    # ---- emit helpers (guarded) ----
    def _emit_cancel(self):
        try:
            self.log.emit("[AUTO-DETECT] WORKER FINISHED cancelled=true")
        except Exception:
            pass
        self.cancelled.emit()

    def _emit_fail(self, msg):
        try:
            self.log.emit(f"[AUTO-DETECT] WORKER FINISHED cancelled=false "
                          f"error={msg}")
        except Exception:
            pass
        self.finished_fail.emit(msg)