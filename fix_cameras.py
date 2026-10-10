# -*- coding: utf-8 -*-
"""پاکسازی دوربین‌های orphan با نگه‌داشتن فقط دوربین‌های واقعی"""
import json
from pathlib import Path

DATA = Path(r"D:\recorde\k1Motion\data")
CAMERAS_FILE = DATA / "cameras.json"
NVRS_FILE = DATA / "nvrs.json"

# ★ دوربین‌هایی که واقعاً می‌خواهید نگه دارید (فقط این ۹ تا)
KEEP_NAMES = {
    "حراست", "تست", "فناوری", "تست2", "تست3", "تست4",
    "خروجی طالقانی", "ورودی طالقانی", "آسانسور",
}


def main():
    if not CAMERAS_FILE.exists():
        print(f"❌ {CAMERAS_FILE} نیست")
        return

    data = json.loads(CAMERAS_FILE.read_text(encoding="utf-8"))
    cams = data.get("cameras", [])
    print(f"قبل: {len(cams)} دوربین")

    keep = []
    removed = 0
    for c in cams:
        name = (c.get("name") or "").strip()
        # اگر نام دقیقاً یکی از این ۹ تا بود → نگه دار
        if name in KEEP_NAMES:
            keep.append(c)
            continue
        # هر چیز دیگری → حذف
        removed += 1

    print(f"حذف می‌شود: {removed}")
    print(f"نگه داشته می‌شود: {len(keep)}")
    for c in keep:
        print(f"  ✓ {c.get('name')}")

    # backup
    bak = CAMERAS_FILE.with_suffix(".json.bak_dedup")
    bak.write_text(
        CAMERAS_FILE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    print(f"بکاپ: {bak}")

    # ذخیره
    CAMERAS_FILE.write_text(
        json.dumps({"cameras": keep}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ ذخیره شد — {len(keep)} دوربین")


if __name__ == "__main__":
    main()