import os
from machine import Pin, SDCard

try:
    os.umount("/sd")
    print("unmounted stale VFS")
except Exception as exc:
    print("umount result", repr(exc))

card = SDCard(slot=2, sck=Pin(15), mosi=Pin(16), miso=Pin(17), cs=Pin(18))
print("card constructed")
os.mount(card, "/sd")
print("mounted", os.listdir("/sd"))
