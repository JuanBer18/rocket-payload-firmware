import time

from core import storage
from dashboard import Dashboard
from mission import MISSION_ACTIVE, MISSION_COMPLETE, MissionController


payload = MissionController().boot()
web = Dashboard(payload)
assert payload.state == MISSION_ACTIVE
assert not web._graph_status()["graphs_enabled"]

enabled = web._action("/api/action?action=graphs_on")
assert enabled["ok"] and enabled["graphs_enabled"]
assert 0 < enabled["graphs_remaining_s"] <= 600
status = web._status()
assert status["dashboard"]["graphs_enabled"]
assert "accel_mag_g" in status["imu"]
assert "pressure_pa" in status["bme280"]

deadline = time.ticks_add(time.ticks_ms(), 3000)
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    payload.tick()
    web.poll()
    time.sleep_ms(1)
assert payload.state == MISSION_ACTIVE
assert "pm2p5" in payload.status_snapshot()["sen65"]

disabled = web._action("/api/action?action=graphs_off")
assert disabled["ok"] and not disabled["graphs_enabled"]
web._set_graphs(True)
web._graphs_deadline_ms = time.ticks_add(time.ticks_ms(), -1)
assert not web._graph_status()["graphs_enabled"]

assert payload.stop_mission()["ok"]
assert payload.state == MISSION_COMPLETE
assert payload.errors == 0
assert payload.fifo_overruns == 0
print("GRAPH DASHBOARD PASS", storage.list_data_files())
storage.unmount()
