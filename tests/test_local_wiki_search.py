#!/usr/bin/env python3
"""Round-trip test for the local reference index used by the wiki route.

This exercises the real pair of components the bridge uses for offline lookups:
``scripts/build_local_wiki_index.py`` builds the SQLite FTS5 index and
``bridge/local_wiki.py`` searches it. Both are standard library only, so this
runs anywhere, with no model, radio or network.

Run from the repository root:
    python3 -m unittest discover -s tests -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (str(ROOT), str(ROOT / "bridge")):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import local_wiki  # noqa: E402  (path set up above)

BUILDER = ROOT / "scripts" / "build_local_wiki_index.py"

ENTRIES = [
    {
        "title": "Water purification",
        "text": "Boiling is the most reliable way to make water safe to drink.",
        "url": "https://en.wikipedia.org/wiki/Water_purification",
    },
    {
        "title": "Heat exhaustion",
        "text": "Warning signs include heavy sweating, clammy skin and dizziness. Move the person somewhere cooler.",
    },
    {
        "title": "Solar power",
        "text": "Photovoltaic panels convert sunlight into electricity. Size the battery for the winter duty cycle.",
        "url": "https://en.wikipedia.org/wiki/Solar_power",
    },
]


class LocalWikiSearchTests(unittest.TestCase):
    """Build an index, then search it the way the bridge does."""

    @classmethod
    def setUpClass(cls):
        cls._tempdir = tempfile.TemporaryDirectory()
        folder = Path(cls._tempdir.name)
        cls.index = folder / "wiki.sqlite3"
        source = folder / "wiki.jsonl"
        source.write_text(
            "\n".join(json.dumps(entry) for entry in ENTRIES) + "\n", encoding="utf-8"
        )
        cls.build = subprocess.run(
            [sys.executable, str(BUILDER), str(source), str(cls.index)],
            capture_output=True,
            text=True,
            check=False,
        )

    @classmethod
    def tearDownClass(cls):
        cls._tempdir.cleanup()

    def test_builder_succeeds(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr)
        self.assertTrue(self.index.is_file())

    def test_search_returns_labelled_source_and_title(self):
        text = local_wiki.search("water purification", index_path=self.index)
        self.assertIn("SOURCE: Local Wikipedia index", text)
        self.assertIn("TITLE: Water purification", text)
        self.assertIn("URL: https://en.wikipedia.org/wiki/Water_purification", text)
        self.assertIn("Boiling", text)

    def test_search_finds_the_right_entry(self):
        text = local_wiki.search("clammy dizziness", index_path=self.index)
        self.assertIn("TITLE: Heat exhaustion", text)
        self.assertNotIn("TITLE: Solar power", text)

    def test_terms_are_and_ed(self):
        # "solar boiling" matches no single entry, so the search must come back empty.
        self.assertEqual(local_wiki.search("solar boiling", index_path=self.index), "")

    def test_miss_returns_empty_string(self):
        self.assertEqual(local_wiki.search("quantum chromodynamics", index_path=self.index), "")

    def test_empty_query_returns_empty_string(self):
        self.assertEqual(local_wiki.search("   ", index_path=self.index), "")

    def test_punctuation_is_not_treated_as_fts_syntax(self):
        # Unquoted FTS5 would raise on these; the helper must survive them.
        for query in ('"water"', "water:", "water AND", "purification*", "water, solar"):
            self.assertIsInstance(local_wiki.search(query, index_path=self.index), str, query)

    def test_missing_index_returns_empty_string(self):
        self.assertEqual(local_wiki.search("water", index_path=self.index.parent / "nope.sqlite3"), "")

    def test_limit_is_respected(self):
        text = local_wiki.search("the", index_path=self.index, limit=1)
        self.assertEqual(text.count("TITLE:"), 1)

    def test_output_is_capped_for_radio_use(self):
        text = local_wiki.search("water", index_path=self.index, limit=3)
        self.assertLessEqual(len(text), 3500)


class BuilderFailureTests(unittest.TestCase):
    """Bad input must fail loudly rather than produce a half-built index."""

    def test_invalid_json_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / "broken.jsonl"
            source.write_text('{"title": "ok", "text": "fine"}\nnot json\n', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(BUILDER), str(source), str(folder / "out.sqlite3")],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid JSON", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
