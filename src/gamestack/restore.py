"""Restore verified data through private staging, preserving every recovery copy."""
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import shlex
import stat
import tarfile
import uuid

from . import backup
from .filesystem import sync_directory
from .pack import CONFIG_LIMIT, GameStackError, parse_yaml, read_yaml, validate
from .runtime import Runtime, validate_configuration

log = logging.getLogger(__name__)
MARKER = ".restore.json"


@dataclass(frozen=True)
class RestoreResult:
    backup_id: str
    safety_backup: Path | None
    recovery_directory: Path
    state: str


def require_no_transaction(directory: Path) -> None:
    from .update import require_no_transaction as require_no_update
    require_no_update(directory)
    path = backup.safe_child(directory, MARKER)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise GameStackError(f"An unfinished restore needs recovery. Files are retained; inspect {path} and follow docs/cli.md#interrupted-restore-recovery before starting another operation. Status, stop, and backup list/verify remain available.")


def data_exists(directory: Path) -> bool:
    path = backup.safe_child(directory, "data")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        raise GameStackError("Current world data is not a directory. Preserve it and correct the folder layout before restoring.")
    return True


def check_sources(directory: Path, pack: dict) -> bool:
    """Missing is allowed; inaccessible, foreign-owned, linked or mounted is not."""
    present = data_exists(directory)
    if os.name == "posix":
        owner = directory.stat()
        if (owner.st_uid, owner.st_gid) != (os.getuid(), os.getgid()):
            raise GameStackError("Restore requires the original operating account. Check instance ownership and retry using that account.")
        metadata = read_yaml(directory / "instance.yaml")
        user = metadata.get("deployment", {}).get("user") if pack.get("user") == "installing-user" else pack.get("user")
        if user is not None and user != f"{os.getuid()}:{os.getgid()}":
            raise GameStackError("Restore cannot safely assign the saved server ownership. Use the original server operating account.")
    if present:
        # ismount also handles portable mount checks. Linux mountinfo catches bind
        # mounts on the same device, which st_dev/ismount alone cannot detect.
        mounts = set()
        mountinfo = Path("/proc/self/mountinfo")
        if mountinfo.exists():
            for line in mountinfo.read_text(encoding="utf-8").splitlines():
                encoded = line.split()[4]
                for escaped, value in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
                    encoded = encoded.replace(escaped, value)
                mounts.add(Path(encoded))
        device = directory.stat().st_dev
        for archive_name, path, info in backup.inventory(directory):
            if path.is_mount() or path in mounts or info.st_dev != device:
                raise GameStackError("Restore requires data and staging on the instance filesystem without nested mounts. Preserve mounted data and correct the layout before retrying.")
            if os.name == "posix" and (info.st_uid, info.st_gid) != (os.getuid(), os.getgid()):
                raise GameStackError("Restore source ownership differs from the operating account. Correct ownership using the original account before retrying.")
    return present


def check_space(directory: Path, manifest: dict, present: bool) -> None:
    required = backup.RESERVE + sum(
        entry["size"] + 4096 for entry in manifest["entries"] if entry["path"] == "data" or entry["path"].startswith("data/"))
    if present:
        entries = backup.preflight(directory)
        required += backup.archive_size(entries)
    free = shutil.disk_usage(directory).free
    if free < required:
        raise GameStackError(f"Could not restore: {free} bytes free; at least {required} bytes required for staged data and a safety backup. Free disk space and retry.")


def write_marker(directory: Path, record: dict) -> None:
    """Durable phase publication; an interrupted write retains its private partial."""
    marker = backup.safe_child(directory, MARKER)
    partial = backup.safe_child(directory, MARKER + "." + uuid.uuid4().hex + ".partial")
    with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, marker)
    sync_directory(directory)


def clear_marker(directory: Path) -> None:
    backup.safe_child(directory, MARKER).unlink()
    sync_directory(directory)


def stage(directory: Path, instance: str, path: Path, pack: dict, present: bool) -> tuple[Path, dict]:
    """Read only regular files; never use tar extraction or archived owner IDs."""
    before = backup.signature(path.lstat())
    manifest = backup.verify(path, instance)
    if manifest["pack_id"] != pack["id"] or manifest["pack_version"] != pack["version"] or manifest["image"] != pack["image"]:
        raise GameStackError("Backup GamePack or server version differs from this instance. Select a backup made with the identical GamePack; version rollback is not supported.")
    for entry in manifest["entries"]:
        if entry["mode"] & 0o7000:
            raise GameStackError("Backup contains special permission bits. Select a backup with ordinary file permissions.")
        if entry["type"] == "directory" and entry["mode"] & 0o700 != 0o700:
            raise GameStackError("Backup directories must be readable, writable, and accessible to the operating account.")
    check_space(directory, manifest, present)
    work = backup.safe_child(directory, ".restore-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    config = {}
    records = {entry["path"]: entry for entry in manifest["entries"]}
    seen = set()
    directories = []
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        if backup.signature(os.fstat(stream.fileno())) != before:
            raise GameStackError("Backup changed during restore. Staged files were retained; select a stable backup and retry.")
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            for member in archive:
                if member.name == "manifest.json":
                    continue
                entry = records.get(member.name)
                if (entry is None or member.name in seen or member.size != entry["size"] or
                        member.mode != entry["mode"] or member.mtime != entry["mtime"] or member.issparse() or
                        not (member.isdir() if entry["type"] == "directory" else member.isfile())):
                    raise GameStackError("Backup changed or contains unsafe members. Staged files were retained; select another backup.")
                seen.add(member.name)
                if member.name.startswith("configuration/"):
                    if member.size > CONFIG_LIMIT:
                        raise GameStackError("Archived configuration is too large. Select another backup.")
                    payload = archive.extractfile(member).read(CONFIG_LIMIT + 1)
                    if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                        raise GameStackError("Archived configuration checksum differs. Select another backup.")
                    try:
                        config[member.name.split("/")[1]] = parse_yaml(payload)
                    except GameStackError:
                        raise GameStackError("Archived configuration is invalid. Select another backup.") from None
                    continue
                # Member names matched the verified manifest above. Check the whole
                # ancestor chain once rather than revisiting every prefix.
                target = work / member.name
                target = backup.safe_child(target.parent, target.name)
                if member.isdir():
                    target.mkdir(mode=0o700)
                    directories.append((target, entry))
                else:
                    digest = hashlib.sha256()
                    with archive.extractfile(member) as source, os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                            digest.update(chunk)
                        if digest.hexdigest() != entry["sha256"]:
                            raise GameStackError("Restored file checksum differs. Current data was retained; select another backup.")
                        output.flush()
                        os.chmod(target, entry["mode"] & 0o777)
                        os.utime(target, (entry["mtime"], entry["mtime"]))
                        os.fsync(output.fileno())
        if seen != records.keys() or backup.signature(os.fstat(stream.fileno())) != before:
            raise GameStackError("Backup changed during restore. Staged files were retained; select a stable backup.")
    if backup.signature(path.lstat()) != before:
        raise GameStackError("Backup changed during restore. Current data was retained; select a stable backup.")
    try:
        archived_pack = validate(config["pack.yaml"])
        if archived_pack != pack:
            raise GameStackError("Different GamePack")
        validate_configuration(config["instance.yaml"], archived_pack, config["compose.yaml"], directory, instance)
    except (GameStackError, KeyError, TypeError, AttributeError, RecursionError):
        raise GameStackError("Archived configuration is invalid or its GamePack differs. Select a backup from this instance with the identical GamePack.") from None
    for target, entry in reversed(directories):
        os.chmod(target, entry["mode"] & 0o777)
        os.utime(target, (entry["mtime"], entry["mtime"]))
        sync_directory(target)
    sync_directory(work)
    sync_directory(directory)
    return work, manifest


def move_directory(source: Path, destination: Path) -> None:
    # All operations are serialized; external writers must be stopped. Never
    # intentionally replace an existing destination, even an empty directory.
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise GameStackError("Restore destination unexpectedly exists. Preserve all copies and reconcile the restore marker.")
    if source.is_symlink() or not source.is_dir() or source.is_mount():
        raise GameStackError("Restore source directory became unsafe. Preserve all copies and reconcile the restore marker.")
    source.rename(destination)


def run(runtime: Runtime, instance: str, backup_id: str, *, expected_state: str, expected_data: bool) -> RestoreResult:
    directory, pack = runtime.inspect(instance, allow_missing_data=True)
    require_no_transaction(directory)
    with runtime.lock(directory):
        require_no_transaction(directory)
        directory, pack = runtime.inspect(instance, allow_missing_data=True)
        runtime.doctor()
        initial = runtime.restore_state(directory)
        present = check_sources(directory, pack)
        if initial != expected_state or present != expected_data:
            raise GameStackError("Server state or world folder changed since confirmation. Run restore again to review the current state.")
        log.info("Restore phase=stage instance=%s backup=%s", instance, backup_id)
        path = backup.selected(directory, backup_id)
        work = None
        safety = None
        stopped = False
        replacing = False
        try:
            work, manifest = stage(directory, instance, path, pack, present)
            if runtime.restore_state(directory) != initial:
                raise GameStackError("Server state changed while staging. Current data was retained; check status and retry.")
            if initial == "running":
                log.info("Restore phase=stop instance=%s", instance)
                runtime.command(runtime.compose_command(directory) + ["stop", "--timeout", str(pack["stop_timeout"]), "server"], pack["stop_timeout"] + 30)
                if runtime.backup_state(directory) != "exited":
                    raise GameStackError("Clean shutdown could not be verified. Check status before retrying; data was not replaced.")
                stopped = True
            if check_sources(directory, pack) != present:
                raise GameStackError("World folder changed while staging. Check external writers before retrying.")
            # Staged bytes already occupy disk; only the safety archive now needs space.
            if present:
                log.info("Restore phase=safety-backup instance=%s", instance)
                safety = backup.create(directory, instance, pack, initial)
                sync_directory(safety.parent)
            elif shutil.disk_usage(directory).free < backup.RESERVE:
                raise GameStackError("Restore reserve is exhausted. Free disk space and retry.")
            if runtime.restore_state(directory) != ("exited" if stopped else initial):
                raise GameStackError("Server state changed before replacement. Current data was retained; check status.")
            record = {"schema_version": 1, "instance": instance, "backup_id": backup_id,
                      "safety_backup": str(safety) if safety else None, "initial_state": initial,
                      "had_data": present, "work_directory": str(work), "phase": "remove-container"}
            # A stopped container can retain a bind mount to the old directory.
            # Remove only this service, without deleting volumes, before any rename.
            replacing = True
            write_marker(directory, record)
            runtime.command(runtime.compose_command(directory) + ["rm", "--force", "server"])
            if runtime.restore_state(directory) != "absent":
                raise GameStackError("Server container removal could not be confirmed. Data copies were retained.")
            record["phase"] = "preserve-current"
            write_marker(directory, record)
            log.warning("Restore phase=replace instance=%s recovery=%s safety_backup=%s", instance, work, safety)
            if present:
                move_directory(backup.safe_child(directory, "data"), backup.safe_child(work, "previous-data"))
                sync_directory(work)
                sync_directory(directory)
            record["phase"] = "install-staged"
            write_marker(directory, record)
            move_directory(backup.safe_child(work, "data"), backup.safe_child(directory, "data"))
            sync_directory(work)
            sync_directory(directory)
            runtime.inspect(instance)
            record["phase"] = "start" if initial == "running" else "complete"
            write_marker(directory, record)
            if initial == "running":
                try:
                    runtime.start_server(directory, pack, instance)
                except (GameStackError, OSError):
                    confirmed = False
                    try:
                        runtime.command(runtime.compose_command(directory) + ["stop", "--timeout", str(pack["stop_timeout"]), "server"], pack["stop_timeout"] + 30)
                        confirmed = runtime.restore_state(directory) in ("exited", "crashed", "created", "absent")
                    except (GameStackError, OSError):
                        pass
                    detail = "Server stop confirmed." if confirmed else "Server stop NOT confirmed; it may still be writing. Run gamestack stop and status."
                    raise GameStackError(f"Restored server failed startup or health verification. {detail}") from None
            record["phase"] = "complete"
            write_marker(directory, record)
            clear_marker(directory)
            state = "healthy" if initial == "running" else "stopped (health not tested)"
            log.info("Restore phase=completed instance=%s state=%s", instance, state)
            return RestoreResult(backup_id, safety, work, state)
        except (GameStackError, OSError, tarfile.TarError, UnicodeError, OverflowError, ValueError) as exc:
            log.debug("Restore failed instance=%s type=%s", instance, type(exc).__name__)
            detail = str(exc) if isinstance(exc, GameStackError) else "Check free disk space and file permissions."
            if stopped and not replacing:
                try:
                    runtime.start_server(directory, pack, instance)
                    detail += " Original server restarted and healthy."
                except (GameStackError, OSError):
                    detail += " Original server restart failed; check status before retrying."
            recovery = f" Recovery files: {work}." if work else " Private staging artifacts, if created, remain inside the instance folder."
            if safety:
                command = shlex.join(["gamestack", "--root", str(runtime.root), "restore", instance, safety.stem])
                recovery += f" Verified safety backup: {safety}. After reconciling any restore marker, run: {command}"
            if replacing:
                recovery += f" Inspect {directory / MARKER}; follow docs/cli.md#interrupted-restore-recovery before retrying."
            raise GameStackError(f"Restore failed. {detail}{recovery} Existing copies were retained.") from None
