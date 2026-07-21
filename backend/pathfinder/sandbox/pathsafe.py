"""Transitional re-export shim: the real module was promoted to
pathfinder.pathsafe (microvm-removal refactor, Task 1). This shim exists only
so not-yet-migrated modules still under pathfinder/sandbox/ (deleted wholesale
in a later task) keep importing without being touched ahead of that deletion.
"""
from __future__ import annotations
from pathfinder.pathsafe import reject_unsafe

__all__ = ["reject_unsafe"]
