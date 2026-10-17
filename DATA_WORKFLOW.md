# Payload data workflow

## Mission profiles

The dashboard stores the selected boot profile in the ESP32's internal flash.
It takes effect on the next reset or power cycle.

- `launch_triggered`: IMU and BME280 log in `PAD_IDLE`. A sustained 3 g event
  or the Simulate Liftoff button starts the SEN65 and high-rate flight logging.
  Logging continues until it is stopped manually.
- `continuous`: IMU, BME280, and SEN65 start automatically during boot and log
  continuously. Use this for room-air measurements.

Neither profile has an automatic timer. Use **Stop, close and make safe** in the
dashboard to stop logging and safely close the binary file. **Return to idle**
also closes the current file, but immediately opens a new launch-triggered idle
log. Removing power stops the payload, but a dashboard stop is preferable
because it flushes the final buffered records and writes a closing marker.

## Fault tolerance and safe power-off

IMU, BME280, and SEN65 failures are isolated. A read error is recorded in the
BIN and dashboard health counters, while every healthy sensor continues to be
sampled and logged. After five consecutive errors, only that sensor is taken
offline. The firmware retries missing sensors every five seconds. When one
recovers, it rejoins automatically and the logger starts a continuation BIN
whose header contains the recovered sensor's calibration and scale metadata.

An SD write failure cannot stop sensor sampling. The failed file handle is
closed and the firmware attempts to open a continuation BIN every two seconds.
Actual persistence is necessarily impossible while the SD card itself is
unavailable, so the dashboard separately displays `logging_active` and
`logging_errors`; do not treat sensor activity alone as proof that bytes are
reaching the card.

For shutdown, press **Stop, close and make safe** and keep power connected until
the green **SAFE TO POWER OFF** panel appears. That indication is shown only
after IMU production is stopped, the final FIFO tail is drained, a stop marker
is written, and the BIN is flushed and closed. If close verification fails,
the dashboard explicitly says to keep power on.

Real-time acquisition always uses a compact binary file. The ESP32 does not
convert it to CSV. Convert a downloaded BIN on a computer when needed; the BIN
remains the authoritative source and is never deleted by conversion.

## Altitude and vertical speed

The BME280 measures pressure. The current height is above-ground-level (AGL),
not height above sea level:

```text
h = 44330 * (1 - (P / P0) ^ 0.1903)
```

`P` is the current pressure and `P0` is the pressure at the launch point. The
first valid pressure automatically becomes `P0`. The Zero barometric altitude
button replaces `P0` with the current pressure and resets displayed height and
vertical speed to zero. It does not reset or recalibrate the BME280.

Vertical speed is the change in barometric altitude divided by elapsed time,
then low-pass filtered with a 0.5 second time constant. The accelerometer is
not mixed into the current altitude solution because body-axis acceleration is
not vertical acceleration after the rocket tilts. Raw IMU and pressure data are
retained so an attitude-aware fusion filter can be developed after flight.

Likely error sources include pressure-port airflow, rapid temperature changes,
weather changes while waiting on the pad, enclosure pressure lag, and pressure
disturbance during high-speed flight.

## What the SEN65 measures

The internal fan draws ambient air through an optical particle chamber. An
850 nm Class 1 laser illuminates particles and the scattered light is converted
to particle and mass estimates. The module updates every `1 +/- 0.03 s`.

Logged channels are:

- PM1.0, PM2.5, PM4.0, and PM10 mass concentration in micrograms per cubic metre
- PM0.5, PM1.0, PM2.5, PM4.0, and PM10 number concentration in particles/cm3
- SEN65 relative humidity and temperature
- VOC Index and NOx Index
- raw humidity, temperature, VOC, and NOx signals
- device-status word and verified fan/PM/gas/RH&T error flags

Important interpretation:

- PM specified range is 0 to 1000 micrograms/m3. Typical PM stabilization is
  30 seconds after measurement starts.
- PM1.0 and PM2.5 have substantially tighter precision than PM4/PM10. Optical
  response depends on aerosol composition; it is not a laboratory gravimetric
  measurement.
- VOC and NOx indices are adaptive relative air-quality signals from 1 to 500,
  not identification or concentration of a specific gas.
- Raw VOC events are reliably detectable in under 60 seconds and raw NOx events
  in under 300 seconds. Full processed-index specifications require up to one
  hour for VOC and six hours for NOx.
- Integrated temperature/RH can show enclosure self-heating offsets. Sensirion
  recommends application-specific compensation for best absolute accuracy.
- Keep inlet/outlet openings unobstructed. Avoid solvents, alcohol cleaners,
  adhesives/outgassing materials, strong air blasts, and mechanical damage to
  the inlet filter.

These points come from `PS_DS_SEN6x.pdf` pages 4-7 and the local SEN6x testing,
handling, temperature-compensation, sensor-specification, calibration, gas,
and optical-PM documents in `sen65 information/`.

## Room-air test

1. Select Continuous from power-on in the dashboard and save the boot mode.
2. Reset the payload.
3. Wait at least 30 seconds for PM; wait longer for meaningful gas behaviour.
4. Keep inlet and outlet clear and avoid breathing directly into the unit.
5. Watch live readings at `http://192.168.4.1/`.
6. When finished, press **Stop and close mission** and wait for
   `MISSION_COMPLETE` before removing power.

In launch-triggered mode, Start SEN65 can be used for the same live room test.
Stop and close mission safely ends that log. Return to idle closes it and starts
a fresh idle log.

## Optional live plots

The dashboard's **Activate live plots** button opens one browser-side chart for
each selected IMU, barometer, and SEN65 variable. The browser builds its own
ten-minute history from the normal one-second status response; the ESP32 does
not keep a second history or perform extra sensor reads.

Live plots are always off after reset or power-up. They can be switched off
manually with the same button, and the ESP32 forcibly disables them ten minutes
after activation. This plotting option does not change mission logging or the
binary data file.

## Dashboard states and wireless updates

The compact dashboard has three tabs: Status, Mission Controls, and Debug / Files.
`ACTIVE / RECORDING` and `ARMED / RECORDING` lock every disruptive control;
only **Stop + close safely** remains available. A safe stop closes the BIN and
shows `IDLE`. **Start new mission** from IDLE opens the next numbered BIN before
starting the sensors. Live plots and maintenance tests are available only in
IDLE and never write a mission BIN.

Debug mode is held only in RAM and is always off after reset. It can be entered
only when no BIN is open. Debug exposes SD download/delete controls and a staged
wireless source updater. Each uploaded `.py` file is written to internal-flash
staging, size-limited, SHA-256 hashed, UTF-8 decoded, and syntax-checked before
it appears in the staged list. Installing a staged update preserves `.ota.bak`
copies and reboots. `boot.py` starts a 15-second rollback guard; `main.py`
confirms the update only after the Wi-Fi dashboard starts. If imports or the new
dashboard fail, the guard resets the board and restores the previous files.
`boot.py` and `core/ota.py` remain UART-only recovery components by design.

The SD mount check requires a live `machine.SDCard` object as well as the `/sd`
path. This prevents a bare `/sd` directory in ESP32 flash from being mistaken
for removable storage during logger recovery.

## Getting and plotting data

Join Wi-Fi `HYPOWER PAYLOAD` with the password set as `AP_PASSWORD` in `config.py`, open
`http://192.168.4.1/`, and use the SD Logs links to download the BIN. New
missions create BIN files only. Older CSV files already on the card are left
untouched. Stop the mission before downloading its active BIN.

For the easiest complete workflow, provide either a flight name or a full path:

```powershell
python tools\process_flight.py flight_020
```

The command searches the project and `downloaded flight data` folder, converts
the BIN, produces the text report, a readable multi-page PDF and all plots, and
creates an altitude-resolved SEN65 profile. With no flight argument it processes
the newest downloaded BIN.
Results are written beside the source in `<flight>_analysis`. The altitude
profile includes the paired sample table, altitude-bin medians/quartiles split
by ascent/descent/level phase, and plots of particulate mass, particle number,
gas indices, temperature, and humidity against barometric AGL altitude.

The lower-level conversion command remains available:

```powershell
python tools\decode.py flight_001.bin -o flight_001.csv
```

For interactive time investigation and cropping, open the decoded flight in
the desktop inspector:

```powershell
python tools\inspect_flight.py flight_021
```

The aligned Overview, Motion, Environment, and Air quality views share one time
axis. The MATLAB-style signal browser can build a custom 1-8-axis workspace;
Ctrl-click signals, choose the axis count, then press **Plot selected**. Use the
standard zoom/pan/save toolbar, optional A/B data cursors, or turn on **Select
crop** and drag on the bottom plot. The statistics table reports count, minimum,
maximum, mean, and standard deviation for the selected interval. Exact start/end
fields can also be copied from the visible window.
**Export cropped CSV** writes every original sparse sensor/marker record inside
the interval while preserving its original `t_us`; it never changes the source
CSV. Display downsampling retains local minima and maxima so short acceleration
peaks remain visible, while export always uses the full-resolution rows.

The resulting CSV opens directly in Excel. Its rows are sparse and the
`source` column identifies IMU, barometer, SEN65, marker, configuration, or
time-sync rows.

Create a quick-look plot on this computer with:

```powershell
python tools\plot_mission.py C:\path\to\flight_001.csv
```

This writes `flight_001_quicklook.png` with altitude, acceleration, particulate
mass and number, temperature, humidity, processed gas indices, and raw gas
signals.

Power the payload down before physically removing the SD card. Never remove it
while `MISSION_ACTIVE` or `PAD_IDLE` is displayed because a BIN is open in both
states. Press **Stop and close mission** and wait for `MISSION_COMPLETE` first.
