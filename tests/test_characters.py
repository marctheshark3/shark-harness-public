"""Character contributions are data; the controller owns state and actions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from characters import load_character, parse_character, render_character, selected_state


class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((Path(__file__).resolve().parents[1] /
                                  "characters/reef.json").read_text())

    def test_selected_session_drives_state_instead_of_the_busiest_thread(self):
        snapshot = {"connected": True, "state": "error", "threads": [
            {"id": "selected", "state": "working"}, {"id": "other", "state": "error"}]}
        self.assertEqual(selected_state(snapshot, {"id": "selected"}), "working")
        snapshot["threads"][0]["state"] = "waiting"
        self.assertEqual(selected_state(snapshot, {"id": "selected"}), "waiting")
        self.assertEqual(selected_state(snapshot, {"id": "remote", "browser": True}), "unknown")
        self.assertEqual(selected_state(snapshot, {"id": "voice-selection", "telemetryThreadId": "selected"}), "waiting")
        self.assertEqual(selected_state(snapshot, {"new": True}), "idle")
        snapshot["connected"] = False
        self.assertEqual(selected_state(snapshot, {"id": "selected"}), "offline")

    def test_bundled_characters_render_distinct_original_silhouettes_and_states(self):
        silhouettes = set()
        for name in ("reef", "manta", "octo"):
            character = load_character(name)
            silhouettes.add(render_character(character, "idle", 0).tobytes())
            working = render_character(character, "working", 0)
            self.assertEqual(working.size, (272, 272))
            self.assertEqual(working.mode, "RGBA")
            self.assertEqual(working.getpixel((0, 0))[3], 0)
            self.assertNotEqual(working.tobytes(), render_character(character, "working", 2).tobytes())
            self.assertNotEqual(working.tobytes(), render_character(character, "waiting", 0).tobytes())
        self.assertEqual(len(silhouettes), 3)

    def test_custom_pack_loads_without_importing_or_opening_its_references(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "my-character.json"
            path.write_text(json.dumps(self.source))
            self.assertEqual(load_character(str(path)).id, "reef")

    def test_rejects_code_urls_unbounded_geometry_and_invalid_metadata(self):
        changes = [
            {"script": "do_something()"}, {"version": 2}, {"id": "../reef"},
            {"license": "unknown"}, {"name": "x" * 49},
            {"palette": {**self.source["palette"], "body": "url(https://example.invalid)"}},
            {"eyes": [[float("nan"), 50], [60, 50]]},
            {"eyes": [[10**400, 50], [60, 50]]},
            {"shapes": [{"kind": "image", "url": "file:///private"}]},
            {"shapes": self.source["shapes"] * 30},
            {"shapes": [{"kind": "blob", "points": [[-1, 50], [20, 20], [30, 40]], "fill": "body"}]},
        ]
        for change in changes:
            with self.subTest(change=change):
                value = copy.deepcopy(self.source)
                value.update(change)
                with self.assertRaises(ValueError):
                    parse_character(value)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "huge.json"
            path.write_text(" " * 32769)
            with self.assertRaisesRegex(ValueError, "32 KiB"):
                load_character(str(path))

    def test_rejects_unknown_live_states_instead_of_implying_success(self):
        snapshot = {"connected": True, "threads": [{"id": "one", "state": "mystery"}]}
        self.assertEqual(selected_state(snapshot, {"id": "one"}), "unknown")


if __name__ == "__main__":
    unittest.main()
