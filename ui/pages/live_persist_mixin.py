# -*- coding: utf-8 -*-
"""State persistence mixin for LivePage."""
import json


class LivePersistenceMixin:
    """Save/load layout + assignments to STATE_FILE.
    Requires self.STATE_FILE (Path), self._restoring (bool),
    self.grid, self.cell_to_uid.
    """

    def _load_state_from_disk(self):
        try:
            if self.STATE_FILE.exists():
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"[live] load state: {e}")
        return None

    def _save_state_to_disk(self):
        if self._restoring:
            return
        try:
            layout = self.grid.get_layout_key()
            n = len(self.grid.get_cells())
            assignments = []
            for i in range(n):
                assignments.append(self.cell_to_uid.get(i, "") or "")
            data = {"layout": layout, "assignments": assignments}
            self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[live] save state: {e}")