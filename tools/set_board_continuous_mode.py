import json
import os

import config


temp = config.MISSION_MODE_FILE + ".tmp"
with open(temp, "w") as target:
    json.dump({"mode": config.MISSION_MODE_CONTINUOUS}, target)
    target.flush()
try:
    os.remove(config.MISSION_MODE_FILE)
except OSError:
    pass
os.rename(temp, config.MISSION_MODE_FILE)
print("boot mode", open(config.MISSION_MODE_FILE).read())
