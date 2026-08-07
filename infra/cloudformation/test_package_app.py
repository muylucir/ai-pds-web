#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path
import zipfile

import package_app


class PackageAppTest(unittest.TestCase):
    def test_globs_match_top_level_and_nested_paths(self) -> None:
        patterns = package_app.load_excludes()
        excluded = [
            ".git", "infra", "docs", ".claude",
            "frontend/node_modules", "frontend/.next", "backend/.venv",
            "backend/pathfinder/__pycache__", "backend/example.egg-info",
            "frontend/.env.local", "files/screenshot.png",
            "proto-config/projects", "discovery-config/sessions",
        ]
        for path in excluded:
            self.assertTrue(package_app.is_excluded(path, patterns), path)
        for path in [
            "README.md", "backend/pathfinder/app.py", "frontend/package.json",
            "proto-config/CLAUDE.md", "files/nested/screenshot.png",
        ]:
            self.assertFalse(package_app.is_excluded(path, patterns), path)

    def test_real_archive_has_runtime_files_and_no_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "app.zip"
            first = package_app.build_archive(package_app.DEFAULT_ROOT, output)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
            for required in [
                "backend/pathfinder/app.py",
                "frontend/package.json",
                "discovery-config/CLAUDE.md",
                "proto-config/CLAUDE.md",
                "rule/aiplc-rules/language/ko.md",
                "rule/aiplc-rules/language/en.md",
            ]:
                self.assertIn(required, names)
            forbidden_parts = {".git", "infra", "docs", ".claude", "node_modules", ".venv", ".next", "__pycache__"}
            for name in names:
                self.assertTrue(forbidden_parts.isdisjoint(Path(name).parts), name)
                self.assertFalse(name.startswith("proto-config/projects/"), name)
                self.assertFalse(name.startswith("proto-config/sessions/"), name)
                self.assertFalse(name.startswith("discovery-config/projects/"), name)
                self.assertFalse(name.startswith("discovery-config/sessions/"), name)
            second = package_app.build_archive(package_app.DEFAULT_ROOT, output)
            self.assertEqual(first, second, "same working tree must produce the same content-addressed ZIP")


if __name__ == "__main__":
    unittest.main()
