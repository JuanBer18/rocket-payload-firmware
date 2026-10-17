# core/storage.py -- SD card mount.
#
# machine.SDCard(slot=1) accepts NO pin arguments -- it uses fixed port
# defaults, and this card is wired to IO15/16/17/18 which are not those
# defaults. Probed on hardware:
#
#   SDCard(slot=1, width=1)              constructs, but pins are not settable
#   SDCard(slot=1, clk=/cmd=/d0=)        TypeError: extra keyword arguments
#   SDCard(slot=2, sck=/mosi=/miso=/cs=) constructs, pins honoured
#
# So SPI (slot=2) is the only usable path on this board. This is not a
# preference, it is the only thing that works.

import os

from machine import Pin, SDCard

import config


class SDMountError(Exception):
    pass


_sd = None


def mount(retries=3, mount_point=None):
    """Mount the card. Returns the mount point.

    Retries because a cold card can NAK the first init. The mission cannot
    proceed without logging, so callers are expected to treat failure here as
    fatal rather than continuing without a log.
    """
    global _sd
    mount_point = mount_point or config.SD_MOUNT

    last_error = None
    for attempt in range(retries):
        try:
            _sd = SDCard(slot=config.SD_SPI_SLOT,
                         sck=Pin(config.SD_CLK),
                         mosi=Pin(config.SD_MOSI),
                         miso=Pin(config.SD_MISO),
                         cs=Pin(config.SD_CS))
            os.mount(_sd, mount_point)
            return mount_point
        except Exception as e:            # noqa: BLE001 -- any failure retries
            last_error = e
            try:
                if _sd:
                    _sd.deinit()
            except Exception:             # noqa: BLE001
                pass
            _sd = None
            import time
            time.sleep_ms(200 * (attempt + 1))

    raise SDMountError(
        "SD mount failed after %d attempts on SPI slot %d "
        "(CLK=%d MOSI=%d MISO=%d CS=%d): %s"
        % (retries, config.SD_SPI_SLOT, config.SD_CLK, config.SD_MOSI,
           config.SD_MISO, config.SD_CS, last_error))


def unmount(mount_point=None):
    global _sd
    mount_point = mount_point or config.SD_MOUNT
    try:
        os.umount(mount_point)
    except Exception:                     # noqa: BLE001
        pass
    try:
        if _sd:
            _sd.deinit()
    except Exception:                     # noqa: BLE001
        pass
    _sd = None


def is_mounted(mount_point=None):
    mount_point = mount_point or config.SD_MOUNT
    # The mount-point directory also exists in internal flash. Checking only
    # os.listdir('/sd') can therefore mistake that empty/bare directory for a
    # mounted card and allow a recovery log to spill into flash. _sd is set
    # only after construction and cleared on every failed mount/unmount.
    if _sd is None:
        return False
    try:
        os.listdir(mount_point)
        return True
    except OSError:
        return False


def free_bytes(mount_point=None):
    mount_point = mount_point or config.SD_MOUNT
    if not is_mounted(mount_point):
        raise SDMountError("SD card is not mounted")
    st = os.statvfs(mount_point)
    return st[0] * st[3]      # block size * free blocks


def list_logs(mount_point=None):
    """-> sorted list of (name, size) for log files on the card."""
    mount_point = mount_point or config.SD_MOUNT
    out = []
    if not is_mounted(mount_point):
        return out
    try:
        for name in os.listdir(mount_point):
            if name.startswith(config.LOG_PREFIX) and name.endswith(config.LOG_EXT):
                try:
                    size = os.stat("%s/%s" % (mount_point, name))[6]
                except OSError:
                    size = 0
                out.append((name, size))
    except OSError:
        pass
    return sorted(out)


def list_data_files(mount_point=None):
    """List downloadable binary and CSV mission files."""
    mount_point = mount_point or config.SD_MOUNT
    out = []
    if not is_mounted(mount_point):
        return out
    try:
        for name in os.listdir(mount_point):
            if (name.startswith(config.LOG_PREFIX) and
                    (name.endswith(config.LOG_EXT) or name.endswith(".csv"))):
                try:
                    size = os.stat("%s/%s" % (mount_point, name))[6]
                except OSError:
                    size = 0
                out.append((name, size))
    except OSError:
        pass
    return sorted(out)
