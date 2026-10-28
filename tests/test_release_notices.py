"""Firmware artifact notices must preserve upstream text and match their manifest."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "collect_licenses", Path(__file__).resolve().parents[1] / "firmware/collect_licenses.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class ReleaseNoticesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.idf, self.managed, self.output = (self.root / name for name in ("idf", "managed", "output"))
        self.idf.mkdir()
        self.component = self.managed / "example__driver"
        self.component.mkdir(parents=True)
        (self.idf / "LICENSE").write_text("ESP-IDF license\n")
        (self.component / "LICENSE.txt").write_text("Driver license\n")

    def test_preserves_nested_notices_and_hashes_without_copying_sources(self):
        nested = self.idf / "components/example/LICENSES"
        nested.mkdir(parents=True)
        (nested / "MIT.txt").write_text("Nested license\n")
        (self.component / "driver.c").write_text("Not a standalone notice\n")
        count = collector.collect(self.idf, self.managed, self.output, image="pinned-image")
        self.assertEqual(count, 3)
        manifest = json.loads((self.output / "MANIFEST.json").read_text())
        self.assertEqual(manifest["idfImage"], "pinned-image")
        for row in manifest["files"]:
            self.assertEqual(hashlib.sha256((self.output / row["path"]).read_bytes()).hexdigest(), row["sha256"])
        self.assertEqual((self.output / "managed_components/example__driver/LICENSE.txt").read_text(), "Driver license\n")
        self.assertFalse(list(self.output.rglob("driver.c")))
        (self.output / "stale-license.txt").write_text("Old build\n")
        collector.collect(self.idf, self.managed, self.output)
        self.assertFalse((self.output / "stale-license.txt").exists())

    def test_missing_component_notice_fails_before_replacing_existing_output(self):
        collector.collect(self.idf, self.managed, self.output)
        before = (self.output / "MANIFEST.json").read_bytes()
        (self.component / "LICENSE.txt").unlink()
        with self.assertRaisesRegex(ValueError, "No upstream license"):
            collector.collect(self.idf, self.managed, self.output)
        self.assertEqual((self.output / "MANIFEST.json").read_bytes(), before)

    def test_rejects_notice_links_outside_dependency_tree(self):
        private = self.root / "private.txt"
        private.write_text("Must not be packaged\n")
        (self.component / "NOTICE").symlink_to(private)
        with self.assertRaisesRegex(ValueError, "symlink leaves"):
            collector.collect(self.idf, self.managed, self.output)

    def test_rejects_output_overlapping_inputs(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            collector.collect(self.idf, self.managed, self.idf / "licenses")
        self.assertTrue((self.idf / "LICENSE").is_file())


if __name__ == "__main__":
    unittest.main()
