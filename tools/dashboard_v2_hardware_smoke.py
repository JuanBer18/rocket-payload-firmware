"""On-board smoke test for dashboard v2 without deleting mission data."""

import time

from core import ota
from mission import MISSION_ACTIVE, MISSION_COMPLETE


class ByteSource:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def read(self, count):
        chunk = self.data[self.offset:self.offset + count]
        self.offset += len(chunk)
        return chunk


first = web._status()
print("initial", first["state"], first["system"]["log_path"])
if first["state"] != MISSION_ACTIVE or not first["system"]["logging_active"]:
    raise AssertionError("continuous boot did not start recording")
first_path = first["system"]["log_path"]

if web._action("/api/action?action=debug_on").get("ok"):
    raise AssertionError("DEBUG was allowed while recording")
if web._action("/api/action?action=graphs_on").get("ok"):
    raise AssertionError("maintenance plots were allowed while recording")

stopped = web._action("/api/action?action=stop")
print("stopped", stopped)
if payload.state != MISSION_COMPLETE or payload.logger is not None:
    raise AssertionError("safe stop did not reach closed IDLE")
if not payload.safe_to_power_off:
    raise AssertionError("safe stop was not verified")

plots = web._action("/api/action?action=graphs_on")
if not plots.get("ok") or not payload.maintenance_sampling:
    raise AssertionError("IDLE live plots did not start maintenance sampling")
web._action("/api/action?action=graphs_off")
if payload.maintenance_sampling:
    raise AssertionError("live plot stop left maintenance sampling active")

started = web._action("/api/action?action=start")
print("started", started)
if not started.get("ok") or payload.state != MISSION_ACTIVE:
    raise AssertionError("IDLE start failed")
second_path = payload.logger.path
if second_path == first_path:
    raise AssertionError("new mission reused the previous BIN")
deadline = time.ticks_add(time.ticks_ms(), 1200)
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    payload.tick()
    time.sleep_ms(5)
stopped = web._action("/api/action?action=stop")
if not stopped.get("ok") or payload.logger is not None:
    raise AssertionError("second mission did not close safely")

debug = web._action("/api/action?action=debug_on")
if not debug.get("ok") or not web.debug_enabled:
    raise AssertionError("DEBUG did not unlock from IDLE")
logs = payload.list_logs()
names = [item[0] for item in logs]
if first_path.rsplit("/", 1)[-1] not in names or second_path.rsplit("/", 1)[-1] not in names:
    raise AssertionError("new BIN files are absent from SD file listing")
if web._action("/api/action?action=delete_file&name=not_a_flight.bin").get("ok"):
    raise AssertionError("unsafe filename passed deletion validation")

source = b"SMOKE_VALUE = 1\n"
entry = ota.stage_stream("core/ota_smoke.py", ByteSource(source), len(source))
if entry["target"] != "core/ota_smoke.py" or not ota.staged_files():
    raise AssertionError("wireless update staging failed")
discarded = ota.discard_staged()
if not discarded.get("ok") or ota.staged_files():
    raise AssertionError("staged update did not discard cleanly")
web._action("/api/action?action=debug_off")

print("DASHBOARD_V2_SMOKE_PASS", first_path, second_path, len(logs))
