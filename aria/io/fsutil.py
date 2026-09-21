"""File system helpers: checksums, atomic writes and safe copies.

Every write that matters goes through :func:`atomic_write_bytes` or
:func:`atomic_replace`. They write to a temporary file in the same directory,
flush, force the data to the device and then rename into place. A rename within
one file system is atomic, so a power loss leaves either the previous file or
the new one, never a half written file. That property is what the autosave and
the journal depend on (FR 015, NFR 009).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

CHUNK = 1024 * 1024


def sha256_file(path, chunk: int = CHUNK) -> str:
    """Cryptographic checksum linking a working copy to its original (FR 002)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fsync_directory(path) -> None:
    """Force a directory entry to disk so a rename survives power loss."""
    if os.name == "nt":
        # Windows does not allow opening a directory handle this way, and NTFS
        # commits the rename with the file data, so this is a no operation.
        return
    fd = os.open(str(path), os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path, data: bytes) -> None:
    """Write bytes so that the destination is never partially written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".aria_tmp_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(target))
        fsync_directory(target.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


@contextmanager
def atomic_replace(path, mode: str = "wb"):
    """Context manager yielding a handle whose content replaces ``path`` on exit."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".aria_tmp_")
    handle = os.fdopen(fd, mode)
    try:
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(tmp, str(target))
        fsync_directory(target.parent)
    except BaseException:
        try:
            handle.close()
        except Exception:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def copy_preserving(src, dst) -> str:
    """Copy a file and verify the copy by checksum.

    The original is opened read only and is never modified (FR 002). The copy is
    verified rather than assumed, because a silent bad copy would break the link
    between annotations and the pixels they describe.
    """
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    source_digest = sha256_file(src_path)

    fd, tmp = tempfile.mkstemp(dir=str(dst_path.parent), prefix=".aria_tmp_")
    try:
        # Copy through the write handle so the data can be forced to the device
        # before the checksum is taken. fsync needs a writable descriptor, which
        # is why the copy is not delegated to shutil.
        with os.fdopen(fd, "wb") as out, open(src_path, "rb") as src:
            shutil.copyfileobj(src, out, CHUNK)
            out.flush()
            os.fsync(out.fileno())
        copy_digest = sha256_file(tmp)
        if copy_digest != source_digest:
            raise OSError(
                "The copied file does not match the source checksum, so the copy "
                "was discarded. Check the storage device."
            )
        os.replace(tmp, str(dst_path))
        fsync_directory(dst_path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return source_digest


def safe_filename(name: str, fallback: str = "file") -> str:
    """Reduce a name to characters that are legal on Windows and macOS."""
    cleaned = "".join(c if c.isalnum() or c in "._- " else "_" for c in str(name)).strip()
    cleaned = cleaned.strip(". ")
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not cleaned or cleaned.upper().split(".")[0] in reserved:
        cleaned = fallback
    return cleaned[:120]


def directory_size(path) -> int:
    total = 0
    for root, _dirs, files in os.walk(str(path)):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def ensure_writable(path) -> tuple:
    """Check that a directory exists and can be written to.

    Returns ``(ok, message)``. Used by the first run compatibility check and by
    the storage preference page.
    """
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"The folder could not be created: {exc}"
    probe = target / ".aria_write_probe"
    try:
        with open(probe, "wb") as fh:
            fh.write(b"probe")
            fh.flush()
            os.fsync(fh.fileno())
        probe.unlink()
    except OSError as exc:
        return False, f"The folder is not writable: {exc}"
    return True, "The folder exists and is writable."
