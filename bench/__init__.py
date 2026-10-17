# bench -- per-sensor characterization scripts.
#
# Each module runs standalone over the REPL and prints pass/fail plus a block
# of measurements ready to paste into RESULTS.md.
#
#     from bench import test_imu;    test_imu.run_all()
#     from bench import test_baro;   test_baro.run_all()
#     from bench import test_sen65;  test_sen65.run_all()
#     from bench import test_sd;     test_sd.run_all()
#     from bench import test_system; test_system.run_all()
#
# Test ONE sensor at a time until the 0x6B collision between the IMU and the
# SEN65 is resolved. They cannot share a bus.
