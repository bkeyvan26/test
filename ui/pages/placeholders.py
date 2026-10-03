# -*- coding: utf-8 -*-
"""K1 VMS — Placeholder pages for upcoming phases.

Playback is intentionally NOT here; it lives in ui/pages/playback_page.py
"""
from ui.pages import PlaceholderPage


class LivePage(PlaceholderPage):
    """Phase 3 — Multi-camera live monitoring grid."""
    def __init__(self, parent=None):
        super().__init__(
            key="live",
            title="Live View",
            icon_name="video",
            subtitle="Multi-camera live monitoring — Phase 3",
            parent=parent
        )


class AlarmsPage(PlaceholderPage):
    """Phase 5 — Alarm and event management."""
    def __init__(self, parent=None):
        super().__init__(
            key="alarms",
            title="Alarms",
            icon_name="bell",
            subtitle="Alarm & event management — Phase 5",
            parent=parent
        )


class AIPage(PlaceholderPage):
    """Phase 6 — AI-powered video analytics."""
    def __init__(self, parent=None):
        super().__init__(
            key="ai",
            title="AI Analytics",
            icon_name="cpu",
            subtitle="AI-powered video analytics — Phase 6",
            parent=parent
        )


class SearchPage(PlaceholderPage):
    """Future phase — Cross-camera and event search."""
    def __init__(self, parent=None):
        super().__init__(
            key="search",
            title="Search",
            icon_name="search",
            subtitle="Search across cameras and events",
            parent=parent
        )


class MapPage(PlaceholderPage):
    """Future phase — Camera map view."""
    def __init__(self, parent=None):
        super().__init__(
            key="map",
            title="Map",
            icon_name="map",
            subtitle="Camera map view",
            parent=parent
        )


class ReportsPage(PlaceholderPage):
    """Future phase — System reports and statistics."""
    def __init__(self, parent=None):
        super().__init__(
            key="reports",
            title="Reports",
            icon_name="bar-chart",
            subtitle="System reports and statistics",
            parent=parent
        )


class SettingsPage(PlaceholderPage):
    """Phase 7 — Global settings, retention, recording, etc."""
    def __init__(self, parent=None):
        super().__init__(
            key="settings",
            title="Settings",
            icon_name="sliders",
            subtitle="Global settings — Phase 7",
            parent=parent
        )


class UsersPage(PlaceholderPage):
    """Phase 7 — User management & permissions."""
    def __init__(self, parent=None):
        super().__init__(
            key="users",
            title="Users",
            icon_name="users",
            subtitle="User management & permissions — Phase 7",
            parent=parent
        )