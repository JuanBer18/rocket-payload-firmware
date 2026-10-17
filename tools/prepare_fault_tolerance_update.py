"""Put the currently installed boot file aside before a UART update."""

import os


backup = "main_before_fault_tolerance_20260808.py"
if backup in os.listdir("/"):
    raise OSError("refusing to overwrite existing backup: " + backup)
os.rename("main.py", backup)
print("autostart paused; previous main saved as", backup)

