from machine import Pin, SDCard


card = SDCard(slot=2, sck=Pin(15), mosi=Pin(16), miso=Pin(17), cs=Pin(18))
print("blocks", card.ioctl(4, 0), "block_size", card.ioctl(5, 0))
sector = bytearray(512)
result = card.readblocks(0, sector)
print("readblocks", result)
print("sector0_first64", bytes(sector[:64]))
print("signature", bytes(sector[510:512]))
card.deinit()
print("RAW READ PASS")
