"""Staged MicroPython source updates with boot-time rollback protection."""

import binascii
import hashlib
import json
import os

import config


STAGE_DIR = "/ota_stage"
STAGE_MANIFEST = STAGE_DIR + "/manifest.json"
PENDING_FILE = "/ota_pending.json"
DENIED_TARGETS = ("boot.py", "core/ota.py")
ROOT_TARGETS = ("main.py", "mission.py", "dashboard.py", "config.py",
                "recfmt.py")
TARGET_PREFIXES = ("core/", "drivers/")

_rollback_timer = None


def _read_json(path, default):
    try:
        with open(path, "r") as source:
            return json.load(source)
    except Exception:
        return default


def _write_json(path, value):
    temp = path + ".tmp"
    with open(temp, "w") as target:
        json.dump(value, target)
        target.flush()
    try:
        os.remove(path)
    except OSError:
        pass
    os.rename(temp, path)


def _ensure_stage_dir():
    try:
        os.mkdir(STAGE_DIR)
    except OSError:
        pass


def normalise_target(target):
    if not target:
        raise ValueError("missing destination path")
    target = target.replace("\\", "/").strip().lstrip("/")
    parts = target.split("/")
    if (not target.endswith(".py") or len(target) > 80 or
            any(not part or part in (".", "..") for part in parts)):
        raise ValueError("destination must be a safe relative .py path")
    allowed_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
    if any(any(char not in allowed_chars for char in part) for part in parts):
        raise ValueError("destination contains unsupported characters")
    if target in DENIED_TARGETS:
        raise ValueError("%s can only be changed over UART" % target)
    if target not in ROOT_TARGETS and not target.startswith(TARGET_PREFIXES):
        raise ValueError("allowed destinations are root firmware files, core/*.py, or drivers/*.py")
    return target


def _stage_path(target):
    encoded = binascii.hexlify(target.encode("utf-8")).decode("ascii")
    return "%s/%s.new" % (STAGE_DIR, encoded)


def staged_files():
    entries = _read_json(STAGE_MANIFEST, [])
    return [entry for entry in entries
            if isinstance(entry, dict) and entry.get("target")]


def status():
    return {"staged": staged_files(), "pending": bool(_read_json(PENDING_FILE, None)),
            "max_file_bytes": config.OTA_MAX_FILE_BYTES,
            "max_total_bytes": config.OTA_MAX_TOTAL_BYTES}


def stage_stream(target, source, size):
    target = normalise_target(target)
    size = int(size)
    if size <= 0 or size > config.OTA_MAX_FILE_BYTES:
        raise ValueError("file size must be 1..%d bytes" % config.OTA_MAX_FILE_BYTES)
    entries = [entry for entry in staged_files()
               if entry.get("target") != target]
    if sum(int(entry.get("size", 0)) for entry in entries) + size > config.OTA_MAX_TOTAL_BYTES:
        raise ValueError("staged update exceeds %d bytes" % config.OTA_MAX_TOTAL_BYTES)
    _ensure_stage_dir()
    final_path = _stage_path(target)
    temp_path = final_path + ".tmp"
    digest = hashlib.sha256()
    remaining = size
    try:
        with open(temp_path, "wb") as output:
            while remaining:
                chunk = source.read(min(config.OTA_UPLOAD_CHUNK, remaining))
                if not chunk:
                    raise OSError("upload ended %d bytes early" % remaining)
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            output.flush()
        with open(temp_path, "rb") as uploaded:
            text = uploaded.read().decode("utf-8")
        compile(text, target, "exec")
        try:
            os.remove(final_path)
        except OSError:
            pass
        os.rename(temp_path, final_path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    entry = {"target": target, "stage": final_path, "size": size,
             "sha256": binascii.hexlify(digest.digest()).decode("ascii")}
    entries.append(entry)
    _write_json(STAGE_MANIFEST, entries)
    return entry


def discard_staged():
    entries = staged_files()
    for entry in entries:
        try:
            os.remove(entry["stage"])
        except OSError:
            pass
    try:
        os.remove(STAGE_MANIFEST)
    except OSError:
        pass
    return {"ok": True, "discarded": len(entries)}


def _rollback(pending):
    for entry in reversed(pending.get("files", [])):
        target = entry["target"]
        backup = entry["backup"]
        try:
            if entry.get("had_target"):
                try:
                    os.remove(target)
                except OSError:
                    pass
                os.rename(backup, target)
            else:
                try:
                    os.remove(target)
                except OSError:
                    pass
        except Exception as exc:
            print("OTA rollback error for %s: %r" % (target, exc))
    try:
        os.remove(PENDING_FILE)
    except OSError:
        pass


def activate():
    entries = staged_files()
    if not entries:
        raise ValueError("no staged update files")
    files = []
    for entry in entries:
        target = normalise_target(entry["target"])
        if os.stat(entry["stage"])[6] != int(entry["size"]):
            raise OSError("staged size changed for %s" % target)
        backup = target + ".ota.bak"
        try:
            os.stat(backup)
            raise OSError("stale rollback backup exists: %s" % backup)
        except OSError as exc:
            if "stale rollback" in str(exc):
                raise
        try:
            os.stat(target)
            had_target = True
        except OSError:
            had_target = False
        files.append({"target": target, "stage": entry["stage"],
                      "backup": backup, "had_target": had_target})

    pending = {"state": "activating", "attempted": False, "files": files}
    _write_json(PENDING_FILE, pending)
    try:
        for entry in files:
            if entry["had_target"]:
                os.rename(entry["target"], entry["backup"])
            os.rename(entry["stage"], entry["target"])
        pending["state"] = "pending_boot"
        _write_json(PENDING_FILE, pending)
        try:
            os.remove(STAGE_MANIFEST)
        except OSError:
            pass
    except Exception:
        _rollback(pending)
        raise
    return {"ok": True, "files": [entry["target"] for entry in files],
            "message": "update installed; rebooting with rollback guard"}


def boot_guard():
    """Called by boot.py before main.py; rolls back an unconfirmed update."""
    global _rollback_timer
    pending = _read_json(PENDING_FILE, None)
    if not pending:
        return False
    if pending.get("state") != "pending_boot" or pending.get("attempted"):
        print("OTA update was not confirmed; restoring previous firmware")
        _rollback(pending)
        return False
    pending["attempted"] = True
    _write_json(PENDING_FILE, pending)
    try:
        import machine
        _rollback_timer = machine.Timer(-1)
        _rollback_timer.init(period=config.OTA_BOOT_CONFIRM_MS,
                             mode=machine.Timer.ONE_SHOT,
                             callback=lambda _timer: machine.reset())
    except Exception as exc:
        print("OTA rollback timer unavailable: %r" % (exc,))
    print("OTA update awaiting dashboard boot confirmation")
    return True


def confirm_boot():
    """Confirm that imports and Wi-Fi dashboard startup succeeded."""
    global _rollback_timer
    pending = _read_json(PENDING_FILE, None)
    if not pending:
        return False
    try:
        if _rollback_timer:
            _rollback_timer.deinit()
    except Exception:
        pass
    _rollback_timer = None
    try:
        os.remove(PENDING_FILE)
    except OSError:
        pass
    for entry in pending.get("files", []):
        try:
            os.remove(entry["backup"])
        except OSError:
            pass
    print("OTA update confirmed")
    return True
