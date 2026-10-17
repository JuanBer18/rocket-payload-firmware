import gc
import os

from core import storage


TARGET = "/sd"
assert TARGET == "/sd"

# The Ctrl-C used to enter raw REPL unwinds main(), releasing its logger.
# Collect before unmounting so any abandoned file object closes first.
gc.collect()
storage.unmount()
storage.mount()


def remove_tree(path):
    mode = os.stat(path)[0]
    if mode & 0x4000:
        for name in os.listdir(path):
            remove_tree(path + "/" + name)
        os.rmdir(path)
    else:
        os.remove(path)


before = os.listdir(TARGET)
print("SD ERASE target", TARGET)
print("SD ERASE before", before)
for entry in before:
    remove_tree(TARGET + "/" + entry)
after = os.listdir(TARGET)
print("SD ERASE after", after)
assert after == []
storage.unmount()
print("SD ERASE PASS")
