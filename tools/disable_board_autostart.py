import os

try:
    os.rename("main.py", "main_enabled.py")
except OSError:
    pass
print("autostart disabled", os.listdir("/"))
