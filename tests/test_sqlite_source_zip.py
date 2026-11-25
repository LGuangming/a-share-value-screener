import io
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from sqlite_source_zip import extract_sqlite_source, REQUIRED_SCRIPTS

ROOT = "sqlite-src-3540100"

def archive(extra=()):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name in REQUIRED_SCRIPTS:
            # Simulate ZIP archives without executable permission metadata.
            z.writestr(ROOT + "/" + name, "#!/bin/sh\nexit 0\n")
        for name, mode, content in extra:
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = mode << 16
            z.writestr(entry, content)
    data.seek(0)
    return data

class ExtractTests(unittest.TestCase):
    def test_restore_only_safe_modes(self):
        with tempfile.TemporaryDirectory() as d:
            root, executable = extract_sqlite_source(archive([
                (ROOT+"/tool/helper", stat.S_IFREG|0o4777, "helper"),
                (ROOT+"/source.c", stat.S_IFREG|0o666, "source"),
            ]), d)
            for name in REQUIRED_SCRIPTS + ("tool/helper",):
                self.assertEqual((root/name).stat().st_mode & 0o7777, 0o755)
                self.assertIn(name, executable)
            self.assertEqual((root/"source.c").stat().st_mode & 0o7777, 0o644)
    def test_reject_traversal_before_extraction(self):
        for name in ("../escape", ROOT+"/../../escape", "/tmp/escape", ROOT+"/bad\\path"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError):
                    extract_sqlite_source(archive([(name, stat.S_IFREG|0o644, "bad")]), d)
                self.assertEqual(list(Path(d).iterdir()), [])
    def test_reject_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                extract_sqlite_source(archive([(ROOT+"/link", stat.S_IFLNK|0o777, "/tmp")]), d)
            self.assertEqual(list(Path(d).iterdir()), [])
    def test_reject_existing_root(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/ROOT).mkdir()
            with self.assertRaises(ValueError):
                extract_sqlite_source(archive(), d)

if __name__ == "__main__":
    unittest.main()
