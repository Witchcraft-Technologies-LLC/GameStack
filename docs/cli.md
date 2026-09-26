# Using the development CLI

This is the first engine milestone, version `0.1.0rc1`. No game is supported yet. Manual backup creation, listing, integrity verification, restore, and explicit Paper updates are implemented. Backup, restore, and explicit Paper update acceptance passed by user confirmation; scheduled maintenance is pending. V0.1 keeps every completed backup and safety backup without automatic pruning, so storage use grows; use synthetic data for development.

## Setup

From the repository, using Python 3.11 or newer:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
gamestack --help
```

On Windows development machines, activate `.venv\Scripts\Activate.ps1` in PowerShell. Development portability is intended, but Windows/macOS have not been validated. Hosting targets Ubuntu Server LTS on x86-64 and is awaiting actual host acceptance. Docker access and Compose supporting `up --wait-timeout` must already be installed for server operations.

If doctor reports that Docker is reachable but Docker Compose is unavailable, install or repair your distribution's Compose plugin and verify `docker compose version`. Docker Engine alone does not supply this capability in every distribution. See [Docker's Compose installation guide](https://docs.docker.com/compose/install/linux/).

## Try the foundation without Docker

```bash
gamestack pack validate packs/example/pack.yaml
gamestack install packs/example/pack.yaml --name sandbox --prepare-only
```

Installation prompts for server name and password. It creates configuration and an empty world folder under `~/.local/share/gamestack/sandbox`. It does not start a container with `--prepare-only`. The bundled example cannot be started; replace its placeholder image and healthcheck in your own pack before testing hosting.

Use `gamestack --root /srv/gamestack ...` to choose a dedicated writable storage directory. Specify the same root for subsequent commands. No command elevates privileges or changes ownership. Do not put instance storage inside a source checkout.

## Commands

| Purpose | Syntax/example | Expected result | Common failures and action |
|---|---|---|---|
| Validate pack | `gamestack pack validate packs/example/pack.yaml` | Schema-valid message | Correct YAML/types/required fields using gamepacks.md |
| Configure and launch | `gamestack install /path/to/pack.yaml --name friends` | Prepared location, then healthy server | Run doctor for host tooling; inspect pack image/permissions/ports/healthcheck; prepared files remain on startup failure |
| Prepare only | `gamestack install /path/to/pack.yaml --name friends --prepare-only --values /private/values.yaml` | Files created, no Docker call | Supply missing settings; choose a new name if instance exists |
| List configured instances | `gamestack list` | Alphabetically sorted names with container state and health | Invalid instances are skipped with a warning; use doctor to investigate |
| Start | `gamestack start friends` | Healthy after healthcheck | Check pack healthcheck and data-folder permissions; failed start may leave server running |
| Remove instance | `gamestack rm friends` | Confirms, stops and removes container; retains files and hides instance from list | Check Docker access or instance lock; failed removal retains configuration for retry |
| Stop | `gamestack stop friends` | Stopped; worlds retained | Check Docker access; inspect server state before retrying a timeout |
| Restart | `gamestack restart friends` | Stopped then healthy | Failed stop prevents start; failed health leaves data in place |
| Status | `gamestack status friends` | Container state and health, or not created | Correct missing/incomplete configuration; run doctor |
| Check prerequisites | `gamestack doctor` | Docker access and Compose capability pass | Install/start Docker and grant the operating account access |
| Create backup | `gamestack backup friends` | Verified archive location and final server state | Check disk space, permissions, and clean shutdown; partial artifacts retained |
| List backups | `gamestack backup list friends` | Newest-first IDs, UTC dates, and sizes; no integrity recheck | Check instance name, root, and backup folder access |
| Restore backup | `gamestack restore friends [BACKUP-ID]` | Confirm full data replacement; preserve a safety backup; resume only an initially running server | Check compatibility, space, shutdown, permissions, and any restore marker; follow recovery instructions |
| Verify backup | `gamestack backup verify friends BACKUP-ID` | Full archive integrity verified without extraction | Keep corrupt/incomplete archives and select another copy |
| Update Paper | `gamestack update friends --pack /reviewed/pack.yaml` | Verified stopped-state backup, recreated server, checked image/JAR and health | Keep all files; run `gamestack update friends --recover` after a failed update |
| Recover update | `gamestack update friends --recover` | Restore pre-update build/world; keep later world separately | Check old backup, image access, free disk space, and retained `.update.json` |
| Check instance | `gamestack doctor friends` | Also validates saved configuration and world-folder existence | Restore missing configuration/data; do not bypass safety checks |

Global options precede the command: `gamestack --debug --root /srv/gamestack status friends`. Debug logs include safe operation boundaries, elapsed subprocess time, and failure type, never raw server output. There is no logs command yet because arbitrary upstream logs can expose credentials.

Doctor does not establish available capacity, port reachability, save consistency, or game support. The first pack's acceptance work will expand these checks.

## Find configured instances

```bash
gamestack list
gamestack --root /srv/gamestack list
```

The command lists instance names (including installations made with `--prepare-only`), rather than available pack files. Use these names with `start`, `stop`, `restart`, and `status`. It reads local configuration and queries Docker for a quick status snapshot without changing files or taking operation locks. Use the same `--root` for listing and lifecycle commands.

Example output:

```text
Configured instances:
  friends: started (healthy)
  sandbox: stopped (not created)
```

Running containers show `started`, with `healthy`, `unhealthy`, or `starting` in parentheses when Docker reports a healthcheck result. Otherwise the container state is shown: `stopped` for a clean exit or a container that has not started, and `crashed (nonzero exit)` for an unsuccessful exit. This label is an exit-code heuristic; a forced stop can also produce a nonzero exit. Dead containers show `crashed`; paused, restarting, and removing containers retain those states. Stopped containers do not display stale health results. A missing exit code is reported explicitly.

Each instance query has a five-second timeout. If Docker is unavailable or returns an unreadable result, that instance remains listed as `unknown (status unavailable)` with a warning to run doctor. Listing still returns exit status 0 when discovery succeeds, even if live status is unavailable. Health describes a snapshot, not a guarantee that the game is ready for players. The schema still requires a pack healthcheck; the fallback covers containers without a reported health result.

Docker state, health, and exit code come from [Compose ps JSON output](https://docs.docker.com/reference/cli/docker/compose/ps/).

A missing or empty storage directory returns a friendly message and exit status 0. Incomplete, invalid, inaccessible, or symlinked instances are skipped with warnings while valid instances are still listed. Unrelated files and invalid directory names are ignored. Failure to read the storage directory returns exit status 1; check its permissions.

## Remove a configured instance

```bash
gamestack rm friends
gamestack --root /srv/gamestack rm friends --yes
```

`rm` removes an installed instance, not the original GamePack YAML file. It asks for confirmation with a default of No. Noninteractive use requires `--yes`. This is a USEFUL SOON feature included in the current release scope by request.

Removal validates the instance paths/configuration, takes its operation lock, stops the server using the pack shutdown timeout, removes its container, and verifies that no server container remains. It then writes `removed.yaml` inside the instance directory. Removed instances disappear from `list` and cannot be started through lifecycle commands.

World data in `data/`, backups, and configuration remain at their existing paths, which the command prints. The instance name stays reserved; a new install must use a different name. Images, volumes, and the project network are retained. There is no data-purge option. Data stored only in the container's writable layer is lost on removal: packs must put all persistent saves in the declared data mount, as required by the schema. [Docker container removal behavior](https://docs.docker.com/reference/cli/docker/compose/rm/).

Docker must be accessible, even for an instance created with `--prepare-only`, so GameStack can verify whether a container exists. A failed stop prevents removal; a failed removal or verification does not mark the instance removed. If writing the marker fails, the container may already be gone; retained configuration permits retry. Interrupted operations may leave a lock, as described below.

To reactivate a removed instance during development, verify that no operation is running and that its original configuration and data paths remain intact, then delete only its `removed.yaml` marker. Run `gamestack doctor friends` and `gamestack start friends` with the original root. No automated recovery or deletion of retained files is provided yet.

## Recovering from interrupted development work

An existing instance is never overwritten by install. If preparation fails, its files remain and the missing completion marker prevents use. Inspect them and choose a fresh instance name; preserve any world files before manual cleanup.

Operations use `.operation.lock` inside the instance directory. If interrupted, first verify no GameStack process is still operating on that instance. Only then remove that specific stale lock file and retry. There is no automatic lock breaking or recursive cleanup.

The default published ports bind only to localhost; friends cannot connect remotely in this milestone. Data folders belong to the installing user with private permissions. Packs running another UID require deliberate provisioning as part of the first real pack's setup work.

## Tests

```bash
python3 -m pip install packaging
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

After editable installation, `python -m unittest discover -s tests -v` also works. Tests use temporary synthetic worlds and mock Docker. Passing them does not constitute real Docker or game-server acceptance.

## Experimental Paper installation

`gamestack install packs/minecraft-paper/pack.yaml --bind-address 0.0.0.0`
configures and starts the experimental private Java server. Follow the
[Paper walkthrough](../packs/minecraft-paper/README.md) for EULA acceptance,
profile names, defaults, expected results, networking, and failure recovery.

`--bind-address` is available for schema 2; omitted means localhost unless an
interactive user explicitly chooses exposure. All-interface binding can expose
the game port publicly. Existing schema 1 instances are unchanged.

## Manual backups

```bash
gamestack backup friends
gamestack backup list friends
gamestack backup verify friends BACKUP-ID
```

Replace `BACKUP-ID` with an ID from the listing, without a file extension. Global
options still precede the command. Instance names `list` and `verify` remain valid:
`gamestack backup list` creates a backup of the instance named `list`.

Creation briefly stops a running server without an extra confirmation prompt,
checks its clean exit, captures and verifies its files, then restarts it and checks
health. Players will be disconnected. Previously stopped or never-started instances
remain stopped. Docker must be available even when no container has been created.
Crashes, ambiguous states, and unsuccessful shutdowns block capture; inspect status
and resolve the server problem before retrying.

Success prints a verified ID, absolute location, and final server state. Archives
live in `<root>/<instance>/backups/` as timestamped, uniquely identified `.tar` files.
They contain the entire `data/` folder and saved instance, pack, and Compose
configuration. Modes and modification times are retained; links and special files
are rejected. Backup files are private (`0600`) inside a private folder (`0700`) on
Linux. They are **unencrypted and may contain credentials**; keep them private.
Do not modify source data through other tools while capture runs.

Archives are uncompressed, so allow approximately the full data/configuration size
plus headers, manifest allowance, and at least 64 MiB of free-space reserve. All
backups are retained. There is no automatic pruning, scheduling,
custom destination, exclusion list, or compression option in this milestone.

Listing works without Docker, displays IDs, UTC timestamps and sizes newest first,
and does not recheck integrity. Empty listings succeed. Verification reads every
archived file and checks its hash, metadata, and archive structure without extracting
anything. Both commands remain available after `rm`, or if the active data folder
is missing, provided the instance and backup folders remain at their original paths.
Verification detects corruption; it neither authenticates an archive nor proves
that Minecraft can restore it. Minecraft Paper restore acceptance passed separately by user confirmation; archive verification alone does not establish recovery.

Failures return exit status 1, and interrupts return 130:

- Insufficient space or unsafe source paths found before shutdown leave the server
  running. Free space or correct the source layout and retry.
- Failed/unverified shutdown prevents capture and automatic restart. Check
  `gamestack status friends` and `gamestack doctor friends` before restarting.
- Capture failure after verified shutdown still attempts to resume an initially
  running server; the backup command remains unsuccessful.
- Failed restart after a verified backup retains the archive and prints its usable
  location. Check status/doctor before retrying `gamestack start friends`.
- Partial `.partial` artifacts are retained and warned about in listings, never
  treated as completed backups. Older backups and source files are preserved.
- Interrupts retain created artifacts and do not automatically restart the server.
  Check status before acting. Abrupt termination may leave the existing operation
  lock; follow the interrupted-operation instructions above.

If GameStack reports that backup publication or directory changes could not be
synced to disk, retain the `.tar` and any `.partial` copy. The archive passed
integrity verification, but durable completion was not confirmed. Check disk
health, free space, and permissions; use `gamestack backup list friends` and
`gamestack backup verify friends BACKUP-ID` to inspect the retained archive.
After resolving the storage problem, create a fresh backup. Verification alone
does not establish that a failed directory sync has been repaired.

A nonzero result can coexist with a valid retained backup. Read the result before
retrying. Do not manually remove partial files or stale locks while an operation is
still running. No backup deletion is performed by GameStack.

## Restore backups

```bash
gamestack restore friends
gamestack restore friends BACKUP-ID
gamestack restore friends BACKUP-ID --yes
```

Restore replaces the **entire** `data/` folder, including world files, player lists,
plugins, and server settings stored there. Files created after the selected backup
are absent from the restored folder. Current GameStack instance, GamePack, and
Compose configuration remain active, including current user settings. Archived
GameStack configuration is validated but never executed or installed.

Without an ID, an interactive numbered list shows completed backups newest first,
with UTC dates and sizes. Enter a number, then confirm `[y/N]`; blank selection or
answering no cancels without changing files. Listing does not verify integrity.
There is no automatic newest-backup selection. Scripts must supply an ID and
`--yes`. Use IDs from `gamestack backup list friends`, without `.tar`; paths and
`latest` are not accepted. Global options such as `--root` precede `restore`.

Before confirmation, GameStack shows the target folder, player-disconnection
warning, safety-backup behavior, and intended final server state. After confirmation
it takes the operation lock, rechecks state, verifies the selected archive, and
stages a complete checked copy privately inside the instance folder. It then stops
a running server gracefully and creates a verified safety backup of current data.
Existing but inaccessible, linked, specially owned, or mounted data blocks restore;
it cannot be skipped. If `data/` is genuinely missing, an explicit warning explains
that no current world can be backed up. `--yes` accepts that warning.

The stopped service container is removed before switching folders so the next
start uses the new data mount; no volumes are deleted. Saves must live inside the
pack's declared data folder, not the container's writable layer. See
[Docker's stopped-container removal behavior](https://docs.docker.com/reference/cli/docker/compose/rm/).
The old data folder and safety archive both remain available. A server that was
running restarts and must pass its health check. Stopped, crashed, and never-started
servers remain stopped, with `health not tested` in the result. Use `gamestack start
friends` when ready. Docker must be accessible for every restore.

Success prints the restored ID, safety archive location (or the missing-data
exception), retained recovery directory, and final state. Safety archives appear in
`backup list` and can be restored with the same command. A safety archive captured
from a confirmed stopped crashed server records `initial_state: crashed`; it
preserves that state, but does not promise those files form a healthy world.
Older GameStack versions may reject this metadata; use the current restore-capable
version to verify or restore it. Existing schema-1 backups remain readable.

Restore requires the original configured instance, original operating account and
ownership, and an identical validated GamePack definition, including its pinned
image and fixed settings. Different GamePack revisions are rejected even if their
version labels match. Removed instances, damaged GameStack configuration, other
hosts, imported archive paths, partial restores, and version rollback are not
supported. Archive checks detect corruption, not authenticity; keep backup storage
private. Do not let other processes or users write instance files during restore.

Allow space for the full staged data, a full safety archive, archive overhead, and
at least 64 MiB reserve. The original data is retained by renaming, without another
full copy. Archives, old data, and partial staging files are never automatically
pruned. Retained directories are private; extracted files use ordinary archived
permissions and modification times under the operating account. Special permission
bits and directories inaccessible to that account are rejected.

Failures return 1; interruptions return 130. Typical outcomes:

- Invalid backups, incompatible packs, insufficient space, or changed state before
  shutdown leave current data intact. Correct the cause and retry.
- Failed or unverified shutdown blocks replacement and automatic restart. Inspect
  status and doctor before retrying.
- A safety-backup failure blocks replacement. After a confirmed clean shutdown,
  GameStack attempts to restart the untouched original server if it was running.
- Once replacement begins, failures retain all copies and the restore marker.
  There is no automatic rollback. Follow the recovery procedure below.
- Failed startup or health verification attempts to stop the restored server.
  Output says whether stopping was confirmed; if not, it may still be writing.
- Interruptions do not trigger an automatic restart. Check status; an interrupted
  startup may already have started the server. Abrupt termination may also retain
  `.operation.lock`.

## Interrupted restore recovery

An unfinished restore leaves `<root>/<instance>/.restore.json`. It blocks start,
restart, backup creation, removal, and further restores. Status, stop, backup list,
and backup verify remain available, including when the active data folder is
missing. Clearing an operation lock alone does **not** clear this protection.

This is a manual recovery procedure for the original operating account. Never
execute text from the marker as commands, overwrite an existing directory, merge
folders, or delete any data copies. If the layout differs from the cases below,
retain everything and investigate before proceeding.

1. Check that no GameStack operation is still running. Only then clear a stale
   `.operation.lock` as described above. Run `gamestack stop INSTANCE`, then
   `gamestack status INSTANCE`, using the original `--root`. Do not move files if
   stopping cannot be confirmed. Stop external writers as well.
2. Read `.restore.json`. Confirm its instance and that `work_directory` is a direct,
   real `.restore-<hex>` child of this instance folder, not a link or another path.
   Confirm any `safety_backup` is inside this instance's `backups/` folder. The
   recorded phase is intent written **before** the corresponding operation, so
   inspect which directories actually exist.
3. Run `gamestack backup verify INSTANCE BACKUP-ID` for the selected backup and,
   when recorded, the safety backup. At least the backup you intend to recover
   must verify successfully. Keep corrupt copies too. These checks do not prove
   that the server will start successfully.
4. Reconcile the folders using this table. All names are relative to the validated
   instance or work directory. Rename only into an absent destination, preserving
   all other directories and their ownership.

| Marker phase | Expected layouts and recovery |
| --- | --- |
| `remove-container` | Folder replacement has not begun. Leave current `data/` in place, or absent if originally missing. The stopped container may already be removed. |
| `preserve-current` | If current `data/` exists and `previous-data/` does not, leave it. If `data/` is absent and work `previous-data/` exists, rename `previous-data/` back to instance `data/`. If both are absent, proceed only when `had_data` is false. |
| `install-staged` | If work `data/` exists and instance `data/` is absent, rename work `previous-data/` back to instance `data/` when present; otherwise leave data absent only when `had_data` is false. If work `data/` is absent and instance `data/` exists, the complete staged copy was installed; leave it and retain `previous-data/`. |
| `start` or `complete` | The restored data was installed. Leave instance `data/` and retained `previous-data/` in place. Confirm the server is stopped before further recovery; a startup may have succeeded even if the command was interrupted. |

5. Once the folders match a case above and the chosen archive verifies, rename
   `.restore.json` into that work directory as `reconciled-restore.json`, only if
   that name is absent. Keep this record. Do not clear a marker merely to suppress
   an error. If data is present, run `gamestack doctor INSTANCE` before starting.
6. To recover the pre-restore state, run `gamestack restore INSTANCE SAFETY-BACKUP-ID`.
   To retry the chosen restore, use its original ID instead. Because the server is
   now stopped, restoration leaves it stopped. Then run `gamestack start INSTANCE`
   and check the world in-game. If there was no previous data, only the selected
   backup is available. Keep all safety archives and retained directories.

If interruption occurred before `.restore.json` was created, active data was not
replaced. Private `.restore-<hex>` staging folders or marker `.partial` files may
remain. Confirm the server state and retry normally; leave partial files retained.


## Command help

Every command supports `--help` and `-h`, including nested commands:

```bash
gamestack --help
gamestack install --help
gamestack pack validate --help
gamestack backup --help
gamestack backup list --help
gamestack backup verify --help
gamestack restore --help
```

Help explains arguments, options, examples, expected behavior, and relevant safety
or failure guidance. It exits successfully without prompting, contacting Docker,
or reading/writing instance files. No installed instance is needed to read help.
Use `gamestack COMMAND --help` for `list`, `rm`, `start`, `stop`, `restart`, `status`,
and `doctor` as well.

Global `--root` and `--debug` options go before the command, for example
`gamestack --root /srv/gamestack --debug status friends`. Each command's help
reminds you of this placement. The top-level help shows the default storage path.
For backup creation, `list` and `verify` remain valid instance names; adding
`--help` after those words displays their corresponding focused help pages.

## Explicit Paper updates

Use a maintainer-reviewed Minecraft Paper candidate `pack.yaml` and its sibling
`upstream-lock.json`. The included acceptance candidate is
`packs/minecraft-paper/candidates/26.2-129/pack.yaml`, pinned to Paper 26.2
build 129; the installed pack remains on build 121. The CLI never discovers or
selects the latest release automatically.

```bash
gamestack update friends --pack packs/minecraft-paper/candidates/26.2-129/pack.yaml
# For scripts, add --yes.
```

The server must be running and healthy. The command checks saved configuration,
backup space, the candidate lock and image before downtime. It shows the old
and new pins, expected shutdown/backup/startup downtime, backup folder, and
recovery consequence without printing saved settings. After confirmation,
GameStack stops the server, verifies shutdown, makes and verifies a full backup,
installs the candidate, forces container recreation, waits for health, then
checks the actual container image and Paper JAR SHA-256. Success means all
checks passed. If a check fails, preserve the printed update directory and
journal; the current server may be stopped or uncertain. Run `gamestack status
friends` before recovery.

```bash
gamestack update friends --recover
# For scripts, add --yes.
```

Recovery works after an interrupted/failed update and after the last successful
update. It verifies the old archive and obtains the old pinned image before
stopping the current server. It retains the current `data/` folder in the
printed `.update-.../post-update-data/`, restores the pre-update configuration
and world, then checks the old server's health and JAR. **The active world goes
back to the pre-update state.** Changes made after the update remain only in
the retained copy (and a verified post-update archive when one could be made).
All archives and world copies are kept; v0.1 has no pruning. Allow space for a
full pre-update archive, a staged restore copy, a post-update archive when
possible, and the retained later world. The prior image must remain obtainable.

An unfinished `.update.json` blocks start, restart, backup creation, restore,
removal, and another update. `status`, backup listing, and backup verification
remain available. If `.operation.lock` was left by an interruption, verify no
GameStack process is still operating before removing that lock file. Then use
`--recover`; do not edit the journal or move worlds by hand. On a recovery
failure, retain everything, correct the reported disk/Docker/permission issue,
and retry `--recover`. If the pre-update backup was never created, recovery
restores the original configuration and checks that the original server starts;
there is no later world to replace.
