"""What is on disk for the reader, and whether it can be trusted.

Layout, under :func:`agent.runtime.platforms.data_home`::

    files/{bundle}/{file}          verified downloads, and files placed by hand
    files/{bundle}/{file}.part     an unfinished download
    runtime/{bundle}/              an unpacked engine archive
    verified.json                  size, mtime and sha256 of each verified file
    reader.lock  reader.pid        the one reader process on this machine
    logs/reader.log                its output, never verbose

**A file is used only once its sha256 matches the manifest.** Hashing 2.7 GB
takes seconds, so the result is remembered against the file's size and
modification time and redone whenever either changes. A hand-placed file is
verified exactly as a downloaded one is: an air-gapped machine is a legitimate
place to keep a health record.

**Nothing here may resolve inside the vault.** The data directory is checked
against the vault root on construction, after symlink resolution.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ..errors import DownloadError
from . import manifest, platforms
from .manifest import Bundle, RemoteFile

VERIFIED_FILENAME = "verified.json"
CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


class Store:
    """The reader's files on this machine."""

    def __init__(self, root: Path | None = None, vault_root: Path | None = None):
        self.root = (root or platforms.data_home()).expanduser()
        if vault_root is not None and platforms.is_inside(self.root, vault_root):
            raise DownloadError(
                "inside-vault",
                f"the reader's files would be stored at {self.root}, which is inside "
                f"the vault at {vault_root}. The vault syncs, so several gigabytes of "
                f"model would be uploaded to your sync service and downloaded again "
                f"on every other machine. Set {platforms.ENV_DATA_HOME} to a folder "
                f"outside the vault.",
            )

    # -- paths -------------------------------------------------------------

    @property
    def files_dir(self) -> Path:
        return self.root / "files"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def path_for(self, bundle: Bundle, item: RemoteFile) -> Path:
        return self.files_dir / bundle.id / item.name

    def part_for(self, bundle: Bundle, item: RemoteFile) -> Path:
        return self.files_dir / bundle.id / f"{item.name}.part"

    def unpacked_dir(self, bundle: Bundle) -> Path:
        return self.root / "runtime" / bundle.id

    def binary(self, bundle: Bundle) -> Path | None:
        """The engine executable, if its archive has been verified and unpacked."""
        if bundle.binary is None:
            return None
        path = self.unpacked_dir(bundle) / PurePosixPath(bundle.binary)
        return path if path.is_file() else None

    # -- verification ------------------------------------------------------

    def _records(self) -> dict[str, dict]:
        try:
            data = json.loads((self.root / VERIFIED_FILENAME).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_records(self, records: dict[str, dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / VERIFIED_FILENAME
        temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", "utf-8")
        os.replace(temporary, target)

    def _key(self, bundle: Bundle, item: RemoteFile) -> str:
        return f"{bundle.id}/{item.name}"

    def is_verified(self, bundle: Bundle, item: RemoteFile) -> bool:
        """Whether the file is present and its bytes are the pinned bytes."""
        path = self.path_for(bundle, item)
        try:
            info = path.stat()
        except OSError:
            return False
        if info.st_size != item.size:
            return False
        records = self._records()
        record = records.get(self._key(bundle, item))
        if (
            isinstance(record, dict)
            and record.get("size") == info.st_size
            and record.get("mtime_ns") == info.st_mtime_ns
            and record.get("sha256") == item.sha256
        ):
            return True
        if sha256_of(path) != item.sha256:
            return False
        self.remember(bundle, item)
        return True

    def remember(self, bundle: Bundle, item: RemoteFile) -> None:
        """Record that a file was hashed and matched, against its current stat."""
        info = self.path_for(bundle, item).stat()
        records = self._records()
        records[self._key(bundle, item)] = {
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "sha256": item.sha256,
        }
        self._write_records(records)

    def missing(self, bundles: tuple[Bundle, ...]) -> list[tuple[Bundle, RemoteFile]]:
        return [
            (bundle, item)
            for bundle in bundles
            for item in bundle.files
            if not self.is_verified(bundle, item)
        ]

    def arrived_bytes(self, bundle: Bundle, item: RemoteFile) -> int:
        """Bytes already here for one file: all of it, or what the part holds."""
        if self.is_verified(bundle, item):
            return item.size
        try:
            return min(self.part_for(bundle, item).stat().st_size, item.size)
        except OSError:
            return 0

    def is_ready(self, bundles: tuple[Bundle, ...]) -> bool:
        """Every file verified and every engine unpacked. Never unpacks anything itself."""
        if self.missing(bundles):
            return False
        return all(self.binary(b) is not None for b in bundles if b.is_archive)

    def prepare(self, bundles: tuple[Bundle, ...]) -> None:
        """Unpack any verified engine archive that has not been unpacked yet.

        Separate from :meth:`is_ready` so that asking whether the reader is
        ready never writes: an archive placed by hand is unpacked when the
        reader is started or the download finishes, not when a screen polls.
        """
        for bundle in bundles:
            if bundle.is_archive and self.binary(bundle) is None:
                if not self.missing((bundle,)):
                    self.unpack(bundle)

    # -- unpacking ---------------------------------------------------------

    def unpack(self, bundle: Bundle) -> Path:
        """Unpack a verified engine archive. Refuses anything that escapes it.

        Unpacked into a temporary sibling and renamed into place, so a crash
        half-way leaves no directory that looks finished. Symlinks are allowed
        only when they point at a sibling inside the archive — the llama.cpp
        tarballs use them for shared-library versions.
        """
        (item,) = bundle.files
        archive = self.path_for(bundle, item)
        if not self.is_verified(bundle, item):
            raise DownloadError(
                "not-verified",
                f"{archive.name} is not the pinned file, so it is not unpacked",
            )
        target = self.unpacked_dir(bundle)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{bundle.id}-", dir=target.parent))
        try:
            if item.name.endswith(".zip"):
                _unzip(archive, staging)
            else:
                _untar(archive, staging)
            binary = staging / PurePosixPath(bundle.binary or "")
            if not binary.is_file():
                raise DownloadError(
                    "archive-layout",
                    f"{archive.name} unpacked without {bundle.binary} in it",
                )
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return target


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise DownloadError("archive-layout", f"the archive holds an unsafe path: {name!r}")
    return path


def _untar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            _safe_member(member.name)
            if member.issym():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or ".." in link.parts or "/" in member.linkname:
                    raise DownloadError(
                        "archive-layout",
                        f"the archive holds a link out of itself: {member.name!r}",
                    )
            elif member.islnk() or not (member.isfile() or member.isdir()):
                raise DownloadError(
                    "archive-layout",
                    f"the archive holds an entry of an unexpected kind: {member.name!r}",
                )
        bundle.extractall(destination, filter="data")


def _unzip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            _safe_member(name)
        bundle.extractall(destination)


def bundle_by_id(bundle_id: str) -> Bundle | None:
    for bundle in (manifest.VISION, manifest.SPEECH, *manifest.ENGINES.values()):
        if bundle.id == bundle_id:
            return bundle
    return None
