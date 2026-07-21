"""Transitional re-export shim: the real module was promoted to
pathfinder.s3store (microvm-removal refactor, Task 1). This shim exists only
so not-yet-migrated modules still under pathfinder/sandbox/ (deleted wholesale
in a later task) keep importing without being touched ahead of that deletion.
"""
from __future__ import annotations
from pathfinder.s3store import S3Store, S3StoreLike

__all__ = ["S3Store", "S3StoreLike"]
