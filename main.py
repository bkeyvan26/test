# -*- coding: utf-8 -*-
"""K1 VMS — Entry point (Phase 8.2: sync folder names + fast shutdown)"""
import sys
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

import config
from ui import theme
from ui.global_style import get_global_qss
from ui.main_window import MainWindow


def _sync_nvr_folder_names():
    """
    نام پوشه‌ی ضبط NVR را به همه‌ی کانال‌های موجود منتقل می‌کند.
    برای دوربین‌هایی که قبل از افزودن فیلد record_folder_name ساخته شده‌اند.
    """
    try:
        from core.nvr_manager import NVRManager
        from core.camera_manager import CameraManager

        nvr_manager = NVRManager.instance()
        cam_manager = CameraManager()

        # بارگذاری تازه
        try:
            cam_manager.reload()
        except Exception:
            pass

        # فهرست NVRها با record_folder_name غیرخالی
        nvrs_with_folder = [
            n for n in nvr_manager.all()
            if (n.record_folder_name or "").strip()
        ]

        if not nvrs_with_folder:
            print("[K1 VMS] no NVR folder names to sync")
            return 0

        # چک دوربین‌هایی که باید آپدیت شوند
        to_update = []
        for nvr in nvrs_with_folder:
            folder = nvr.record_folder_name.strip()
            for cam in cam_manager.all():
                if cam.nvr_uid != nvr.uid:
                    continue
                current = (getattr(cam, "record_folder_name", "") or "").strip()
                if current != folder:
                    cam.record_folder_name = folder
                    to_update.append(cam)

        if not to_update:
            print(f"[K1 VMS] ✓ {len(nvrs_with_folder)} NVR folder names "
                  f"already synced")
            return 0

        # ذخیره‌ی تغییرات
        for cam in to_update:
            try:
                cam_manager.update(cam.uid, cam)
            except Exception as e:
                print(f"[K1 VMS] sync update failed for {cam.name}: {e}")

        print(f"[K1 VMS] ✓ synced folder_name for {len(to_update)} camera(s)")
        return len(to_update)
    except Exception as e:
        print(f"[K1 VMS] ⚠ sync failed: {e}")
        return 0


def main():
    t_start = time.monotonic()

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont(theme.FONT_FAMILY, 10))
    app.setStyleSheet(get_global_qss())

    print(f"[K1 VMS] v{config.APP_VERSION}")
    print(f"[K1 VMS] Data dir:       {config.DATA_DIR}")
    print(f"[K1 VMS] Recordings dir: {config.RECORDINGS_DIR}")

    # MediaMTX check
    if not config.MEDIAMTX_EXE.exists():
        print(f"[K1 VMS] ⚠ MediaMTX NOT FOUND at {config.MEDIAMTX_EXE}")
        print(f"[K1 VMS]   Recording will not work.")
    else:
        print(f"[K1 VMS] ✓ MediaMTX found at {config.MEDIAMTX_EXE}")

    # Migration یک‌بار — پوشه‌های قدیمی (junction یا cam_xxx)
    try:
        from core.recording_organizer import migrate_old_recordings
        moved = migrate_old_recordings(log_fn=lambda m: print(m))
        if moved:
            print(f"[K1 VMS] migrated {moved} old folder(s)")
    except Exception as e:
        print(f"[K1 VMS] ⚠ migrate failed: {e}")

    # ★ Sync نام پوشه NVR → کانال‌ها
    _sync_nvr_folder_names()

    # ★ Shutdown سریع‌تر: signal handler برای Ctrl+C
    try:
        import signal
        def _sigint_handler(sig, frame):
            print("\n[K1 VMS] Ctrl+C — shutting down fast...")
            app.quit()
        signal.signal(signal.SIGINT, _sigint_handler)
    except Exception:
        pass

    # ★ Enable periodic event loop processing for signals
    try:
        from PySide6.QtCore import QTimer
        _sig_timer = QTimer()
        _sig_timer.timeout.connect(lambda: None)
        _sig_timer.start(200)
    except Exception:
        pass

    window = MainWindow()
    window.show()

    print(f"[K1 VMS] ✓ ready in {time.monotonic()-t_start:.2f}s")

    exit_code = app.exec()

    # ★ Shutdown سریع: بعد از بستن پنجره، فقط منتظر کوتاه می‌مانیم
    print("[K1 VMS] bye")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())