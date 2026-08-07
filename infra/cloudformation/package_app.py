#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import zipfile

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parents[1]
EXCLUDES_FILE = HERE.parent / "app-asset-excludes.json"
FIXED_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    result = ""
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            result += "(?:.*/)?"
            index += 3
        elif pattern.startswith("**", index):
            result += ".*"
            index += 2
        elif pattern[index] == "*":
            result += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            result += "[^/]"
            index += 1
        else:
            result += re.escape(pattern[index])
            index += 1
    return re.compile(f"^{result}$")


def load_excludes() -> list[re.Pattern[str]]:
    patterns = json.loads(EXCLUDES_FILE.read_text(encoding="utf-8"))
    return [_glob_regex(pattern) for pattern in patterns]


def is_excluded(relative_path: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.fullmatch(relative_path) for pattern in patterns)


def archive_files(root: Path, output: Path) -> list[Path]:
    root = root.resolve()
    output = output.resolve()
    patterns = load_excludes()
    files: list[Path] = []
    for current, directory_names, file_names in os.walk(root, topdown=True):
        current_path = Path(current)
        kept_directories = []
        for name in sorted(directory_names):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.resolve() == output or is_excluded(relative, patterns):
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.resolve() == output or is_excluded(relative, patterns):
                continue
            files.append(candidate)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_archive(root: Path, output: Path) -> str:
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in archive_files(root, output):
            relative = source.relative_to(root).as_posix()
            mode = source.stat().st_mode
            permissions = 0o755 if mode & stat.S_IXUSR else 0o644
            info = zipfile.ZipInfo(relative, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | permissions) << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Pathfinder EC2 application ZIP")
    parser.add_argument("output", type=Path, help="output ZIP path (prefer a path outside the repository)")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    digest = build_archive(args.root, args.output)
    print(digest)


if __name__ == "__main__":
    main()
