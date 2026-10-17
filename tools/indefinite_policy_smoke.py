import os
import time

import config
from core import storage
from dashboard import Dashboard
from mission import MISSION_ACTIVE, MISSION_COMPLETE, PAD_IDLE, MissionController


def run_for(payload, web, seconds):
    deadline = time.ticks_add(time.ticks_ms(), seconds * 1000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        payload.tick()
        web.poll()
        time.sleep_ms(1)


def csv_for(binary_path):
    return binary_path[:-4] + ".csv"


def main():
    print("INDEFINITE boot continuous")
    payload = MissionController().boot()
    assert payload.mode == config.MISSION_MODE_CONTINUOUS
    assert payload.state == MISSION_ACTIVE
    assert payload.sen.measuring
    assert payload.status_snapshot()["mission"]["auto_stop"] is False
    web = Dashboard(payload)
    ip = web.start()

    continuous_bin = payload.logger.path
    run_for(payload, web, 5)
    assert payload.state == MISSION_ACTIVE
    result = web._action("/api/action?action=stop")
    assert result["ok"] and payload.state == MISSION_COMPLETE
    assert payload.logger is None
    assert os.stat(continuous_bin)[6] > 0
    try:
        os.stat(csv_for(continuous_bin))
        raise AssertionError("firmware unexpectedly created a CSV")
    except OSError:
        pass
    print("INDEFINITE continuous manually stopped", continuous_bin)

    print("INDEFINITE launch-triggered transition")
    assert payload.return_to_idle()["ok"]
    assert payload.state == PAD_IDLE
    idle_bin = payload.logger.path
    run_for(payload, web, 2)
    assert payload.state == PAD_IDLE
    assert payload.start_mission("indefinite hardware smoke")["ok"]
    run_for(payload, web, 5)
    assert payload.state == MISSION_ACTIVE
    result = web._action("/api/action?action=stop")
    assert result["ok"] and payload.state == MISSION_COMPLETE
    assert payload.logger is None
    assert os.stat(idle_bin)[6] > 0
    try:
        os.stat(csv_for(idle_bin))
        raise AssertionError("firmware unexpectedly created a CSV")
    except OSError:
        pass
    assert payload.errors == 0
    assert payload.fifo_overruns == 0
    print("INDEFINITE launch manually stopped", idle_bin)
    print("INDEFINITE files preserved", storage.list_data_files())

    web.sock.close()
    web.ap.active(False)
    storage.unmount()
    print("INDEFINITE POLICY PASS", ip)


if __name__ == "__main__":
    main()
