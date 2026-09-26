"""Beginner-facing commands; logging and interactive input live here."""
import argparse
import getpass
import logging
from pathlib import Path
import sys

from . import __version__
from .pack import GameStackError, load_pack, read_yaml, string, validate_value, bind_address
from .runtime import Runtime


GLOBAL_HELP = "Global options go BEFORE the command: gamestack --root /srv/gamestack --debug COMMAND ...\nUse the same --root for all operations on an instance. See docs/cli.md for full guidance."


def command_parser(commands, command: str, summary: str, behavior: str, examples: str, **kwargs):
    return commands.add_parser(command, help=summary, description=behavior,
                               formatter_class=argparse.RawDescriptionHelpFormatter,
                               epilog=f"Examples:\n{examples}\n\n{GLOBAL_HELP}", **kwargs)


class BackupHelp(argparse.Action):
    """Show focused help without reserving instance names list and verify."""
    def __call__(self, parser, namespace, values, option_string=None):
        arguments = getattr(namespace, "arguments", None) or []
        if arguments and arguments[0] in ("list", "verify"):
            action = arguments[0]
            descriptions = {
                "list": "List completed backups newest first with IDs, UTC dates, and sizes.\nThis reads local files without Docker and does not recheck archive integrity.\nWorks after instance removal or loss of the data folder; partial archives are not listed as completed.\nIf access fails, check the instance name, --root, and backup folder permissions.",
                "verify": "Read the entire selected backup and check its structure, metadata, and file checksums.\nThis works without Docker, does not extract files, and never changes server data.\nVerification detects corruption; it does not authenticate a backup or prove in-game recovery.\nIf verification fails, keep all copies and select another backup or check permissions.",
            }
            example = f"  gamestack backup {action} friends" + (" BACKUP-ID" if action == "verify" else "")
            focused = argparse.ArgumentParser(prog=f"{parser.prog} {action}", description=descriptions[action],
                formatter_class=argparse.RawDescriptionHelpFormatter,
                epilog=f"Examples:\n{example}\n\n{GLOBAL_HELP}")
            focused.add_argument("instance", help="Installed instance name from gamestack list")
            if action == "verify":
                focused.add_argument("backup_id", help="ID from backup list, without .tar; paths are not accepted")
            focused.print_help()
        else:
            parser.print_help()
        parser.exit()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="gamestack", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Manage local game servers using GamePacks (experimental).\nInstall, play, back up, and recover worlds through one CLI. Hosting requires Linux x86-64 and Docker Compose.",
        epilog="Examples:\n  gamestack install packs/minecraft-paper/pack.yaml --name friends\n  gamestack list\n  gamestack restore friends\n  gamestack --root /srv/gamestack status friends\n\nRun gamestack COMMAND --help for usage, examples, and safety details.\nHelp works offline and does not change files or contact Docker.")
    result.add_argument("--version", action="version", version=__version__, help="Print the installed GameStack version and exit")
    result.add_argument("--debug", action="store_true", help="Show diagnostic logging; place before the command")
    result.add_argument("--root", type=Path, default=Path.home() / ".local" / "share" / "gamestack",
                        help="Dedicated instance storage directory (default: %(default)s); place before the command")
    commands = result.add_subparsers(dest="command", required=True, title="commands")
    command_parser(commands, "list", "List installed instances and their server state",
        "Show installed instance names and current server state/health.\nUse these names with lifecycle, backup, and restore commands; they are not GamePack file paths.\nReads configuration and queries server status without changing files. Removed instances are hidden.\nIf status is unavailable, run doctor; if an instance is missing, check --root and its configuration.",
        "  gamestack list\n  gamestack --root /srv/gamestack list")
    backup = command_parser(commands, "backup", "Create, list, or verify private full-data backups",
        "Create a verified backup of all data and saved GameStack configuration.\nA running server is stopped temporarily (disconnecting players), then restarted and checked for health.\nStopped servers remain stopped. Crashed or ambiguous states block capture.\nArchives are private but unencrypted; no backups are pruned. Allow space for a full copy.\nIf capture fails, check space, permissions, and clean shutdown; retain partial files and older backups.",
        "  gamestack backup friends\n  gamestack backup list friends\n  gamestack backup verify friends BACKUP-ID\n  gamestack backup list --help\n  gamestack backup verify --help\n\nForms:\n  backup INSTANCE\n  backup list INSTANCE\n  backup verify INSTANCE BACKUP-ID\nInstance names 'list' and 'verify' remain valid: backup list creates a backup of instance list.", add_help=False)
    backup.add_argument("arguments", nargs="+", metavar="ARG", help="INSTANCE | list INSTANCE | verify INSTANCE BACKUP-ID; IDs exclude .tar")
    backup.add_argument("-h", "--help", action=BackupHelp, nargs=0, help="Show general backup help or focused list/verify help and exit")
    restore = command_parser(commands, "restore", "Restore a backup while preserving current data for recovery",
        "Replace the entire data folder with a verified local backup from this instance and identical GamePack.\nCurrent GameStack configuration stays active. A verified safety backup and the previous data folder are retained.\nRunning servers stop and restart with a health check; stopped or crashed servers stay stopped.\nMissing data triggers a warning that no current world can be backed up; inaccessible data cannot be skipped.\nInteractive use offers a numbered picker and confirmation. Scripts require BACKUP-ID and --yes.\nIf restore fails, keep all copies, check status, and follow docs/cli.md#interrupted-restore-recovery.\nAn unfinished restore marker blocks further data-changing operations until reconciled.",
        "  gamestack restore friends\n  gamestack restore friends BACKUP-ID\n  gamestack restore friends BACKUP-ID --yes")
    restore.add_argument("instance", help="Original installed instance name from gamestack list")
    restore.add_argument("backup_id", nargs="?", help="ID from backup list, without .tar; omit for interactive selection")
    restore.add_argument("--yes", action="store_true", help="Confirm replacement and any missing-data warning without prompting; scripts also require an ID")
    update = command_parser(commands, "update", "Apply a reviewed Paper update or recover the previous build",
        "Update a running, healthy Paper server from a reviewed pinned GamePack and sibling upstream-lock.json.\n"
        "The server stops cleanly, makes a verified backup, recreates on the selected image, then checks health and the Paper JAR checksum.\n"
        "Recovery restores the pre-update world and configuration while retaining later world data separately.\n"
        "All backups and recovery copies are kept. Allow disk space for multiple full world copies.\n"
        "Interactive confirmation is required; scripts must pass --yes. An interrupted update must be recovered before other changes.",
        "  gamestack update friends --pack /path/to/reviewed/pack.yaml\n"
        "  gamestack update friends --recover\n"
        "  gamestack update friends --pack /path/to/reviewed/pack.yaml --yes")
    update.add_argument("instance", help="Installed Minecraft Paper instance name")
    choice = update.add_mutually_exclusive_group(required=True)
    choice.add_argument("--pack", type=Path, help="Reviewed candidate pack.yaml with a sibling upstream-lock.json")
    choice.add_argument("--recover", action="store_true", help="Restore the pre-update build and world, retaining the current world")
    update.add_argument("--yes", action="store_true", help="Confirm without prompting; required for scripts")
    remove = command_parser(commands, "rm", "Remove a server container while retaining instance files",
        "Confirm, stop, and remove the instance's server container.\nWorld data, backups, and configuration remain in their current folders; the instance name stays reserved.\nRemoved instances disappear from list and cannot be started or restored through normal commands.\nData stored only in the container's writable layer is lost; packs must keep saves in their data folder.\nScripts must pass --yes. If removal fails, check Docker access, doctor, and the operation lock.",
        "  gamestack rm friends\n  gamestack rm friends --yes")
    remove.add_argument("instance", help="Installed instance name to retire; this does not delete its saved files")
    remove.add_argument("--yes", action="store_true", help="Confirm stopping and removing the container without prompting")
    pack = command_parser(commands, "pack", "Inspect a GamePack before installation",
        "Check a GamePack's declarative configuration before installing it.\nUse the validate subcommand; no server is installed or started.",
        "  gamestack pack validate packs/minecraft-paper/pack.yaml\n  gamestack pack validate --help")
    sub = pack.add_subparsers(dest="pack_command", required=True, title="commands")
    validate = command_parser(sub, "validate", "Validate a GamePack YAML file offline",
        "Validate required fields, supported schema, pinned image reference, and configuration rules.\nThis works offline without Docker and does not modify files.\nA pass does not establish image availability or real-game support.\nIf invalid, correct the reported setting using docs/gamepacks.md and retry.",
        "  gamestack pack validate packs/minecraft-paper/pack.yaml")
    validate.add_argument("file", type=Path, help="Path to the GamePack YAML file to validate")
    install = command_parser(commands, "install", "Configure an instance and start its server",
        "Read a GamePack file, collect settings, and create private instance configuration and a data folder.\nBy default the server starts and must pass its health check. Existing instance names are never overwritten.\nInteractive setup prompts for required values, agreements, and supported network exposure.\nScripts need --values with required settings and explicit agreements; quote numbers and booleans in YAML.\nKeep values files containing secrets private and outside the repository.\nIf setup fails, retain created files; check settings, host prerequisites, space, memory, and port conflicts.",
        "  gamestack install packs/minecraft-paper/pack.yaml --name friends\n  gamestack install packs/minecraft-paper/pack.yaml --name friends --values /private/settings.yaml --prepare-only\n  gamestack --root /srv/gamestack install packs/minecraft-paper/pack.yaml --name friends")
    install.add_argument("file", type=Path, help="Path to a GamePack YAML file, such as packs/minecraft-paper/pack.yaml")
    install.add_argument("--bind-address", help="Schema 2: numeric host IP; default localhost unless interactive exposure is accepted. 0.0.0.0 exposes game ports on all IPv4 interfaces")
    install.add_argument("--name", help="New instance name (default: pack ID); lowercase letters/digits/hyphens, starting with a letter, up to 40 characters")
    install.add_argument("--values", type=Path, help="Private YAML mapping of user settings; fixed GamePack values cannot be overridden")
    install.add_argument("--prepare-only", action="store_true", help="Create configuration and data folders without starting a server or requiring Docker")
    lifecycle = {
        "start": ("Start a server and verify its health", "Start the configured server using its saved GamePack and pinned image.\nWait for health verification before reporting success. No restore or version upgrade is performed.\nIf startup fails, the server may still be running: check status and doctor before retrying.\nCheck free memory/disk, downloads, port conflicts, and world-folder permissions."),
        "stop": ("Gracefully stop a server", "Stop the server using the GamePack's shutdown timeout; players are disconnected.\nWorld files and backups are retained. This command does not create a backup.\nIf shutdown fails, check status and doctor before touching world files or retrying."),
        "restart": ("Stop, start, and check server health", "Gracefully stop the server, then start it and verify health. Players are disconnected.\nA stopped server will be started. No backup or version upgrade is performed.\nIf restart fails, check status and doctor; the server may still be running."),
        "status": ("Inspect an instance's server state and health", "Query the server's current state and health without starting or stopping it.\nStates include running, exited, or not created; unavailable health is shown explicitly.\nStatus remains available during interrupted restore recovery, even if data is missing.\nIf the query fails, check Docker access and saved configuration with doctor."),
    }
    for action, (summary, behavior) in lifecycle.items():
        command = command_parser(commands, action, summary, behavior, f"  gamestack {action} friends\n  gamestack --root /srv/gamestack {action} friends")
        command.add_argument("instance", help="Installed instance name from gamestack list, not a GamePack path")
    doctor = command_parser(commands, "doctor", "Check host prerequisites and optional instance configuration",
        "Check the supported hosting platform, Docker access, and required Compose capabilities.\nWith an instance name, also validate saved configuration and world-folder access/ownership.\nThis diagnoses prerequisites; it does not repair files, start a server, or prove world health.\nIf a check fails, follow its suggested action and rerun doctor.\nMissing world data can be recovered using restore when saved configuration is intact.",
        "  gamestack doctor\n  gamestack doctor friends")
    doctor.add_argument("instance", nargs="?", help="Optional installed instance name; omit to check host prerequisites only")
    return result

def configure(pack: dict, supplied: dict) -> dict:
    if supplied.keys() - pack["environment"].keys():
        raise GameStackError("Unknown configuration settings. Use the environment keys declared in the GamePack.")
    if any("value" in pack["environment"][key] for key in supplied):
        raise GameStackError("Fixed GamePack settings cannot be supplied through --values. Remove those keys.")
    values = {}
    for key, setting in pack["environment"].items():
        if "value" in setting:
            values[key] = setting["value"]
            continue
        if "agreement" in setting and key not in supplied and sys.stdin.isatty():
            answer = input(f"{setting['prompt']}\n{setting['agreement']}\nAccept? [y/N] ")
            values[key] = validate_value(key, setting, "TRUE" if answer.strip().lower() in ("y", "yes") else "FALSE")
            continue
        if key in supplied:
            value = supplied[key]
        elif "default" in setting and not sys.stdin.isatty():
            value = setting["default"]
        else:
            if not sys.stdin.isatty():
                raise GameStackError("Required settings are missing. Run interactively or provide --values with every required setting.")
            default = setting.get("default")
            prompt = setting["prompt"] + (f" [{default}]" if default else "") + ": "
            value = (getpass.getpass(prompt) if setting["secret"] else input(prompt)) or default
        if not string(value):
            raise GameStackError("Settings must be nonempty single-line strings. Quote numbers and booleans in your values file.")
        values[key] = validate_value(key, setting, value)
    return values


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logger = logging.getLogger("gamestack")
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if args.debug else logging.WARNING)
    logger.propagate = False
    try:
        if args.command == "pack":
            pack = load_pack(args.file)
            print(f"GamePack is valid (schema {pack['schema_version']}). Image availability and game behavior have not been tested.")
            return 0
        runtime = Runtime(args.root)
        if args.command == "backup":
            from . import backup
            parts = args.arguments
            if len(parts) == 1:
                instance = parts[0]
                print("Backup will stop a running server temporarily and restart it after capture.", flush=True)
                artifact, state = runtime.backup(instance)
                print(f"Verified backup: {artifact.stem}\nLocation: {artifact}\n{instance}: {state}")
            elif len(parts) == 2 and parts[0] == "list":
                records = backup.list_backups(runtime.directory(parts[1]))
                print("Completed backups (listing does not recheck integrity):")
                for record in records:
                    print(f"  {record.id}  {record.created_utc}  {record.size} bytes")
                if not records:
                    print("No completed backups found.")
            elif len(parts) == 3 and parts[0] == "verify":
                backup.verify(backup.selected(runtime.directory(parts[1]), parts[2]), parts[1])
                print("Backup integrity verified. This does not authenticate the archive or prove restoration.")
            else:
                raise GameStackError("Use gamestack backup INSTANCE, backup list INSTANCE, or backup verify INSTANCE BACKUP-ID.")
            return 0
        if args.command == "restore":
            from . import backup
            from .restore import data_exists, require_no_transaction
            directory, _ = runtime.inspect(args.instance, allow_missing_data=True)
            require_no_transaction(directory)
            selected_id = args.backup_id
            if not sys.stdin.isatty() and (selected_id is None or not args.yes):
                raise GameStackError("Restore needs a backup ID and confirmation. Run interactively or use restore INSTANCE BACKUP-ID --yes.")
            if selected_id is None:
                records = backup.list_backups(directory)
                if not records:
                    print("No completed backups found. Current data was unchanged.")
                    return 0
                print("Select a backup (listing does not recheck integrity):")
                for number, record in enumerate(records, 1):
                    print(f"  {number}. {record.created_utc}  {record.size} bytes  {record.id}")
                while True:
                    answer = input("Backup number (blank to cancel): ").strip()
                    if not answer:
                        print("Restore cancelled.")
                        return 0
                    if answer.isascii() and answer.isdigit() and len(answer) <= 10 and 1 <= int(answer) <= len(records):
                        selected_id = records[int(answer) - 1].id
                        break
                    print("Enter a number from the list, or leave blank to cancel.")
            path = backup.selected(directory, selected_id)
            if not path.is_file():
                raise GameStackError("Selected backup is missing. Run gamestack backup list with the instance name and select an available ID.")
            runtime.doctor()
            initial = runtime.restore_state(directory)
            present = data_exists(directory)
            print(f"Restore backup: {selected_id}\nReplace entire data folder: {directory / 'data'}")
            print("Current GameStack settings remain active. A running server will stop and players will be disconnected.")
            print("A verified safety backup of current data will be created." if present else
                  "WARNING: The current data folder is missing. No current world can be backed up.")
            print("After restore: restart and check health." if initial == "running" else
                  "After restore: leave stopped; health will not be tested.", flush=True)
            if not args.yes:
                answer = input("Restore this backup? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Restore cancelled.")
                    return 0
            result = runtime.restore(args.instance, selected_id, expected_state=initial, expected_data=present)
            print(f"Restored backup: {result.backup_id}")
            print(f"Safety backup: {result.safety_backup}" if result.safety_backup else "Safety backup: none (current data was missing).")
            print(f"Recovery files retained: {result.recovery_directory}\n{args.instance}: {result.state}")
            return 0
        if args.command == "update":
            from . import update
            if not args.yes and not sys.stdin.isatty():
                raise GameStackError("Update requires confirmation. Run interactively or pass --yes in scripts.")
            if args.recover:
                work, backup_id = update.recovery_summary(runtime, args.instance)
                print(f"Recover {args.instance} from pre-update backup: {backup_id or 'none; old data never changed'}")
                print(f"Recovery files: {work}\nPlayers will be disconnected.")
                if backup_id:
                    print("Current world will be retained separately.")
                print("Recovery returns to the pre-update world state; play since then will not appear in the active world.", flush=True)
                if not args.yes and input("Recover this update? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("Recovery cancelled.")
                    return 0
                result = update.recover(runtime, args.instance)
                print(f"Recovered {args.instance}: {result.state}")
                print(f"Pre-update backup: {result.backup}" if result.backup else "Pre-update backup: none (update stopped before capture).")
                print(f"Later world retained: {result.retained_data}" if result.retained_data else "Later world: no replacement occurred.")
            else:
                directory = runtime.directory(args.instance)
                update.require_no_transaction(directory)
                directory, current = runtime.inspect(args.instance)
                proposed, lock = update.candidate(current, args.pack)
                print(f"Update {args.instance}: GamePack {current['version']} → {proposed['version']}")
                print(f"Paper {current['environment']['VERSION']['value']} build {current['environment']['PAPER_BUILD']['value']} → "
                      f"{proposed['environment']['VERSION']['value']} build {proposed['environment']['PAPER_BUILD']['value']}")
                print(f"Image: {current['image']} → {proposed['image']}")
                print(f"Reviewed JAR SHA-256: {lock['paper']['sha256']}")
                print(f"Downtime: clean shutdown, full backup, image recreation, and health wait up to {proposed['startup_timeout']} seconds.")
                print(f"Backup location: {directory / 'backups'}; all backups are kept.")
                print("If update fails, check server status and use explicit recovery. Recovery keeps later data but returns to the pre-update world state.", flush=True)
                if not args.yes and input("Apply this update? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("Update cancelled.")
                    return 0
                result = update.run(runtime, args.instance, args.pack, expected=(proposed, lock))
                print(f"Updated {args.instance}: healthy on GamePack {result.version}\nPre-update backup: {result.backup}\nRecovery files: {result.work}")
            return 0
        if args.command == "rm":
            directory, _ = runtime.inspect(args.instance)
            if not args.yes:
                if not sys.stdin.isatty():
                    raise GameStackError("Removal needs confirmation. Run interactively or pass --yes to stop and remove the container while retaining its files.")
                answer = input(f"Stop and remove {args.instance}? Files will remain at {directory}. [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("Removal cancelled.")
                    return 0
            retained = runtime.remove(args.instance)
            print(f"Removed {args.instance}. World data, backups, and configuration retained at: {retained}")
            return 0
        if args.command == "list":
            instances = runtime.list_instances()
            if instances:
                print("Configured instances:")
                for instance in instances:
                    print(f"  {instance}: {runtime.quick_status(instance)}")
                print("Use an instance name with gamestack start, stop, restart, or status.")
            else:
                print("No configured instances found. Use gamestack install --help to configure a GamePack.")
            return 0
        if args.command == "install":
            pack = load_pack(args.file)
            instance = args.name or pack["id"]
            values = configure(pack, read_yaml(args.values) if args.values else {})
            address = bind_address(args.bind_address or "127.0.0.1")
            if pack["schema_version"] == 2 and args.bind_address is None and sys.stdin.isatty():
                answer = input("Allow players on other machines to connect? This exposes game ports on all IPv4 interfaces, including public interfaces if present. Router/firewall settings stay under your control. [y/N] ")
                if answer.strip().lower() in ("y", "yes"):
                    address = "0.0.0.0"
            if not args.prepare_only:
                runtime.doctor()
            directory = runtime.prepare(pack, instance, values, address)
            print(f"Prepared {instance}. World folder: {directory / 'data'}")
            if not args.prepare_only:
                print(f"{instance}: {runtime.lifecycle('start', instance)}")
            return 0
        if args.command == "doctor":
            if args.instance:
                runtime.inspect(args.instance)
            runtime.doctor()
            print("Host prerequisites passed" + ("; instance configuration and world folder passed." if args.instance else "."))
            return 0
        print(f"{args.instance}: {runtime.lifecycle(args.command, args.instance)}")
        return 0
    except GameStackError as exc:
        print(f"GameStack: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        logger.debug("Filesystem operation failed type=%s errno=%s", type(exc).__name__, exc.errno)
        print("GameStack cannot access its files. Check free disk space and directory permissions, then retry. Any partial files were retained for inspection.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("GameStack operation interrupted. Check status before retrying; any created files were retained.", file=sys.stderr)
        return 130
    finally:
        logger.removeHandler(handler)
        handler.close()
