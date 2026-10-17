# Rocket payload firmware

MicroPython firmware and host tools for an atmospheric science payload on an ESP32-S3. I designed the board and wrote this firmware for HyPower Bristol's Launch 4 Change rocket (flown in August 2026). It is a student project, shared as it was built.

## What the payload does

- Reads an LSM6DSO32 IMU, a BME280 barometer and a SEN65 particulate and gas sensor, and logs to an SD card.
- Detects lift-off from acceleration and runs a small mission state machine (pad idle, mission active, mission complete).
- Serves an offline Wi-Fi dashboard from the board, with live readings, a file manager and a staged update mechanism with rollback.
- Keeps working when a sensor fails: the fault is reported and the sensor is retried instead of stopping the mission.

## The main idea

The logging path never touches an individual sample. The IMU's FIFO buffers at the sensor's own rate and each block of samples is copied to the card as raw bytes. Everything is stored as raw counts, so scaling and compensation run on a laptop afterwards. A wrong scale factor then costs a re-run of the decoder, not a corrupted flight. The dashboard uses a separate slow path so a browser refresh cannot cost IMU samples.

## Layout

```
main.py, boot.py, mission.py   entry point, boot guard, mission controller
dashboard.py                   Wi-Fi dashboard and file manager
config.py                      pins, addresses, rates, scale factors
recfmt.py                      binary record format, shared with the host decoder
core/                          clock, buses, SD storage, logger, altitude, update staging
drivers/                       LSM6DSO32, BME280, SEN65
bench/                         one characterisation script per sensor, run on the board
tools/                         host-side decoding and analysis
tests/                         tests that run without hardware
```

RESULTS.md records what the sensors measured on the bench. DATA_WORKFLOW.md explains how to get data off the board and decode it.

## Notes

- Set your own `AP_PASSWORD` in `config.py` before using the Wi-Fi access point.
- The particulate readings from the flight are measurements under launch conditions, not a validated pollution profile.

## Licence

MIT. See `LICENSE`.
