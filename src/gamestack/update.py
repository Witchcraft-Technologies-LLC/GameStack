"""Explicit, pinned Paper updates with retained worlds and resumable recovery."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

import yaml

from . import backup, restore
from .filesystem import safe_child, sync_directory
from .pack import CONFIG_LIMIT, GameStackError, load_pack, read_yaml, validate_values
from .runtime import Runtime, compose, validate_configuration

MARKER = ".update.json"
LAST = "last-update.json"
PINS = ("VERSION", "PAPER_BUILD")
ID = re.compile(r"[a-f0-9]{32}")
HASH = re.compile(r"[a-f0-9]{64}")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateResult:
    backup: Path
    work: Path
    version: str


@dataclass(frozen=True)
class RecoveryResult:
    backup: Path | None
    retained_data: Path | None
    state: str


def _exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def require_no_transaction(directory: Path) -> None:
    marker = safe_child(directory, MARKER)
    if _exists(marker):
        raise GameStackError(
            "An update needs recovery. Files are retained. Run gamestack update INSTANCE --recover "
            "after checking status; start, restart, backup, restore, removal, and another update are blocked."
        )


def _write_json(path: Path, value: dict) -> None:
    temporary = safe_child(path.parent, path.name + "." + uuid.uuid4().hex + ".partial")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def _read_json(path: Path) -> dict:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError()
        with path.open("rb") as stream:
            payload = stream.read(CONFIG_LIMIT + 1)
        if len(payload) > CONFIG_LIMIT:
            raise ValueError()
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=backup.unique_object)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise GameStackError(f"{path.name} is missing or invalid. Keep all files and follow the update recovery guide.") from None


def _record(directory: Path, value: dict) -> None:
    log.info("Update phase=%s instance=%s", value.get("phase"), directory.name)
    _write_json(safe_child(directory, MARKER), value)


def _work(directory: Path, value: dict) -> Path:
    token = value.get("id")
    if not isinstance(token, str) or not ID.fullmatch(token) or value.get("instance") != directory.name:
        raise GameStackError("Update recovery record does not match this instance. Keep all files and inspect it.")
    path = safe_child(directory, ".update-" + token)
    if not path.is_dir() or path.is_symlink():
        raise GameStackError("Update recovery directory is missing or unsafe. Keep all files and inspect it.")
    return path


def _copy_private(source: Path, destination: Path) -> None:
    if not stat.S_ISREG(source.lstat().st_mode) or destination.exists():
        raise GameStackError("Update configuration staging is unsafe. Existing files were retained.")
    with source.open("rb") as read, os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as write:
        shutil.copyfileobj(read, write)
        write.flush()
        os.fsync(write.fileno())
    sync_directory(destination.parent)


def _replace_private(source: Path, destination: Path) -> None:
    if not stat.S_ISREG(source.lstat().st_mode):
        raise GameStackError("Saved update configuration is not a regular file. Keep all files and inspect it.")
    temporary = safe_child(destination.parent, destination.name + "." + uuid.uuid4().hex + ".partial")
    _copy_private(source, temporary)
    os.replace(temporary, destination)
    sync_directory(destination.parent)


def _paper_jar(directory: Path, version: str, build: str, expected: str | None = None) -> str:
    if not re.fullmatch(r"[0-9][0-9A-Za-z._-]*", version) or not re.fullmatch(r"[0-9]+", build):
        raise GameStackError("Paper version/build pin is invalid.")
    filename = f"paper-{version}-{build}.jar"
    matches = [(path, info) for name, path, info in backup.inventory(directory)
               if name.startswith("data/") and path.name == filename and stat.S_ISREG(info.st_mode)]
    if len(matches) != 1:
        raise GameStackError("Expected pinned Paper JAR was not found in the world folder. Update was not verified.")
    path, before = matches[0]
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        if backup.signature(os.fstat(stream.fileno())) != backup.signature(before):
            raise GameStackError("Paper JAR changed during verification. Update was not verified.")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if backup.signature(os.fstat(stream.fileno())) != backup.signature(before):
            raise GameStackError("Paper JAR changed during verification. Update was not verified.")
    if expected is not None and digest != expected:
        raise GameStackError("Paper JAR checksum differs from the reviewed upstream lock. Update was not verified.")
    return digest


def candidate(current: dict, path: Path) -> tuple[dict, dict]:
    pack = load_pack(path)
    if current["id"] != "minecraft-paper" or pack["id"] != current["id"] or pack["schema_version"] != current["schema_version"]:
        raise GameStackError("V0.1 updates support only the same Minecraft Paper GamePack and schema.")
    old, new = json.loads(json.dumps(current)), json.loads(json.dumps(pack))
    for item in (old, new):
        item.pop("version")
        item.pop("image")
        for key in PINS:
            setting = item["environment"].get(key)
            if not isinstance(setting, dict) or set(setting) != {"value"}:
                raise GameStackError("Paper update pins must remain fixed GamePack values.")
            setting.pop("value")
    if old != new or pack == current:
        raise GameStackError("Update candidate must change only its version, image, and fixed Paper pins; a new GamePack version is required.")
    lock = _read_json(path.with_name("upstream-lock.json"))
    try:
        paper = lock["paper"]
        if (lock["image"]["reference"] != pack["image"] or lock["image"]["platform"] != "linux/amd64" or
                paper["version"] != pack["environment"]["VERSION"]["value"] or
                str(paper["build"]) != pack["environment"]["PAPER_BUILD"]["value"] or
                not isinstance(paper["sha256"], str) or not HASH.fullmatch(paper["sha256"])):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise GameStackError("The reviewed upstream lock does not match the candidate Paper pins.") from None
    return pack, lock


def _values(directory: Path, old: dict, new: dict) -> dict:
    document = read_yaml(safe_child(directory, "compose.yaml"))
    escaped = document["services"]["server"]["environment"]
    values = {key: value.replace("$$", "$") for key, value in escaped.items()}
    validate_values(old, values)
    for key in PINS:
        values[key] = new["environment"][key]["value"]
    validate_values(new, values)
    return values


def _pull(runtime: Runtime, image: str) -> str:
    runtime.command(["docker", "image", "pull", image], timeout=900)
    platform = runtime.command(["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image])
    if platform.strip() != "linux/amd64":
        raise GameStackError("Selected image is not for Linux x86-64. Current server was unchanged.")
    image_id = runtime.command(["docker", "image", "inspect", "--format", "{{.Id}}", image]).strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise GameStackError("Could not verify the selected image. Current server was unchanged.")
    return image_id


def _healthy(runtime: Runtime, instance: str) -> bool:
    return runtime.quick_status(instance) == "started (healthy)"


def _stop(runtime: Runtime, directory: Path, timeout: int) -> None:
    runtime.command(runtime.compose_command(directory) + ["stop", "--timeout", str(timeout), "server"], timeout + 30)
    if runtime.backup_state(directory) != "exited":
        raise GameStackError("Clean server shutdown could not be verified. Keep all files and check status.")


def _new_container(runtime: Runtime, directory: Path, pack: dict, expected_image_id: str) -> None:
    timeout = pack["startup_timeout"]
    runtime.command(runtime.compose_command(directory) + [
        "up", "--detach", "--force-recreate", "--pull", "never", "--wait", "--wait-timeout", str(timeout), "server"
    ], max(900, timeout + 120))
    _verify_container(runtime, directory, expected_image_id)


def _verify_container(runtime: Runtime, directory: Path, expected_image_id: str) -> None:
    container = runtime.command(runtime.compose_command(directory) + ["ps", "--quiet", "server"]).strip()
    if not re.fullmatch(r"[a-f0-9]{12,64}", container):
        raise GameStackError("Server container could not be identified. Its image and health were not verified.")
    actual = runtime.command(["docker", "inspect", "--format", "{{.Image}}", container]).strip()
    if actual != expected_image_id or not _healthy(runtime, directory.name):
        raise GameStackError("Server container image or health does not match the selected release.")


def _saved(directory: Path, work: Path, name: str) -> Path:
    source = safe_child(work, name)
    if not source.is_file() or source.is_symlink():
        raise GameStackError("Saved update configuration is missing or unsafe. Keep all files and inspect it.")
    return source


def run(runtime: Runtime, instance: str, candidate_path: Path, *, expected: tuple[dict, dict] | None = None) -> UpdateResult:
    directory = runtime.directory(instance)
    from .restore import require_no_transaction as require_no_restore
    require_no_restore(directory)
    directory, old = runtime.inspect(instance)
    new, lock = candidate(old, candidate_path)
    with runtime.lock(directory):
        require_no_restore(directory)
        require_no_transaction(directory)
        directory, old = runtime.inspect(instance)
        new, lock = candidate(old, candidate_path)
        if expected is not None and (new, lock) != expected:
            raise GameStackError("Update candidate changed since confirmation. Review its pins and confirm again.")
        runtime.doctor()
        if not _healthy(runtime, instance):
            raise GameStackError("Update requires a running, healthy server. Run gamestack status and doctor first.")
        values = _values(directory, old, new)
        metadata = read_yaml(safe_child(directory, "instance.yaml"))
        new_compose = compose(new, values, directory, metadata.get("deployment"))
        validate_configuration(metadata, new, new_compose, directory, instance)
        backup.preflight(directory)
        old_jar = _paper_jar(directory, old["environment"]["VERSION"]["value"], old["environment"]["PAPER_BUILD"]["value"])
        image_id = _pull(runtime, new["image"])
        backup.preflight(directory)
        token = uuid.uuid4().hex
        work = safe_child(directory, ".update-" + token)
        work.mkdir(mode=0o700)
        _copy_private(safe_child(directory, "pack.yaml"), safe_child(work, "old-pack.yaml"))
        _copy_private(safe_child(directory, "compose.yaml"), safe_child(work, "old-compose.yaml"))
        _copy_private(safe_child(directory, "instance.yaml"), safe_child(work, "old-instance.yaml"))
        with os.fdopen(os.open(safe_child(work, "new-pack.yaml"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            yaml.safe_dump(new, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        with os.fdopen(os.open(safe_child(work, "new-compose.yaml"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            yaml.safe_dump(new_compose, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        _write_json(safe_child(work, "new-lock.json"), lock)
        record = {"schema": 1, "id": token, "instance": instance, "phase": "stop-old",
                  "backup_id": None, "old_jar": old_jar, "new_image_id": image_id}
        _record(directory, record)
        switched = False
        try:
            _stop(runtime, directory, old["stop_timeout"])
            record["phase"] = "backup-old"
            _record(directory, record)
            artifact = backup.create(directory, instance, old, "running")
            record["backup_id"] = artifact.stem
            record["phase"] = "install-new"
            _record(directory, record)
            switched = True
            _replace_private(_saved(directory, work, "new-pack.yaml"), safe_child(directory, "pack.yaml"))
            _replace_private(_saved(directory, work, "new-compose.yaml"), safe_child(directory, "compose.yaml"))
            runtime.inspect(instance)
            record["phase"] = "start-new"
            _record(directory, record)
            _new_container(runtime, directory, new, image_id)
            _paper_jar(directory, new["environment"]["VERSION"]["value"],
                       new["environment"]["PAPER_BUILD"]["value"], lock["paper"]["sha256"])
            record["phase"] = "complete"
            _record(directory, record)
            _write_json(safe_child(directory, LAST), record)
            safe_child(directory, MARKER).unlink()
            sync_directory(directory)
            return UpdateResult(artifact, work, new["version"])
        except (GameStackError, OSError, ValueError, UnicodeError) as exc:
            if switched:
                try:
                    _stop(runtime, directory, new["stop_timeout"])
                except (GameStackError, OSError):
                    pass
            else:
                try:
                    if runtime.backup_state(directory) == "exited":
                        runtime.start_server(directory, old, instance)
                        if _healthy(runtime, instance):
                            safe_child(directory, MARKER).unlink()
                            sync_directory(directory)
                except (GameStackError, OSError):
                    pass
            detail = str(exc) if isinstance(exc, GameStackError) else "Check disk space and file permissions."
            raise GameStackError(
                f"Update failed. {detail} Files and any backup were retained at {work}. "
                f"Run gamestack status {instance}; if an update marker remains, run gamestack update {instance} --recover."
            ) from None


def _load_recovery(directory: Path) -> tuple[dict, Path, bool]:
    marker = safe_child(directory, MARKER)
    active = _exists(marker)
    record = _read_json(marker if active else safe_child(directory, LAST))
    if record.get("schema") != 1 or record.get("phase") == "recovered":
        raise GameStackError("No recoverable update was found for this instance.")
    if (record.get("phase") not in ("stop-old", "backup-old", "install-new", "start-new", "complete",
                                     "recover-stop", "recover-remove-container", "recover-preserve-new", "recover-install-old")
            or not isinstance(record.get("old_jar"), str) or not HASH.fullmatch(record["old_jar"])
            or not isinstance(record.get("new_image_id"), str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", record["new_image_id"])):
        raise GameStackError("Update recovery record is invalid. Keep all files and inspect it.")
    work = _work(directory, record)
    backup_id = record.get("backup_id")
    if backup_id is not None and (not isinstance(backup_id, str) or not backup.ID.fullmatch(backup_id)):
        raise GameStackError("Update backup ID is invalid. Keep all files and inspect the recovery record.")
    return record, work, active


def recovery_summary(runtime: Runtime, instance: str) -> tuple[Path, str | None]:
    directory = runtime.directory(instance)
    record, work, _ = _load_recovery(directory)
    return work, record["backup_id"]


def recover(runtime: Runtime, instance: str) -> RecoveryResult:
    directory = runtime.directory(instance)
    with runtime.lock(directory):
        # Restore marker and update marker are independent; only the former blocks recovery.
        if safe_child(directory, restore.MARKER).exists():
            raise GameStackError("Finish the interrupted restore before recovering an update.")
        record, work, active = _load_recovery(directory)
        old = load_pack(_saved(directory, work, "old-pack.yaml"))
        if record["backup_id"] is None:
            # Interrupted before a verified backup: the old world/configuration never changed.
            if record["phase"] not in ("stop-old", "backup-old"):
                raise GameStackError("No verified pre-update backup exists. Keep all files and inspect the update record.")
            for source, target in (("old-pack.yaml", "pack.yaml"), ("old-compose.yaml", "compose.yaml"),
                                   ("old-instance.yaml", "instance.yaml")):
                _replace_private(_saved(directory, work, source), safe_child(directory, target))
            runtime.inspect(instance)
            runtime.doctor()
            if runtime.backup_state(directory) == "exited":
                runtime.start_server(directory, old, instance)
            if not _healthy(runtime, instance):
                raise GameStackError("Old server did not recover healthy. Update record and data were retained.")
            record["phase"] = "recovered"
            _write_json(safe_child(directory, LAST), record)
            if active:
                safe_child(directory, MARKER).unlink()
            return RecoveryResult(None, None, "healthy")
        archive = backup.selected(directory, record["backup_id"])
        manifest = backup.verify(archive, instance)
        if (manifest["pack_id"] != old["id"] or manifest["pack_version"] != old["version"] or
                manifest["image"] != old["image"]):
            raise GameStackError("Pre-update backup does not match the saved old GamePack. Current files were unchanged.")
        runtime.doctor()
        old_image_id = _pull(runtime, old["image"])
        if not active:
            record["phase"] = "recover-stop"
            _record(directory, record)
        base = runtime.compose_command(directory)
        ids = runtime.command(base + ["ps", "--all", "--quiet", "server"]).split()
        if len(ids) > 1 or (ids and not re.fullmatch(r"[a-f0-9]{12,64}", ids[0])):
            raise GameStackError("Server container state is ambiguous. Recovery made no data changes.")
        if ids:
            state = _read_container_state(runtime, ids[0])
            if state["Running"]:
                runtime.command(["docker", "stop", "-t", str(old["stop_timeout"]), ids[0]], old["stop_timeout"] + 30)
                state = _read_container_state(runtime, ids[0])
            if state["Running"] or state["Paused"] or state["Restarting"]:
                raise GameStackError("Server could not be confirmed stopped. World data was unchanged.")
        post = safe_child(work, "post-update-data")
        if post.is_symlink() or (post.exists() and not post.is_dir()):
            raise GameStackError("Retained world copy is unsafe. Keep all files and inspect the update directory.")
        if not post.exists() and safe_child(directory, "data").exists():
            try:
                current = load_pack(safe_child(directory, "pack.yaml"))
                runtime.inspect(instance)
                if ids and _read_container_state(runtime, ids[0])["ExitCode"] == 0:
                    artifact = backup.create(directory, instance, current, "exited")
                    record["post_backup_id"] = artifact.stem
                    _record(directory, record)
            except (GameStackError, OSError):
                # The raw data directory is still preserved even if a consistent archive is impossible.
                pass
        staged, _ = restore.stage(directory, instance, archive, old, False)
        had_installed_old = record["phase"] == "recover-install-old"
        record["phase"] = "recover-remove-container"
        _record(directory, record)
        if ids:
            runtime.command(base + ["rm", "--force", "server"])
            if runtime.command(base + ["ps", "--all", "--quiet", "server"]).strip():
                raise GameStackError("Stopped container removal could not be confirmed. World data was unchanged.")
        record["phase"] = "recover-preserve-new"
        _record(directory, record)
        if post.exists() and safe_child(directory, "data").exists() and not had_installed_old:
            raise GameStackError("Recovery found two active world folders before installing the old world. Keep both and inspect the journal.")
        if not post.exists() and safe_child(directory, "data").exists():
            restore.move_directory(safe_child(directory, "data"), post)
            sync_directory(work)
            sync_directory(directory)
        record["phase"] = "recover-install-old"
        _record(directory, record)
        for source, target in (("old-pack.yaml", "pack.yaml"), ("old-compose.yaml", "compose.yaml"),
                                   ("old-instance.yaml", "instance.yaml")):
            _replace_private(_saved(directory, work, source), safe_child(directory, target))
        if not safe_child(directory, "data").exists():
            restore.move_directory(safe_child(staged, "data"), safe_child(directory, "data"))
            sync_directory(directory)
        runtime.inspect(instance)
        runtime.start_server(directory, old, instance)
        _verify_container(runtime, directory, old_image_id)
        _paper_jar(directory, old["environment"]["VERSION"]["value"],
                   old["environment"]["PAPER_BUILD"]["value"], record["old_jar"])
        record["phase"] = "recovered"
        _write_json(safe_child(directory, LAST), record)
        if safe_child(directory, MARKER).exists():
            safe_child(directory, MARKER).unlink()
        sync_directory(directory)
        return RecoveryResult(archive, post, "healthy")


def _read_container_state(runtime: Runtime, container: str) -> dict:
    try:
        state = json.loads(runtime.command(["docker", "inspect", "--format", "{{json .State}}", container]))
        if not isinstance(state, dict) or any(type(state.get(key)) is not bool for key in ("Running", "Paused", "Restarting")):
            raise ValueError()
        return state
    except (ValueError, TypeError):
        raise GameStackError("Cannot establish server state. Keep all files and check Docker status.") from None
