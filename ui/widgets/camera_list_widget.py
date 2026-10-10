# -*- coding: utf-8 -*-
"""K1 VMS — Camera/NVR/Group tree widget (collapsed default)"""
import json
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtGui import QDrag, QColor

from ui.widgets.live_grid import MIME_CAMERA


NVR_PREFIX = "nvr:"
GROUP_PREFIX = "grp:"


class CameraTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragOnly)
        self.setSelectionMode(QTreeWidget.SingleSelection)
        self.setIndentation(14)
        self.setAnimated(True)
        self.setExpandsOnDoubleClick(False)
        self._cam_to_nvr = {}

    def clear(self):
        super().clear()
        self._cam_to_nvr = {}

    def add_standalone_root(self, count: int) -> QTreeWidgetItem:
        root = QTreeWidgetItem(self)
        root.setText(0, f"📷  دوربین‌های مستقل  ({count})")
        root.setForeground(0, QColor("#85c1e9"))
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        root.setExpanded(False)          # ★ بسته پیش‌فرض
        root.setFlags(Qt.ItemIsEnabled)
        return root

    def add_standalone_under(self, root, cam) -> QTreeWidgetItem:
        item = QTreeWidgetItem(root)
        item.setText(0, f"  {cam.name or cam.uid}")
        item.setData(0, Qt.UserRole, ("camera", cam.uid))
        item.setForeground(0, QColor("#aed6f1"))
        return item

    def add_nvr(self, nvr, cameras: list) -> QTreeWidgetItem:
        nvr_uid = nvr.uid
        for c in cameras:
            self._cam_to_nvr[c.uid] = nvr_uid

        root = QTreeWidgetItem(self)
        root.setText(0, f"📹  {nvr.name}  ({len(cameras)} ch)")
        root.setData(0, Qt.UserRole, ("nvr", nvr_uid))
        root.setForeground(0, QColor("#c39bd3"))
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        root.setExpanded(False)          # ★ بسته پیش‌فرض
        root.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        for cam in cameras:
            child = QTreeWidgetItem(root)
            label = cam.name or "—"
            if " دوربین " in label:
                label = "دوربین " + label.split(" دوربین ", 1)[1]
            child.setText(0, f"  {label}")
            child.setData(0, Qt.UserRole, ("camera", cam.uid))
            child.setForeground(0, QColor("#d7bde2"))
        return root

    def add_custom_group(self, name: str, uid: str, cameras: list,
                         color: str = "#f39c12") -> QTreeWidgetItem:
        root = QTreeWidgetItem(self)
        root.setText(0, f"📁  {name}  ({len(cameras)})")
        root.setData(0, Qt.UserRole, ("group", uid))
        root.setForeground(0, QColor(color))
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        root.setExpanded(False)          # ★ بسته پیش‌فرض
        root.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        for cam in cameras:
            child = QTreeWidgetItem(root)
            child.setText(0, f"  {cam.name or cam.uid}")
            child.setData(0, Qt.UserRole, ("camera", cam.uid))
            child.setForeground(0, QColor(color).lighter(140))
        return root

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        kind, uid = data

        if kind == "camera":
            drag_uid = uid
            payload = {
                "kind": "camera",
                "uid": uid,
                "nvr_uid": self._cam_to_nvr.get(uid, ""),
                "from_cell": -1,
            }
        elif kind == "nvr":
            drag_uid = f"{NVR_PREFIX}{uid}"
            payload = {
                "kind": "nvr",
                "uid": drag_uid,
                "nvr_uid": uid,
                "from_cell": -1,
            }
        elif kind == "group":
            drag_uid = f"{GROUP_PREFIX}{uid}"
            payload = {
                "kind": "group",
                "uid": drag_uid,
                "group_uid": uid,
                "from_cell": -1,
            }
        else:
            return

        md = QMimeData()
        md.setData(MIME_CAMERA, json.dumps(payload).encode("utf-8"))
        md.setText(drag_uid)
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)