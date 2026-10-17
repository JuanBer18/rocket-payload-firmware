import os
from machine import Pin, SDCard


card = SDCard(slot=2, sck=Pin(15), mosi=Pin(16), miso=Pin(17), cs=Pin(18))
print("FORMAT target SDCard slot=2 CLK=15 MOSI=16 MISO=17 CS=18")
os.VfsFat.mkfs(card)
os.mount(card, "/sd")
contents = os.listdir("/sd")
print("FORMAT after", contents)
assert contents == []
os.umount("/sd")
card.deinit()
print("FORMAT PASS")
