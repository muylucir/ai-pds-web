from __future__ import annotations

import errno
import logging
import os
import stat
from pathlib import Path, PurePosixPath

_log = logging.getLogger(__name__)

def reject_unsafe(path: str) -> None:
    """Raise ValueError if `path` could escape the workspace root.

    Reject any path that is absolute (leading "/") or contains a ".." path
    segment. Wildcards ("*", "**", "?") are ordinary, non-".." segments and
    pass, so legitimate globs like "aiplc-docs/*-questions.md" are accepted.
    Used by AgentRunner before it forwards any path/glob to S3 or the local
    workspace.
    """
    if path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise ValueError(f"unsafe path: {path}")


def workspace_relative(path: str, workspace: str) -> str | None:
    """Make a tool's file_path workspace-relative; return None on escape.

    The two functions above *raise* because their callers are our own routes,
    where an unsafe path is a bug. This one *returns None* because its callers
    are tool/hook callbacks reacting to a path the **model** chose — an escape
    there is ordinary model behavior to be reported, not an exception to
    propagate through the SDK's callback machinery.

    `relative_to` also raises ValueError when `path` is absolute but shares no
    prefix with `workspace` at all (e.g. "/etc/passwd" vs workspace
    "/workspace") -- not just for genuinely relative inputs. A naive fallback
    (`path.lstrip("/")`) would treat both cases as "already relative", letting
    an unrelated absolute path escape undetected. Only fall back to the lstrip
    path when `path` was not absolute to begin with; an absolute path that
    isn't under the workspace is always an escape.

    Lives here rather than in either caller because it had already been copied
    twice (VM-era claude_driver -> proto/builder -> agent/claude_driver), and
    agent/discovery_guard needed a third. Three copies of an escape check is
    three chances for one of them to drift into a hole.
    """
    ws = PurePosixPath(workspace)
    p = PurePosixPath(path)
    try:
        rel = p.relative_to(ws)
    except ValueError:
        if path.startswith("/"):
            return None
        rel = PurePosixPath(path.lstrip("/"))
    rel_str = str(rel)
    if ".." in rel.parts or rel_str.startswith("/"):
        return None
    return rel_str


def reject_unsafe_segment(value: str) -> None:
    """Raise ValueError unless `value` is exactly ONE ordinary path segment.

    Strictly stronger than `reject_unsafe`, and the difference is load-bearing
    wherever a single URL path parameter becomes one directory name. `""` and
    `"."` both PASS reject_unsafe -- `PurePosixPath` reduces them to no parts
    at all -- yet `root / pid / slug` then collapses onto `root / pid`, so a
    caller that deletes "the slug's directory" deletes every SIBLING slug
    instead. `".."` climbs one further and takes every project with it.
    Multi-segment values ("a/b") are rejected for the same reason: one
    parameter must not silently address a nested path.

    Ordinary names with a ".." substring inside them ("..foo") are fine --
    same boundary reject_unsafe draws, since they are one real segment.
    """
    reject_unsafe(value)
    if PurePosixPath(value).parts != (value,):
        raise ValueError(f"unsafe path segment: {value!r}")


# ---- When the backend touches trees the agent can write to ----
#
# Workspace and build trees are shared by the agent inside the sandbox (a different uid)
# and the backend. The agent can create symbolic links inside them, and while link
# targets are not visible inside the sandbox, **the backend resolves links in its own
# mount namespace.** If `aiplc-docs` is replaced with a link pointing outside, the next
# restore writes S3 contents to the link target as the backend uid (arbitrary file
# write), and the next sync reads the link target and uploads it to the source-of-truth copy
# (exfiltration of outside files).
#
# So these functions open each component from the root, one at a time, with
# `O_NOFOLLOW`. Check-then-open (lstat → open) is not used — the agent could swap a
# component in between (the Discovery CLI stays up between turns, and processes launched
# via Bash run in the same unit). Each next component is opened while holding the fd of
# the directory already opened, so even if the path changes, what has already been
# opened does not.
#
# - Read: on encountering a link, OSError(ELOOP). If not a regular file (a FIFO, etc.),
#   OSError(EINVAL) — opening a FIFO for reading would hang the backend thread forever.
# - Write: on encountering a link, **remove the link and create a real directory/file.**
#   What is written is canonical (S3, or content the backend decided), so taking over
#   the link's place is correct. In the normal flow nothing creates links in these trees
#   (`node_modules/.bin` gets no special handling — the backend neither writes nor reads
#   there).
#
# `root` itself is followed — it is a path the backend decided, and in the sandbox it is
# a bind mount point, so the agent cannot change it.

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _parts(rel: str | PurePosixPath) -> tuple[str, ...]:
    text = str(rel)
    reject_unsafe(text)
    parts = tuple(part for part in PurePosixPath(text).parts if part != ".")
    if not parts:
        raise ValueError(f"empty path: {text!r}")
    return parts


def _open_dirs(root: Path, names: tuple[str, ...], *, create: bool) -> int:
    """Directory fd of `root/names…`. Links are not followed for any component.

    With `create`, missing components are created, and components that are links are
    removed and replaced with real directories.
    """
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in names:
            try:
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, dir_fd=fd)
                except FileExistsError:
                    pass  # created in between — the open below decides what it is
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                # For a link, O_DIRECTORY|O_NOFOLLOW makes the kernel return ENOTDIR
                # (not ELOOP).
                if not create or exc.errno not in (errno.ELOOP, errno.ENOTDIR):
                    raise
                if not stat.S_ISLNK(os.stat(name, dir_fd=fd,
                                            follow_symlinks=False).st_mode):
                    raise
                _log.warning("replacing a symlink with a directory: %s/%s",
                             root, name)
                os.unlink(name, dir_fd=fd)
                os.mkdir(name, dir_fd=fd)
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _require_regular(fd: int, root: Path, rel: str | PurePosixPath) -> os.stat_result:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise OSError(errno.EINVAL, f"not a regular file: {root}/{rel}")
    return st


def read_bytes_beneath(root: Path, rel: str | PurePosixPath) -> bytes:
    """Bytes of `root/rel`. OSError if there is a link anywhere on the path or it is not
    a regular file."""
    parts = _parts(rel)
    dfd = _open_dirs(Path(root), parts[:-1], create=False)
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, dir_fd=dfd)
    finally:
        os.close(dfd)
    with os.fdopen(fd, "rb") as f:
        _require_regular(fd, root, rel)
        return f.read()


def read_text_beneath(root: Path, rel: str | PurePosixPath,
                      errors: str = "strict") -> str:
    return read_bytes_beneath(root, rel).decode("utf-8", errors=errors)


def stat_beneath(root: Path, rel: str | PurePosixPath) -> os.stat_result | None:
    """The stat of `root/rel` if it is a regular file reachable without links, otherwise
    None. Never raises."""
    try:
        parts = _parts(rel)
        dfd = _open_dirs(Path(root), parts[:-1], create=False)
    except (OSError, ValueError):
        return None
    try:
        st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
    except OSError:
        return None
    finally:
        os.close(dfd)
    return st if stat.S_ISREG(st.st_mode) else None


def write_beneath(root: Path, rel: str | PurePosixPath, data: bytes | str, *,
                  append: bool = False, mtime_ns: int | None = None) -> None:
    """Write to `root/rel`. Missing directories are created, and components that are
    links are replaced with real ones.

    If `mtime_ns` is given, the mtime is set to that time after writing (what
    `shutil.copy2` used to do).
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    parts = _parts(rel)
    flags = (os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
             | (os.O_APPEND if append else os.O_TRUNC))
    dfd = _open_dirs(Path(root), parts[:-1], create=True)
    try:
        try:
            fd = os.open(parts[-1], flags, 0o666, dir_fd=dfd)
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
            _log.warning("replacing a symlink with a file: %s/%s", root, rel)
            os.unlink(parts[-1], dir_fd=dfd)
            fd = os.open(parts[-1], flags | os.O_EXCL, 0o666, dir_fd=dfd)
    finally:
        os.close(dfd)
    with os.fdopen(fd, "ab" if append else "wb") as f:
        _require_regular(fd, root, rel)
        f.write(data)
        if mtime_ns is not None:
            f.flush()
            os.utime(fd, ns=(mtime_ns, mtime_ns))


def unlink_beneath(root: Path, rel: str | PurePosixPath) -> bool:
    """Remove `root/rel` (only the link, if it is a link). True if removed. False if
    missing or a parent component is a link.

    If a parent component is a link, nothing is removed — what lies beyond that link is
    not this tree.
    """
    try:
        parts = _parts(rel)
        dfd = _open_dirs(Path(root), parts[:-1], create=False)
    except (OSError, ValueError):
        return False
    try:
        st = os.stat(parts[-1], dir_fd=dfd, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            return False
        os.unlink(parts[-1], dir_fd=dfd)
        return True
    except OSError:
        return False
    finally:
        os.close(dfd)
