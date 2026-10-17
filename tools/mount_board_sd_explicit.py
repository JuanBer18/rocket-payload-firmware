import os
from machine import Pin, SDCard


card = SDCard(slot=2, sck=Pin(15), mosi=Pin(16), miso=Pin(17), cs=Pin(18))
print("card constructed")
vfs = os.VfsFat(card)
print("FAT object constructed")
os.mount(vfs, "/sd")
print("mounted", os.listdir("/sd"))
os.umount("/sd")
card.deinit()
print("EXPLICIT MOUNT PASS")
