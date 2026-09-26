"""Private, streaming archives. No extraction or deletion of user data."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import uuid

from . import __version__
from .filesystem import safe_child, sync_directory
from .pack import GameStackError

log = logging.getLogger(__name__)
ID = re.compile(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}")
MANIFEST_LIMIT = 16 * 1024 * 1024
RESERVE = 64 * 1024 * 1024
CONFIG = ("instance.yaml", "pack.yaml", "compose.yaml")


def folder(directory: Path) -> Path:
    if not directory.is_dir():
        raise GameStackError("Instance folder is missing. Check the instance name and --root.")
    return safe_child(directory, "backups")


@dataclass(frozen=True)
class BackupRecord:
    id: str
    created_utc: str
    size: int
    path: Path


def list_backups(directory: Path) -> list[BackupRecord]:
    destination = folder(directory)
    if not destination.exists():
        return []
    records = []
    incomplete = False
    for path in destination.iterdir():
        if path.name.endswith(".partial"):
            incomplete = True
        if path.suffix != ".tar" or not ID.fullmatch(path.stem):
            continue
        path = safe_child(destination, path.name)
        if not stat.S_ISREG(path.stat().st_mode):
            raise GameStackError("A backup is not a regular file. Inspect the backup folder before retrying.")
        try:
            timestamp = datetime.strptime(path.stem.split("-")[0], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise GameStackError("A backup filename has an invalid timestamp. Inspect the backup folder.") from None
        records.append(BackupRecord(path.stem, timestamp.isoformat(), path.stat().st_size, path))
    if incomplete:
        log.warning("Incomplete backup artifacts retained. Inspect the backup folder; these are not completed backups.")
    return sorted(records, key=lambda record: record.id, reverse=True)


def selected(directory: Path, backup_id: str) -> Path:
    if not ID.fullmatch(backup_id):
        raise GameStackError("Invalid backup ID. Use an ID from gamestack backup list; paths are not accepted.")
    return safe_child(folder(directory), backup_id + ".tar")


def signature(info: os.stat_result) -> tuple:
    if os.name == "nt":
        # Windows path and open-handle stats can disagree on identity metadata.
        return (stat.S_IFMT(info.st_mode), info.st_size, info.st_mtime_ns)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inventory(directory: Path) -> list[tuple[str, Path, os.stat_result]]:
    entries = []
    pending = [("data", safe_child(directory, "data"))]
    for filename in CONFIG:
        path = safe_child(directory, filename)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise GameStackError("Backup configuration must contain regular files. Run gamestack doctor with the instance name.")
        pending.append(("configuration/" + filename, path))
    while pending:
        archive_name, path = pending.pop()
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise GameStackError("Backup source contains a link or special file. Use regular files and directories inside the data folder.")
        entries.append((archive_name, path, info))
        if stat.S_ISDIR(info.st_mode):
            pending.extend((archive_name + "/" + entry.name, entry) for entry in path.iterdir())
    return sorted(entries)


def archive_size(entries: list) -> int:
    """Conservative archive estimate including bounded manifest and PAX overhead."""
    return MANIFEST_LIMIT + 10240 + sum(
        ((info.st_size + 511) // 512 * 512 if stat.S_ISREG(info.st_mode) else 0)
        + 4096 + len(archive_name.encode("utf-8")) * 2 for archive_name, _, info in entries)


def preflight(directory: Path) -> list:
    entries = inventory(directory)
    destination = folder(directory)
    if destination.exists():
        if not destination.is_dir():
            raise GameStackError("Backup location is not a directory. Preserve the existing file and correct the folder layout before retrying.")
        if os.name == "posix" and destination.stat().st_mode & 0o077:
            raise GameStackError("Backup folder is not private. Restrict its permissions to the operating account before retrying.")
    required = RESERVE + archive_size(entries)
    free = shutil.disk_usage(destination if destination.exists() else directory).free
    if free < required:
        raise GameStackError(f"Could not create a backup: {free} bytes free; at least {required} bytes required. Free disk space and retry gamestack backup.")
    return entries


class HashReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(size)
        self.digest.update(data)
        return data


def create(directory: Path, instance: str, pack: dict, initial_state: str) -> Path:
    entries = preflight(directory)
    destination = folder(directory)
    destination.mkdir(mode=0o700, exist_ok=True)
    if os.name == "posix" and destination.stat().st_mode & 0o077:
        raise GameStackError("Backup folder is not private. Restrict its permissions to the operating account before retrying.")
    now = datetime.now(timezone.utc)
    backup_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
    partial = safe_child(destination, backup_id + ".tar.partial")
    final = safe_child(destination, backup_id + ".tar")
    manifest = {"schema_version": 1, "instance": instance, "created_utc": now.isoformat(),
                "gamestack_version": __version__, "pack_id": pack["id"], "pack_version": pack["version"],
                "image": pack["image"], "initial_state": initial_state, "entries": []}
    log.info("Backup phase=write instance=%s", instance)
    with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for archive_name, path, before in entries:
                if signature(path.lstat()) != signature(before):
                    raise GameStackError("Backup source changed. Stop external writers and retry; partial backup retained.")
                member = tarfile.TarInfo(archive_name)
                member.mode = stat.S_IMODE(before.st_mode)
                member.mtime = before.st_mtime
                record = {"path": archive_name, "mode": member.mode, "mtime": member.mtime}
                if stat.S_ISDIR(before.st_mode):
                    member.type = tarfile.DIRTYPE
                    record.update(type="directory", size=0)
                    archive.addfile(member)
                else:
                    member.size = before.st_size
                    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as source:
                        if signature(os.fstat(source.fileno())) != signature(before):
                            raise GameStackError("Backup source changed. Stop external writers and retry.")
                        reader = HashReader(source)
                        archive.addfile(member, reader)
                        if signature(os.fstat(source.fileno())) != signature(before):
                            raise GameStackError("Backup source changed. Stop external writers and retry.")
                    record.update(type="file", size=member.size, sha256=reader.digest.hexdigest())
                manifest["entries"].append(record)
            if [(n, signature(s)) for n, _, s in inventory(directory)] != [(n, signature(s)) for n, _, s in entries]:
                raise GameStackError("Backup source changed. Stop external writers and retry; partial backup retained.")
            payload = json.dumps(manifest, ensure_ascii=True, sort_keys=True).encode("utf-8")
            if len(payload) > MANIFEST_LIMIT:
                raise GameStackError("Backup has too many entries for this format. Partial artifact retained; reduce file count before retrying.")
            member = tarfile.TarInfo("manifest.json")
            member.size, member.mode = len(payload), 0o600
            archive.addfile(member, io.BytesIO(payload))
        output.flush()
        os.fsync(output.fileno())
    log.info("Backup phase=verify instance=%s", instance)
    verify(partial, instance)
    # Atomic no-clobber publication on the same filesystem. Never replace another copy.
    os.link(partial, final)
    try:
        sync_directory(destination)
        # Persist the backups folder itself when this is its first archive.
        sync_directory(directory)
    except OSError:
        raise GameStackError("Backup publication could not be synced to disk. Archive copies were retained, but durability is unconfirmed. Check disk health and permissions, run backup verify, and retry backup after resolving the problem.") from None
    try:
        partial.unlink()
    except OSError:
        log.warning("Verified backup published; its partial artifact was also retained. Inspect the backup folder.")
    try:
        sync_directory(destination)
    except OSError:
        raise GameStackError("Backup directory changes could not be synced to disk. The verified archive was retained, but completion is unconfirmed. Check disk health and permissions, run backup verify, and retry backup after resolving the problem.") from None
    log.info("Backup phase=completed instance=%s", instance)
    return final


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def verify(path: Path, instance: str) -> dict:
    """Read every payload and validate structure, without extracting anything."""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Not regular")
        actual = []
        manifest = None
        with path.open("rb") as stream, tarfile.open(fileobj=stream, mode="r:") as archive:
            for member in archive:
                if manifest is not None:
                    raise ValueError("Manifest must be last")
                key = member.name
                parts = PurePosixPath(key).parts
                if (not parts or key != str(PurePosixPath(key)) or key.startswith("/") or
                        ".." in parts or "\\" in key or member.size < 0 or not (member.isdir() or member.isfile()) or member.issparse()):
                    raise ValueError("Unsafe member")
                if key == "manifest.json":
                    if not member.isfile() or member.size > MANIFEST_LIMIT:
                        raise ValueError("Invalid manifest")
                    manifest = json.loads(archive.extractfile(member).read(), object_pairs_hook=unique_object)
                    continue
                if not (parts[0] == "data" or key in {"configuration/" + f for f in CONFIG}):
                    raise ValueError("Unexpected root")
                record = {"path": key, "mode": member.mode, "mtime": member.mtime,
                          "type": "directory" if member.isdir() else "file", "size": member.size}
                if member.isdir() and member.size != 0:
                    raise ValueError("Directory payload")
                if member.isfile():
                    source = archive.extractfile(member)
                    record["sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
                actual.append(record)
                if len(actual) > MANIFEST_LIMIT // 64:
                    raise ValueError("Too many entries")
            # tarfile accepts missing end markers; require complete zero padding explicitly.
            stream.seek(archive.offset)
            tail_size = 0
            while chunk := stream.read(1024 * 1024):
                if any(chunk):
                    raise ValueError("Trailing content")
                tail_size += len(chunk)
            if tail_size < 1024 or path.stat().st_size % 512:
                raise ValueError("Truncated archive")
        required = {"schema_version", "instance", "created_utc", "gamestack_version", "pack_id", "pack_version", "image", "initial_state", "entries"}
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise ValueError("Invalid manifest fields")
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["instance"] != instance:
            raise ValueError("Manifest identity")
        if any(not isinstance(manifest[k], str) or not manifest[k] for k in required - {"schema_version", "entries"}):
            raise ValueError("Invalid metadata")
        if datetime.fromisoformat(manifest["created_utc"]).utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("Invalid time")
        if manifest["initial_state"] not in ("running", "exited", "created", "absent", "crashed"):
            raise ValueError("Invalid state")
        if not re.fullmatch(r".+@sha256:[a-f0-9]{64}", manifest["image"]):
            raise ValueError("Invalid image")
        if not isinstance(manifest["entries"], list):
            raise ValueError("Invalid entry list")
        for entry in manifest["entries"]:
            if (not isinstance(entry, dict) or type(entry.get("size")) is not int or
                    type(entry.get("mode")) is not int or not 0 <= entry["mode"] <= 0o7777 or
                    type(entry.get("mtime")) not in (int, float) or not math.isfinite(entry["mtime"])):
                raise ValueError("Invalid entry metadata")
        if manifest["entries"] != actual:
            raise ValueError("Content mismatch")
        by_name = {entry["path"]: entry for entry in actual}
        if len(by_name) != len(actual) or by_name.get("data", {}).get("type") != "directory":
            raise ValueError("Duplicate or missing root")
        if any(by_name.get("configuration/" + f, {}).get("type") != "file" for f in CONFIG):
            raise ValueError("Missing configuration")
        for entry in actual:
            if entry["path"].startswith("data/") and by_name.get(str(PurePosixPath(entry["path"]).parent), {}).get("type") != "directory":
                raise ValueError("Missing directory")
        return manifest
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, tarfile.TarError) as exc:
        log.debug("Backup verification failed type=%s", type(exc).__name__)
        raise GameStackError("Backup verification failed. The archive is inaccessible, incomplete, corrupt, or belongs to another instance. Keep it and other copies; check permissions and select another backup.") from None
