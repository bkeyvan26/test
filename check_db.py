import sqlite3

conn = sqlite3.connect(r"D:\recorde\k1Motion\data\recordings_index.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

for cam in ["cam_676bf0fe", "cam_72c8973a", "cam_3059f25c"]:
    print(f"\n{'='*60}")
    print(f"Camera: {cam}")
    print('='*60)
    cur.execute("""
        SELECT COUNT(*) as n,
               AVG(duration) as avg_dur,
               MIN(duration) as min_dur,
               MAX(duration) as max_dur,
               SUM(duration) as total_dur
        FROM segments
        WHERE camera_name=? AND date='2026-10-05'
    """, (cam,))
    row = cur.fetchone()
    print(f"  count   = {row['n']}")
    print(f"  avg_dur = {row['avg_dur']}")
    print(f"  min_dur = {row['min_dur']}")
    print(f"  max_dur = {row['max_dur']}")
    print(f"  total   = {row['total_dur']}")

    cur.execute("""
        SELECT file_name, start_sec, end_sec, duration, probe_method
        FROM segments
        WHERE camera_name=? AND date='2026-10-05'
        ORDER BY start_sec LIMIT 5
    """, (cam,))
    print("  نمونه ۵ قطعه اول:")
    for r in cur.fetchall():
        print(f"    {r['file_name']}  start={r['start_sec']}  "
              f"end={r['end_sec']}  dur={r['duration']}  method={r['probe_method']}")

conn.close()