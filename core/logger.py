# core/logger.py -- typed binary record writer.
#
# Design constraint that drives everything here: the IMU path must not touch
# an individual sample. write_imu_fifo() takes the raw bytes that came off
# the sensor and puts them on the card unchanged -- no unpacking, no scaling,
# no formatting. That is what lets a Python interpreter log at kHz rates.
#
# Concretely, for a 200-sample FIFO drain this does one struct.pack of a
# 15-byte prefix and two f.write() calls. The old CSV logger did ~40
# interpreter operations per sample, so ~8000 for the same data.
#
# The prefix and the payload are written separately on purpose: concatenating
# them would copy the whole 1.4KB buffer for the sake of 15 bytes.

import json
import os
import struct

import recfmt


class BinaryLogger:
    def __init__(self, path, clock, flush_every=16):
        self.path = path
        self.clock = clock
        self.flush_every = flush_every
        self._f = None
        self._since_flush = 0
        self.records = 0
        self.bytes_written = 0
        self.imu_words = 0
        self._prefix = bytearray(recfmt.REC_HEADER_LEN + recfmt.IMU_FIFO_HDR_LEN)

    # ------------------------------------------------------------- lifecycle
    def open(self, config_snapshot):
        """Create the file and write its header.

        The snapshot is not decoration: the decoder reads scale factors and
        the BME280's per-chip calibration coefficients out of it. A log
        without it cannot be interpreted at all.
        """
        self._f = open(self.path, "wb")
        blob = json.dumps(config_snapshot).encode("utf-8")
        self._f.write(recfmt.pack_file_header(blob))
        self.bytes_written = recfmt.FILE_HDR_LEN + len(blob)
        self._f.flush()
        return self

    def close(self):
        if self._f:
            try:
                self._f.flush()
            finally:
                self._f.close()
                self._f = None

    def flush(self):
        if self._f:
            self._f.flush()
            self._since_flush = 0

    def _maybe_flush(self):
        self._since_flush += 1
        if self._since_flush >= self.flush_every:
            self._f.flush()
            self._since_flush = 0

    def _emit(self, rec_type, payload):
        self._f.write(recfmt.pack_record(rec_type, payload))
        self.records += 1
        self.bytes_written += recfmt.REC_HEADER_LEN + len(payload)
        self._maybe_flush()

    # ------------------------------------------------------------- hot path
    def write_imu_fifo(self, t_us, buf, n_words):
        """Raw FIFO bytes, verbatim. The only path that runs at kHz.

        buf must hold at least n_words * 7 bytes.
        """
        n_bytes = n_words * recfmt.IMU_FIFO_WORD_LEN
        struct.pack_into(recfmt.REC_HEADER_FMT, self._prefix, 0,
                         recfmt.SYNC, recfmt.REC_IMU_FIFO,
                         recfmt.IMU_FIFO_HDR_LEN + n_bytes)
        struct.pack_into(recfmt.IMU_FIFO_HDR_FMT, self._prefix,
                         recfmt.REC_HEADER_LEN, t_us, n_words)

        self._f.write(self._prefix)
        self._f.write(memoryview(buf)[:n_bytes])

        self.records += 1
        self.imu_words += n_words
        self.bytes_written += len(self._prefix) + n_bytes
        self._maybe_flush()

    # ---------------------------------------------------------- slow paths
    def write_baro(self, t_us, adc_p, adc_t, adc_h):
        self._emit(recfmt.REC_BARO,
                   struct.pack(recfmt.BARO_FMT, t_us, adc_p, adc_t, adc_h))

    def write_sen65(self, t_us, words, status=0):
        if len(words) != 8:
            raise ValueError("SEN65 record needs 8 words, got %d" % len(words))
        self._emit(recfmt.REC_SEN65,
                   struct.pack(recfmt.SEN65_FMT, t_us, *words, status))

    def write_sen65_raw(self, t_us, words):
        if len(words) != 4:
            raise ValueError("SEN65 raw record needs 4 words")
        self._emit(recfmt.REC_SEN65_RAW,
                   struct.pack(recfmt.SEN65_RAW_FMT, t_us, *words))

    def write_sen65_number(self, t_us, words):
        if len(words) != 5:
            raise ValueError("SEN65 number record needs 5 words")
        self._emit(recfmt.REC_SEN65_NUMBER,
                   struct.pack(recfmt.SEN65_NUMBER_FMT, t_us, *words))

    def write_timesync(self, t_us, imu_timestamp):
        """Pin the board clock to the IMU's hardware clock.

        Written once a second. The host fits these pairs into a linear map and
        places every FIFO sample by the sensor's own 25us counter, so jitter
        in when the drain happened stops affecting timestamp quality.
        """
        self._emit(recfmt.REC_TIMESYNC,
                   struct.pack(recfmt.TIMESYNC_FMT, t_us, imu_timestamp))

    def write_marker(self, t_us, tag, text=""):
        payload = struct.pack(recfmt.MARKER_HDR_FMT, t_us, tag)
        if text:
            payload += text.encode("utf-8")[:recfmt.MAX_PAYLOAD - len(payload)]
        self._emit(recfmt.REC_MARKER, payload)
        self.flush()          # markers mark something worth not losing

    def write_config(self, t_us, snapshot):
        """Log a live reconfiguration mid-file.

        The bench rate sweeps change ODR and full-scale while logging. Without
        this the decoder would apply one set of scale factors to the whole
        file and quietly mis-scale everything after the first change.
        """
        payload = struct.pack(recfmt.CONFIG_HDR_FMT, t_us)
        payload += json.dumps(snapshot).encode("utf-8")
        self._emit(recfmt.REC_CONFIG, payload)
        self.flush()

    # ------------------------------------------------------------ reporting
    def stats(self):
        return {
            "path": self.path,
            "records": self.records,
            "bytes": self.bytes_written,
            "imu_words": self.imu_words,
        }


def build_header(imu=None, baro=None, sen65=None, **extra):
    """Assemble the file header the decoder expects.

    The nesting is part of the format: tools/decode.py reads scale factors
    from header["imu"], the BME280's per-chip calibration from
    header["baro"]["calibration"], and channel names from header["sen65"].
    Pass each sensor's own config_snapshot(); omit any that is absent.

        build_header(imu=imu.config_snapshot(),
                     baro=baro.config_snapshot(),
                     note="bench run 3")
    """
    header = {}
    if imu is not None:
        header["imu"] = imu
    if baro is not None:
        header["baro"] = baro
    if sen65 is not None:
        header["sen65"] = sen65
    header.update(extra)
    return header


def next_log_path(mount=None, prefix=None, ext=None):
    """Scan the card and return the next unused flight_NNN.bin path."""
    import config
    mount = mount or config.SD_MOUNT
    prefix = prefix or config.LOG_PREFIX
    ext = ext or config.LOG_EXT

    highest = 0
    try:
        for name in os.listdir(mount):
            if name.startswith(prefix) and name.endswith(ext):
                stem = name[len(prefix):-len(ext)]
                try:
                    n = int(stem)
                except ValueError:
                    continue
                if n > highest:
                    highest = n
    except OSError:
        pass
    return "%s/%s%03d%s" % (mount, prefix, highest + 1, ext)
