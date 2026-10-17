"""Pause autostart and preserve the pre-dashboard-v2 board files."""

import os


pairs = (
    ("main.py", "main_before_dashboard_v2.py"),
    ("dashboard.py", "dashboard_before_dashboard_v2.py"),
    ("mission.py", "mission_before_dashboard_v2.py"),
    ("config.py", "config_before_dashboard_v2.py"),
    ("boot.py", "boot_before_dashboard_v2.py"),
    ("core/storage.py", "core/storage_before_dashboard_v2.py"),
)

root_names = os.listdir("/")
core_names = os.listdir("/core")
for _source, backup in pairs:
    directory, name = backup.rsplit("/", 1) if "/" in backup else ("", backup)
    existing = core_names if directory == "core" else root_names
    if name in existing:
        raise OSError("refusing to overwrite existing backup: " + backup)

for source, backup in pairs:
    os.rename(source, backup)
    print("saved", backup)
print("autostart paused")
