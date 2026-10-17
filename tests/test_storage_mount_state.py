import sys
import types
import unittest
from unittest import mock


machine = sys.modules.get("machine") or types.ModuleType("machine")
machine.Pin = getattr(machine, "Pin", object)
machine.SDCard = getattr(machine, "SDCard", object)
sys.modules.setdefault("machine", machine)

from core import storage


class StorageMountStateTests(unittest.TestCase):
    def test_bare_flash_mount_directory_is_not_an_sd_card(self):
        previous = storage._sd
        storage._sd = None
        try:
            with mock.patch.object(storage.os, "listdir", return_value=[]):
                self.assertFalse(storage.is_mounted())
        finally:
            storage._sd = previous

    def test_live_sd_object_and_directory_are_required(self):
        previous = storage._sd
        storage._sd = object()
        try:
            with mock.patch.object(storage.os, "listdir", return_value=[]):
                self.assertTrue(storage.is_mounted())
            with mock.patch.object(storage.os, "listdir", side_effect=OSError()):
                self.assertFalse(storage.is_mounted())
        finally:
            storage._sd = previous


if __name__ == "__main__":
    unittest.main()
