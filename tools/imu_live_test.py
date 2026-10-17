"""Short on-board IMU range and polled-throughput test.

Run through tools/repl_exec.py while SEN65 is physically disconnected.
"""

from machine import I2C, Pin
import math
import time

from drivers.lsm6dso32 import LSM6DSO32


i2c = I2C(0, sda=Pin(9), scl=Pin(10), freq=400000)
print("FS_TEST")
for fs in (4, 8, 16, 32):
    imu = LSM6DSO32(
        i2c, 0x6B, accel_fs_g=fs, gyro_fs_dps=2000, odr_hz=416
    ).init()
    time.sleep_ms(100)
    values = [imu.read() for _ in range(100)]
    magnitudes = [
        math.sqrt(value[0] ** 2 + value[1] ** 2 + value[2] ** 2)
        for value in values
    ]
    mean = sum(magnitudes) / len(magnitudes)
    print(fs, mean, min(magnitudes), max(magnitudes),
          "PASS" if 0.95 < mean < 1.05 else "FAIL")

imu = LSM6DSO32(
    i2c, 0x6B, accel_fs_g=32, gyro_fs_dps=2000, odr_hz=1666
).init()
start = time.ticks_us()
reads = 0
while time.ticks_diff(time.ticks_us(), start) < 2_000_000:
    imu.read()
    reads += 1
elapsed_us = time.ticks_diff(time.ticks_us(), start)
print("MAX_POLLED_HZ", reads * 1_000_000 / elapsed_us)
