import gc
import network

from core import storage

try:
    payload._close_log("cleanup after interrupted smoke")
    payload.imu.fifo_bypass()
except Exception as exc:
    print("payload cleanup", exc)

try:
    web.sock.close()
except Exception as exc:
    print("web cleanup", exc)

storage.unmount()
network.WLAN(network.AP_IF).active(False)
gc.collect()
print("cleanup complete")
