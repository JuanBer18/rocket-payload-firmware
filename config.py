# config.py -- single source of truth for the bench rig.
#
# Every pin, address, rate and scale factor lives here. Nothing else in the
# tree hardcodes a magic number that belongs in this file.
#
# Values marked UNVERIFIED are starting points that Phase 1 is supposed to
# replace with measured numbers. Do not treat them as facts until RESULTS.md
# says otherwise.

# ---------------------------------------------------------------- board
# The module is an N16R2: 16MB flash, 2MB *quad* PSRAM. The octal firmware
# the original spec called for will not boot it. Correct firmware is the
# plain ESP32_GENERIC_S3 build; gc.mem_free() should be ~2,058,752.
EXPECTED_MEM_FREE_MIN = 1_800_000

# ------------------------------------------------------------------ I2C
# Pull-ups are 4.7k to 3V3_SR on both lines.
I2C0_SDA = 9
I2C0_SCL = 10

# 400kHz, not the 100kHz v1 used. Every rate ceiling in the plan assumes this.
# The 12-byte IMU burst is ~1.38ms at 100kHz but ~0.35ms at 400kHz, which is
# the difference between a ~700Hz and a ~2.9kHz polled ceiling.
I2C0_FREQ = 400_000

# Second bus for the SEN65. GPIO39/40 are exposed on J13 and are routed
# through the ESP32-S3 GPIO matrix. They overlap the external JTAG function,
# which is not used by this payload. This bus needs its own pull-ups.
I2C1_SDA = 40
I2C1_SCL = 39
I2C1_FREQ = 100_000

# -------------------------------------------------------------- devices
# THE 0x6B COLLISION: LSM6DSO32 and SEN65 both answer at 0x6B as wired.
#
# The IMU address is strap-selectable -- 0x6A when SA0/SD0 is low, 0x6B when
# high. It enumerated at 0x6B, so SA0 is pulled high on this board. If that
# strap can be moved to GND the IMU becomes 0x6A and the collision disappears
# with no code change beyond the line below.
#
# Phase 0 checks the schematic. Until then the two cannot share a bus, and
# buses.py refuses to bring both up at once rather than returning silent
# garbage from whichever part answers first.
IMU_ADDR = 0x6B          # set to 0x6A if the SA0 strap gets pulled low
IMU_BUS = 0

BARO_ADDR = 0x76         # SDO tied to GND
BARO_BUS = 0

SEN65_ADDR = 0x6B        # set if the module turns out to have an address strap
SEN65_BUS = 1            # separate bus avoids its fixed 0x6B colliding with IMU

# --------------------------------------------------------------- IMU
IMU_WHOAMI_REG = 0x0F
IMU_WHOAMI_EXPECT = 0x6C

# LSM6DSO32 is 4/8/16/32g -- NOT the plain LSM6DSO's 2/4/8/16. Getting this
# wrong scales every accel sample by 2x and the error looks plausible.
IMU_ACCEL_FS_G = 32
IMU_GYRO_FS_DPS = 2000
# The IMU alone sustained 1666 Hz, but the integrated mission also services
# the SEN65's blocking command delays, BME280, SD, and web status. Hardware
# integration testing showed FIFO overruns at 1666 Hz and zero-loss headroom
# is more valuable than unused bandwidth, so flight logging uses 833 Hz.
IMU_ODR_HZ = 833

# mg per LSB, indexed by full-scale in g. Verify empirically: at rest the
# accel magnitude must read 1.000g at *every* full-scale setting.
IMU_ACCEL_MG_PER_LSB = {4: 0.122, 8: 0.244, 16: 0.488, 32: 0.976}
IMU_GYRO_MDPS_PER_LSB = {250: 8.75, 500: 17.50, 1000: 35.0, 2000: 70.0}

# On-chip temperature: degC = raw/256 + 25. v1 never read this register; it
# is what gyro bias gets compensated against.
IMU_TEMP_LSB_PER_C = 256.0
IMU_TEMP_OFFSET_C = 25.0

# Hardware timestamp counter resolution. This is the flight timebase anchor --
# it lets samples be placed by the sensor's own clock rather than by whenever
# Python got around to draining the FIFO.
IMU_TIMESTAMP_RES_US = 25.0

# FIFO watermark in words (7 bytes each). Sized against the SD write
# throughput measured in Phase 1d -- 200 words is ~1.4KB per drain.
# 400 words (~2.8 kB) leaves enough FIFO headroom for the SEN65's blocking
# command/read cycle and an occasional dashboard request at 1666 Hz.
IMU_FIFO_WATERMARK = 400
IMU_INT1_PIN = 11

# --------------------------------------------------------------- BME280
BARO_ID_REG = 0xD0
BARO_ID_BME280 = 0x60    # has humidity
BARO_ID_BMP280 = 0x58    # no humidity channel

# Oversampling codes: 0=skip, 1=x1, 2=x2, 3=x4, 4=x8, 5=x16
# Pressure gets the oversampling because altitude resolution depends on it.
BARO_OSR_P = 5
BARO_OSR_T = 2
BARO_OSR_H = 1

# IIR filter coefficient: 0=off, 1=x2, 2=x4, 3=x8, 4=x16.
# UNVERIFIED TRADE -- higher is quieter but lags. A rocket climbing at 200 m/s
# is badly served by a heavy filter. Phase 1b measures the step response and
# picks the knee; this is a placeholder, not an answer.
BARO_IIR = 2

BARO_STANDBY = 0         # t_sb code 0 = 0.5ms

# ---------------------------------------------------------------- SEN65
SEN65_STOP_EXEC_MS = 1400
SEN65_RESTART_GUARD_MS = 1400
# Measured twice on this unit at ~1620 ms; retain a small margin.
SEN65_WARMUP_MS = 1700

# ---------------------------------------------------------------- SD card
# machine.SDCard(slot=1) accepts no pin arguments and the card is wired to
# IO15/16/17/18 rather than any port default, so SPI (slot=2) is the only
# usable path. Confirmed on hardware.
SD_MODE = "spi"
SD_SPI_SLOT = 2
SD_CLK = 15
SD_MOSI = 16
SD_MISO = 17
SD_CS = 18
SD_MOUNT = "/sd"

LOG_PREFIX = "flight_"
LOG_EXT = ".bin"

# How many records between flushes. Phase 1d measures what durability costs;
# until then this is a guess biased toward safety.
LOG_FLUSH_EVERY = 16

# ---------------------------------------------------------------- board IO
LED_RED = 2              # active LOW
VBAT_ADC_PIN = None      # no sense divider on this board -- hook only

# ---------------------------------------------------------------- WiFi AP
AP_SSID = "HYPOWER PAYLOAD"
AP_PASSWORD = "change-me-8-chars-min"  # set your own; must be at least 8 characters (WPA2)
AP_CHANNEL = 6
HTTP_PORT = 80

# Dashboard sampling is a *separate, slow* path from the logger. It never
# touches the FIFO drain, so a browser refresh cannot cost IMU samples.
DISPLAY_RATE_HZ = 10
HISTORY_POINTS = 600

# Optional browser-side live plots. They are always disabled on boot and the
# dashboard forcibly switches them off after ten minutes.
DASHBOARD_GRAPH_TIMEOUT_MS = 10 * 60 * 1000
DASHBOARD_GRAPH_POINTS = 600

# Cold-power sequencing. The dashboard access point is started before sensor
# and SD initialisation so a boot failure remains visible instead of looking
# like a dead board. Failed payload initialisation is retried indefinitely.
POWER_SETTLE_MS = 1000
BOOT_RETRY_DELAY_MS = 2000
DASHBOARD_START_RETRIES = 3

# Runtime fault isolation. A failed sensor is removed after consecutive I/O
# errors, while the other sensors and SD logger continue. It is then retried
# periodically and can rejoin the same mission automatically.
SENSOR_ERROR_REINIT_COUNT = 5
SENSOR_RECOVERY_INTERVAL_MS = 5000
LOGGER_RECOVERY_INTERVAL_MS = 2000

# --------------------------------------------------------- nominal mission
# Keep the pad rate modest while still checking acceleration often enough to
# catch a real launch. At liftoff the controller switches to IMU_ODR_HZ.
PAD_IMU_ODR_HZ = 104
PAD_BARO_RATE_HZ = 10
FLIGHT_BARO_RATE_HZ = 20
# The SEN65 produces approximately one new sample per second. Polling twice a
# second catches it promptly without spending four 20 ms I2C waits per second.
SEN65_POLL_RATE_HZ = 2
TIMESYNC_RATE_HZ = 1

# Automatic liftoff: acceleration magnitude must remain over this threshold
# for the complete dwell interval. The dashboard's Simulate Liftoff button
# uses the exact same mission transition, but bypasses this detector.
LIFTOFF_ACCEL_G = 3.0
LIFTOFF_DWELL_MS = 50

# Missions do not stop on a timer. Logging continues until the dashboard's
# Stop and close mission/Return to idle command, or until power is removed.
MISSION_AUTO_STOP = False

# Mission profiles. The selected boot profile is persisted by the dashboard
# in internal flash, not on the removable SD card. Launch-triggered is the
# safe factory default.
MISSION_MODE_LAUNCH = "launch_triggered"
MISSION_MODE_CONTINUOUS = "continuous"
MISSION_MODE_FILE = "/mission_mode.json"
MISSION_MODE_DEFAULT = MISSION_MODE_LAUNCH

# Dashboard altitude is height above the pressure recorded at zeroing. The
# vertical-speed estimate is the derivative of barometric altitude with a
# simple low-pass filter; raw pressure remains in the binary log.
VERTICAL_SPEED_TAU_S = 0.5

# Web requests are deliberately small during an active mission. Log downloads
# and disruptive maintenance tests are refused until the mission is idle or
# complete, protecting the IMU FIFO drain.
HTTP_CLIENT_TIMEOUT_S = 0.15
HTTP_DOWNLOAD_CHUNK = 1024

# Debug-mode wireless source updates are staged in internal flash, syntax
# checked, then activated on reboot. boot.py restores .ota.bak files unless
# main.py confirms that the Wi-Fi dashboard started successfully.
OTA_MAX_FILE_BYTES = 96 * 1024
OTA_MAX_TOTAL_BYTES = 384 * 1024
OTA_UPLOAD_CHUNK = 1024
OTA_UPLOAD_TIMEOUT_S = 15
OTA_BOOT_CONFIRM_MS = 15000
