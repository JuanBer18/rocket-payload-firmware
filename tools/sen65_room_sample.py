import json
import time

import config
from core import buses
from drivers.sen65 import SEN65


sensor = SEN65(buses.init_bus(config.SEN65_BUS), config.SEN65_ADDR).init()
samples = []
try:
    sensor.start_measurement()
    print("SEN65 warming for 30 seconds")
    time.sleep_ms(30000)
    for index in range(10):
        deadline = time.ticks_add(time.ticks_ms(), 2000)
        while not sensor.data_ready():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise OSError("SEN65 data-ready timeout")
            time.sleep_ms(100)
        _words, values = sensor.read_values()
        _raw_words, raw = sensor.read_raw_signals()
        _number_words, number = sensor.read_number_concentration()
        status, flags = sensor.device_status()
        row = {}
        row.update(values)
        row.update(raw)
        row.update(number)
        row["status_raw"] = status
        row.update(flags)
        samples.append(row)
        print("SAMPLE", index + 1, json.dumps(row))
        time.sleep_ms(850)
finally:
    if sensor.measuring:
        sensor.stop_measurement()

averages = {}
for key in samples[0]:
    values = [row[key] for row in samples
              if isinstance(row.get(key), (int, float)) and
              not isinstance(row.get(key), bool)]
    if values:
        averages[key] = sum(values) / len(values)
print("AVERAGES", json.dumps(averages))
print("SEN65 ROOM SAMPLE PASS")
