# -*- coding: utf-8 -*-
"""Camera list widget for Live page (drag source)."""
import json
from PySide6.QtWidgets import QListWidget
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtGui import QDrag

from ui.widgets.live_grid import MIME_CAMERA


class CameraListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragOnly)
        self.setSelectionMode(QListWidget.SingleSelection)

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None:
            return
        uid = item.data(Qt.UserRole) or ""
        if not uid:
            return
        md = QMimeData()
        payload = json.dumps({"uid": uid, "from_cell": -1})
        md.setData(MIME_CAMERA, payload.encode("utf-8"))
        md.setText(uid)
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)