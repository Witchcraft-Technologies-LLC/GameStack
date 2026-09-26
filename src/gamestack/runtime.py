"""Local instance storage and a small Docker Compose boundary."""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import time
import tarfile
from typing import TYPE_CHECKING

import yaml

from .filesystem import safe_child as child
from .pack import GameStackError, fields, load_pack, name, read_yaml, require, validate_values, bind_address

if TYPE_CHECKING:
    from .restore import RestoreResult

log = logging.getLogger(__name__)


def checked_root(path: Path) -> Path:
    path = path.expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise GameStackError("The storage path contains a symbolic link. Choose a dedicated real directory.")
    path = path.resolve()
    if path in (Path.home().resolve(), Path("/srv"), Path("/opt"), Path("/home")) or len(path.parts) < 3:
        raise GameStackError("Storage needs a dedicated subdirectory, such as /srv/gamestack.")
    return path


def write_private(path: Path, value: dict) -> None:
    # Exclusive creation leaves existing configuration untouched, including symlinks.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
        yaml.safe_dump(value, stream, sort_keys=False, allow_unicode=True)


def literal(value):
    """Escape Compose interpolation recursively, including passwords and host paths."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [literal(v) for v in value]
    if isinstance(value, dict):
        return {k: literal(v) for k, v in value.items()}
    return value


def compose(pack: dict, values: dict, directory: Path, deployment: dict | None = None) -> dict:
    deployment = deployment or {}
    service = {
        "image": pack["image"], "restart": "unless-stopped",
        "stop_grace_period": f'{pack["stop_timeout"]}s',
        "environment": values,
        "ports": [{"target": p["container"], "published": str(p["host"]),
                   "host_ip": deployment.get("bind_address", "127.0.0.1"), "protocol": p["protocol"]} for p in pack["ports"]],
        "volumes": [{"type": "bind", "source": str(directory / "data"), "target": pack["data_path"],
                     "bind": {"create_host_path": False}}],
        "healthcheck": {"test": ["CMD", *pack["healthcheck"]], "interval": "10s", "timeout": "5s", "retries": 12},
    }
    if "user" in pack:
        service["user"] = deployment["user"] if pack["user"] == "installing-user" else pack["user"]
    if pack["schema_version"] == 2:
        service["healthcheck"]["start_period"] = f'{pack["startup_timeout"]}s'
    return literal({"services": {"server": service}})


def validate_configuration(metadata: dict, pack: dict, document: dict, directory: Path, instance: str) -> None:
    fields(metadata, {"schema_version", "instance"}, {"deployment"} if metadata.get("schema_version") == 2 else set())
    require(type(metadata["schema_version"]) is int and metadata["schema_version"] in (1, 2) and metadata["instance"] == instance,
            "Instance metadata does not match.")
    require(metadata["schema_version"] == pack["schema_version"], "Instance and pack schema versions differ.")
    deployment = metadata.get("deployment", {})
    if pack["schema_version"] == 2:
        fields(deployment, {"bind_address"} | ({"user"} if pack.get("user") == "installing-user" else set()))
        require(isinstance(deployment["bind_address"], str) and bind_address(deployment["bind_address"]) == deployment["bind_address"], "Invalid saved bind address.")
        if pack.get("user") == "installing-user":
            require(isinstance(deployment["user"], str) and re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", deployment["user"]), "Invalid saved server user.")
    # Reject hand-edited Compose that could introduce arbitrary host mounts or images.
    try:
        escaped = document["services"]["server"]["environment"]
        values = {k: v.replace("$$", "$") for k, v in escaped.items()}
        validate_values(pack, values)
        require(document == compose(pack, values, directory, deployment), "Generated server configuration has changed.")
    except (KeyError, AttributeError, TypeError, RecursionError) as exc:
        raise GameStackError("Saved server configuration is invalid. Recover the original configuration before retrying.") from exc


def status_rows(raw: str) -> list:
    """Decode Compose array or JSON-lines output; callers apply status policy."""
    return json.loads(raw) if raw.strip().startswith("[") else [
        json.loads(line) for line in raw.splitlines() if line.strip()]


class Runtime:
    def __init__(self, root: Path):
        self.root = checked_root(root)

    def directory(self, instance: str) -> Path:
        return child(self.root, name(instance))

    def list_instances(self) -> list[str]:
        """List locally valid instances without contacting Docker or changing files."""
        try:
            entries = sorted(self.root.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            return []
        instances = []
        for entry in entries:
            try:
                name(entry.name)
            except GameStackError:
                continue
            if not entry.is_symlink() and not entry.is_dir():
                continue
            if not entry.is_symlink() and (entry / "removed.yaml").is_file():
                continue
            try:
                self.inspect(entry.name)
            except (GameStackError, OSError):
                log.warning("Skipping instance=%s: configuration is incomplete, inaccessible, or unsafe. Run gamestack doctor %s to investigate.", entry.name, entry.name)
                continue
            instances.append(entry.name)
        return instances

    def prepare(self, pack: dict, instance: str, values: dict, address: str = "127.0.0.1") -> Path:
        validate_values(pack, values)
        address = bind_address(address)
        deployment = {}
        if pack["schema_version"] == 1:
            require(address == "127.0.0.1", "Network binding requires GamePack schema 2.")
        else:
            deployment["bind_address"] = address
            if pack.get("user") == "installing-user":
                if not hasattr(os, "getuid") or not hasattr(os, "getgid") or os.getuid() == 0 or os.getgid() == 0:
                    raise GameStackError("This GamePack requires a non-root Linux user. Install on the hosting machine using your normal account.")
                deployment["user"] = f"{os.getuid()}:{os.getgid()}"
        directory = self.directory(instance)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise GameStackError("Instance already exists. Choose another name or use its lifecycle commands; no files were replaced.") from exc
        log.info("Preparing instance=%s", instance)
        # Interrupted setup stays in place for inspection. Never recursively clean up user paths.
        (directory / "data").mkdir(mode=0o700)
        write_private(directory / "pack.yaml", pack)
        write_private(directory / "compose.yaml", compose(pack, values, directory, deployment))
        write_private(directory / "instance.yaml", {"schema_version": pack["schema_version"], "instance": instance, **({"deployment": deployment} if deployment else {})})
        log.info("Prepared instance=%s", instance)
        return directory

    def inspect(self, instance: str, *, allow_missing_data: bool = False) -> tuple[Path, dict]:
        directory = self.directory(instance)
        marker = child(directory, "removed.yaml")
        if marker.exists():
            raise GameStackError("This instance was removed. Its files remain in the instance directory for recovery; choose a new name for a new installation.")
        for filename in ("instance.yaml", "pack.yaml", "compose.yaml", "data"):
            path = child(directory, filename)
            if filename != "data":
                try:
                    regular = stat.S_ISREG(path.lstat().st_mode)
                except OSError:
                    regular = False
                if not regular:
                    raise GameStackError("Saved configuration must use accessible regular files. Recover the original configuration before retrying.")
        metadata = read_yaml(directory / "instance.yaml")
        pack = load_pack(directory / "pack.yaml")
        document = read_yaml(directory / "compose.yaml")
        validate_configuration(metadata, pack, document, directory, instance)
        data = directory / "data"
        try:
            owner = data.lstat()
        except FileNotFoundError:
            if not allow_missing_data:
                raise GameStackError("World folder is missing. Use gamestack restore to recover a backup.") from None
        else:
            require(stat.S_ISDIR(owner.st_mode), "World folder is not a directory.")
            if pack.get("user") == "installing-user":
                require(metadata["deployment"]["user"] == f"{owner.st_uid}:{owner.st_gid}" and owner.st_uid > 0 and owner.st_gid > 0,
                        "World folder ownership differs from the saved server user. Use the original operating account and restore the expected ownership.")
        return directory, pack

    @contextmanager
    def lock(self, directory: Path):
        path = child(directory, ".operation.lock")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise GameStackError("Another operation holds this instance's lock. Wait; after an interruption, verify no operation is running before removing .operation.lock.") from exc
        try:
            os.close(fd)
            yield
        finally:
            path.unlink()

    def command(self, args: list[str], timeout: int = 30) -> str:
        started = time.monotonic()
        try:
            # Windows also searches system directories for bare executable
            # names. Honor PATH consistently, including an intentionally empty
            # tool search path during installed-artifact checks.
            executable = shutil.which(args[0])
            if executable is None:
                raise FileNotFoundError("Server tooling is not on PATH")
            command = [str(Path(executable).resolve()), *args[1:]]
            result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            # Docker output and exception strings can expose credentials; never log either.
            log.debug("Server tooling failed type=%s elapsed=%.2fs", type(exc).__name__, time.monotonic() - started)
            raise GameStackError("Server operation failed. Check Docker is installed and running and your account has access; run gamestack doctor. For start failures, also check the pack image, ports, permissions, and healthcheck. Existing data was retained.") from exc
        log.debug("Server tooling completed elapsed=%.2fs", time.monotonic() - started)
        return result.stdout

    def doctor(self) -> None:
        if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
            raise GameStackError("Hosting targets Linux x86-64. Use an Ubuntu Server LTS x86-64 host; local pack validation works on other systems.")
        self.command(["docker", "info", "--format", "{{.ServerVersion}}"])
        try:
            self.command(["docker", "compose", "version"])
        except GameStackError as exc:
            raise GameStackError("Docker is reachable, but Docker Compose is unavailable. Install or repair the Docker Compose plugin for your distribution, verify with docker compose version, then retry.") from exc
        help_text = self.command(["docker", "compose", "up", "--help"])
        if "--wait-timeout" not in help_text:
            raise GameStackError("Docker Compose is too old. Install a Compose version with up --wait-timeout support.")

    def compose_command(self, directory: Path) -> list[str]:
        project = "gamestack-" + hashlib.sha256(str(directory).encode()).hexdigest()[:16]
        return ["docker", "compose", "--project-name", project, "--project-directory", str(directory),
                "--file", str(directory / "compose.yaml")]

    def remove(self, instance: str) -> Path:
        """Remove the server container and retire its configuration, retaining all files."""
        directory, pack = self.inspect(instance)
        with self.lock(directory):
            from .restore import require_no_transaction
            require_no_transaction(directory)
            directory, pack = self.inspect(instance)
            self.doctor()
            base = self.compose_command(directory)
            log.warning("Removing instance=%s container; retaining files at %s", instance, directory)
            self.command(base + ["stop", "--timeout", str(pack["stop_timeout"]), "server"], pack["stop_timeout"] + 30)
            self.command(base + ["rm", "--force", "server"])
            remaining = self.command(base + ["ps", "--all", "--quiet", "server"])
            if remaining.strip():
                raise GameStackError("Container removal could not be verified. Run gamestack status and retry rm with the instance name. All instance files were retained.")
            # Written only after confirmed removal. Keep paths stable for recovery.
            write_private(directory / "removed.yaml", {"schema_version": 1, "instance": instance})
            log.info("Removed instance=%s; retained files at %s", instance, directory)
        return directory

    def quick_status(self, instance: str) -> str:
        """Read a bounded status snapshot without locks or host preflight."""
        try:
            directory = self.directory(instance)
            raw = self.command(self.compose_command(directory) +
                               ["ps", "--all", "--format", "json", "server"], timeout=5)
            rows = status_rows(raw)
            if not rows:
                return "stopped (not created)"
            if len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError("Unexpected container count or format")
            row = rows[0]
            state, health, code = row.get("State"), row.get("Health"), row.get("ExitCode")
            if state == "running":
                return f"started ({health})" if health in ("healthy", "unhealthy", "starting") else "started"
            if state == "exited":
                if type(code) is int and code >= 0:
                    return "stopped" if code == 0 else "crashed (nonzero exit)"
                return "stopped (exit code unavailable)"
            if state == "dead":
                return "crashed"
            if state == "created":
                return "stopped"
            if state in ("restarting", "paused", "removing"):
                return state
            raise ValueError("Unknown container state")
        except (GameStackError, OSError, ValueError, TypeError, RecursionError):
            log.warning("Status unavailable for instance=%s. Check Docker access with gamestack doctor.", instance)
            return "unknown (status unavailable)"

    def backup_state(self, directory: Path) -> str:
        return self.server_state(directory)

    def restore_state(self, directory: Path) -> str:
        return self.server_state(directory, allow_crashed=True)

    def server_state(self, directory: Path, *, allow_crashed: bool = False) -> str:
        """Fail closed on anything other than one unambiguous safe container state."""
        raw = self.command(self.compose_command(directory) + ["ps", "--all", "--quiet", "server"])
        ids = raw.split()
        if not ids:
            return "absent"
        try:
            if len(ids) != 1 or not re.fullmatch(r"[a-f0-9]{12,64}", ids[0]):
                raise ValueError("Ambiguous containers")
            state = json.loads(self.command(["docker", "inspect", "--format", "{{json .State}}", ids[0]]))
            status = state["Status"]
            if (allow_crashed and status == "exited" and state["Running"] is False and
                    state["Paused"] is False and state["Restarting"] is False and state["Dead"] is False and
                    type(state["OOMKilled"]) is bool and type(state["ExitCode"]) is int and state["ExitCode"] >= 0):
                return "crashed" if state["OOMKilled"] or state["ExitCode"] else "exited"
            if (state["OOMKilled"] is not False or state["Paused"] is not False or
                    state["Restarting"] is not False or state["Dead"] is not False or
                    type(state["ExitCode"]) is not int or state["ExitCode"] != 0 or
                    state["Running"] is not (status == "running") or
                    status not in ("running", "exited", "created")):
                raise ValueError("Unsafe state")
            return status
        except (ValueError, TypeError, KeyError) as exc:
            raise GameStackError("Cannot establish a safe server state for this operation. Check gamestack status and doctor with the instance name; resolve crashes or incomplete shutdown before retrying.") from None

    def restore(self, instance: str, backup_id: str, *, expected_state: str, expected_data: bool) -> "RestoreResult":
        from .restore import run
        return run(self, instance, backup_id, expected_state=expected_state, expected_data=expected_data)

    def backup(self, instance: str) -> tuple[Path, str]:
        from . import backup
        directory, pack = self.inspect(instance)
        with self.lock(directory):
            from .restore import require_no_transaction
            require_no_transaction(directory)
            directory, pack = self.inspect(instance)
            self.doctor()
            log.info("Backup phase=preflight instance=%s", instance)
            initial = self.backup_state(directory)
            backup.preflight(directory)
            if initial == "running":
                log.info("Backup phase=stop instance=%s", instance)
                self.command(self.compose_command(directory) + ["stop", "--timeout", str(pack["stop_timeout"]), "server"], pack["stop_timeout"] + 30)
                if self.backup_state(directory) != "exited":
                    raise GameStackError("Backup stopped: clean shutdown could not be verified. No completed backup was created. Check server status before restarting.")
            artifact = None
            failure = None
            try:
                artifact = backup.create(directory, instance, pack, initial)
            except (GameStackError, OSError, tarfile.TarError, UnicodeError) as exc:
                log.debug("Backup failed type=%s", type(exc).__name__)
                failure = exc
            # Deliberately not finally: interruption must not unexpectedly start a server.
            if initial == "running":
                log.info("Backup phase=restart instance=%s", instance)
                try:
                    self.start_server(directory, pack, instance)
                except (GameStackError, OSError):
                    result = f"Verified backup retained at: {artifact}." if artifact else "Backup failed; partial artifacts and existing data were retained."
                    raise GameStackError(f"{result} Server restart or health verification also failed. Run gamestack status {instance} and gamestack doctor {instance} before retrying start.") from None
            if failure is not None:
                state = "Server restarted and healthy." if initial == "running" else "Server remains stopped."
                detail = str(failure) if isinstance(failure, GameStackError) else "Check free disk space and backup folder permissions."
                raise GameStackError(f"Backup failed. {detail} {state} Partial artifacts and older backups were retained.") from None
            return artifact, "healthy" if initial == "running" else "stopped"

    def start_server(self, directory: Path, pack: dict, instance: str) -> None:
        """Start with health verification; caller holds the instance lock."""
        base = self.compose_command(directory)
        startup_timeout = pack.get("startup_timeout", 180)
        try:
            self.command(base + ["up", "--detach", "--no-recreate", "--pull", "missing", "--wait", "--wait-timeout", str(startup_timeout)], max(900, startup_timeout + 120))
        except GameStackError as exc:
            raise GameStackError(f"Could not start {instance} and confirm its health. Check downloads, free memory/disk, game port conflicts, and world folder permissions. Files were retained; the server may still be running. Run gamestack status {instance} and gamestack doctor {instance} before retrying.") from exc

    def lifecycle(self, action: str, instance: str) -> str:
        if action == "status":
            directory = self.directory(instance)
            try:
                child(directory, ".update.json").lstat()
            except FileNotFoundError:
                pass
            else:
                return self.quick_status(instance)
        directory, pack = self.inspect(instance, allow_missing_data=action in ("stop", "status"))
        with self.lock(directory):
            from .restore import require_no_transaction
            if action in ("start", "restart"):
                require_no_transaction(directory)
            directory, pack = self.inspect(instance, allow_missing_data=action in ("stop", "status"))
            base = self.compose_command(directory)
            self.doctor()
            log.info("Operation=%s instance=%s started", action, instance)
            if action == "status":
                # Only query fixed state fields; never return raw container output or labels.
                raw = self.command(base + ["ps", "--all", "--format", "json"])
                try:
                    rows = status_rows(raw)
                    states = []
                    for row in rows:
                        state, health = row.get("State"), row.get("Health")
                        states.append(f"{state if state in ('running', 'exited', 'created', 'restarting', 'paused', 'dead') else 'unknown'} / {health if health in ('healthy', 'unhealthy', 'starting') else 'health unavailable'}")
                    return ", ".join(states) or "not created"
                except (ValueError, AttributeError, TypeError) as exc:
                    raise GameStackError("Cannot read server status. Check your Docker Compose installation.") from exc
            if action not in ("start", "stop", "restart"):
                raise GameStackError("Unknown operation. Run gamestack --help.")
            if action in ("stop", "restart"):
                self.command(base + ["stop", "--timeout", str(pack["stop_timeout"])], pack["stop_timeout"] + 30)
            if action in ("start", "restart"):
                self.start_server(directory, pack, instance)
            log.info("Operation=%s instance=%s completed", action, instance)
        return "stopped" if action == "stop" else "healthy"
