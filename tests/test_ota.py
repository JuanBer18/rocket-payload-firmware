import io
import os
from pathlib import Path
import tempfile
import unittest

from core import ota


class OtaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_cwd = os.getcwd()
        os.chdir(self.root)
        self.old_paths = (ota.STAGE_DIR, ota.STAGE_MANIFEST, ota.PENDING_FILE)
        ota.STAGE_DIR = str(self.root / "ota_stage")
        ota.STAGE_MANIFEST = str(self.root / "ota_stage" / "manifest.json")
        ota.PENDING_FILE = str(self.root / "ota_pending.json")

    def tearDown(self):
        ota.STAGE_DIR, ota.STAGE_MANIFEST, ota.PENDING_FILE = self.old_paths
        os.chdir(self.old_cwd)
        self.temp.cleanup()

    def test_stages_syntax_checked_source_and_can_rollback(self):
        Path("main.py").write_text("VERSION = 'old'\n", encoding="utf-8")
        source = b"VERSION = 'new'\n"
        entry = ota.stage_stream("main.py", io.BytesIO(source), len(source))
        self.assertEqual(entry["target"], "main.py")
        result = ota.activate()
        self.assertTrue(result["ok"])
        self.assertIn("new", Path("main.py").read_text(encoding="utf-8"))
        pending = ota._read_json(ota.PENDING_FILE, None)
        ota._rollback(pending)
        self.assertIn("old", Path("main.py").read_text(encoding="utf-8"))

    def test_rejects_bad_syntax_and_protected_boot_files(self):
        broken = b"def broken(:\n"
        with self.assertRaises(SyntaxError):
            ota.stage_stream("main.py", io.BytesIO(broken), len(broken))
        with self.assertRaises(ValueError):
            ota.normalise_target("boot.py")
        with self.assertRaises(ValueError):
            ota.normalise_target("../main.py")


if __name__ == "__main__":
    unittest.main()
