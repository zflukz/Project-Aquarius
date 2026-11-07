"""Unit tests for the migration runner's file handling (no database needed)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from aquarius_ingest.migrate import discover, plan  # noqa: E402

REPO_MIGRATIONS = os.path.join(HERE, "..", "..", "db", "migrations")


class TestDiscover(unittest.TestCase):
    def _dir(self, names):
        d = tempfile.mkdtemp()
        for n, body in names.items():
            with open(os.path.join(d, n), "w") as f:
                f.write(body)
        return d

    def test_sorted_by_version_and_other_files_ignored(self):
        d = self._dir({"0002_b.sql": "B", "0001_a.sql": "A", "README.md": "x"})
        self.assertEqual([m.version for m in discover(d)], ["0001", "0002"])

    def test_bad_name_and_duplicate_version_are_errors(self):
        with self.assertRaises(ValueError):
            discover(self._dir({"1_a.sql": ""}))
        with self.assertRaises(ValueError):
            discover(self._dir({"0001_a.sql": "", "0001_b.sql": ""}))

    def test_plan_pending_and_changed(self):
        d = self._dir({"0001_a.sql": "A", "0002_b.sql": "B", "0003_c.sql": "C"})
        found = discover(d)
        applied = {"0001": found[0].checksum, "0002": "edited-later"}
        pending, changed = plan(found, applied)
        self.assertEqual([m.version for m in pending], ["0003"])
        self.assertEqual([m.version for m in changed], ["0002"])

    def test_repo_migrations_are_well_formed(self):
        found = discover(REPO_MIGRATIONS)
        self.assertGreaterEqual(len(found), 5)
        self.assertEqual(found[0].name, "phase_b_tables")
        self.assertEqual([m.version for m in found], [f"{i:04d}" for i in range(1, len(found) + 1)])


if __name__ == "__main__":
    unittest.main()
