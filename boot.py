"""Minimal early boot setup and staged-update rollback protection."""

import gc
import esp


esp.osdebug(None)

# Always start from a known radio state. main.py brings up the local access
# point explicitly after the interpreter has loaded the application modules.
try:
    import network
    network.WLAN(network.STA_IF).active(False)
    network.WLAN(network.AP_IF).active(False)
except Exception:
    pass

try:
    from core import ota
    ota.boot_guard()
except Exception as exc:
    print("OTA boot guard error: %r" % (exc,))

gc.collect()
