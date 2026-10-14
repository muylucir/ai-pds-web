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


# ---- 에이전트가 쓸 수 있는 트리를 백엔드가 만질 때 ----
#
# 워크스페이스와 빌드 트리는 샌드박스 안의 에이전트(다른 uid)와 백엔드가 함께 쓴다.
# 에이전트는 그 안에 심볼릭 링크를 만들 수 있고, 샌드박스 안에서는 링크 대상이 보이지
# 않지만 **백엔드는 자기 마운트 네임스페이스에서 링크를 푼다.** `aiplc-docs`를 바깥을
# 가리키는 링크로 바꿔 두면, 다음 복원이 S3 본문을 백엔드 uid로 링크 대상에 쓰고(임의
# 파일 쓰기), 다음 sync가 링크 대상을 읽어 정본에 올린다(바깥 파일 유출).
#
# 그래서 이 함수들은 루트부터 한 조각씩 `O_NOFOLLOW`로 연다. 검사한 뒤 여는 방식
# (lstat → open)은 쓰지 않는다 — 그 사이에 에이전트가 조각을 바꿔 끼울 수 있다(Discovery
# CLI는 턴 사이에도 떠 있고, Bash로 띄운 프로세스가 같은 unit에서 돈다). 연 디렉터리
# fd를 쥔 채 다음 조각을 여므로 경로가 바뀌어도 이미 연 것은 바뀌지 않는다.
#
# - 읽기: 링크를 만나면 OSError(ELOOP). 정규 파일이 아니면(FIFO 등) OSError(EINVAL) —
#   FIFO를 읽으려고 열면 백엔드 스레드가 영원히 멈춘다.
# - 쓰기: 링크를 만나면 **링크를 지우고 실제 디렉터리·파일을 만든다.** 쓰는 것은 정본
#   (S3, 또는 백엔드가 정한 내용)이므로 링크 자리를 차지하는 것이 맞다. 정상 흐름에서
#   이 트리들에 링크가 생기는 곳은 없다(`node_modules/.bin`은 따로 다루지 않는다 —
#   백엔드가 거기를 쓰거나 읽지 않는다).
#
# `root` 자체는 따라간다 — 백엔드가 정한 경로이고, 샌드박스에서는 bind 마운트 지점이라
# 에이전트가 바꿀 수 없다.

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _parts(rel: str | PurePosixPath) -> tuple[str, ...]:
    text = str(rel)
    reject_unsafe(text)
    parts = tuple(part for part in PurePosixPath(text).parts if part != ".")
    if not parts:
        raise ValueError(f"empty path: {text!r}")
    return parts


def _open_dirs(root: Path, names: tuple[str, ...], *, create: bool) -> int:
    """`root/names…`의 디렉터리 fd. 조각마다 링크를 따라가지 않는다.

    `create`면 없는 조각을 만들고, 링크인 조각은 지운 뒤 실제 디렉터리로 바꾼다.
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
                    pass  # 그 사이에 생겼다 — 아래 open이 무엇인지 판정한다
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                # 링크에 O_DIRECTORY|O_NOFOLLOW면 커널이 ENOTDIR을 준다(ELOOP가 아니다).
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
    """`root/rel`의 바이트. 경로 어디든 링크가 있거나 정규 파일이 아니면 OSError."""
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
    """`root/rel`이 링크 없이 닿는 정규 파일이면 그 stat, 아니면 None. 예외를 던지지 않는다."""
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
    """`root/rel`에 쓴다. 없는 디렉터리는 만들고, 링크인 조각은 실제 것으로 바꾼다.

    `mtime_ns`를 주면 쓴 뒤 그 시각으로 맞춘다(`shutil.copy2`가 하던 일).
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
    """`root/rel`을 지운다(링크면 링크만). 지웠으면 True. 없거나 상위 조각이 링크면 False.

    상위 조각이 링크면 아무것도 지우지 않는다 — 그 링크 너머는 이 트리가 아니다.
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
