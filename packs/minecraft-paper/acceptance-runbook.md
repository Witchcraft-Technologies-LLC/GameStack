# Paper playable acceptance runbook

Run these steps on your disposable Ubuntu Server 24.04 x86-64 host as a non-root
user with Docker/Compose access and 8 GiB RAM. You operate the host and clients;
return sanitized results for the [acceptance record](acceptance.md). Do not run
failure tests against your working server. These are evaluator commands, not new
normal-operation requirements. No release workflow is needed.

## 1. Record the existing successful session

Supply the actual test date, host OS/architecture, clean-install status, operating
account privilege, walkthrough followed, client version, whether the client was
on a second machine, allowlist status, and revision/pins if known. Unknown fields
stay unknown. Your successful setup and legitimate-account join already count as
user-reported evidence; repeat only conditions that were not established.

For subsequent runs, record the exact candidate before copying it to the host:

```bash
git rev-parse HEAD
git status --short
sha256sum packs/minecraft-paper/pack.yaml packs/minecraft-paper/upstream-lock.json
```

A commit hash alone does not identify uncommitted changes. Retain the exact source
snapshot used, including untracked pack/test files; do not include private values
or worlds. Record its archive checksum when transferring it. Do not change pins
between acceptance steps.

Follow [CLI setup](../../docs/cli.md#setup), then the [pack walkthrough](README.md).
Record `cat /etc/os-release`, `uname -m`, `id -u`, `python --version`,
`docker version`, `docker compose version`, `free -h`, and `df -h .` privately.
Do not accept the EULA unless you personally agree.

## 2. Run automated host checks

TCP 25565 must be free. Schedule this separately from the working server; do not
stop an unrelated server merely to run the harness.

```bash
GAMESTACK_PAPER_TEST=1 GAMESTACK_MINECRAFT_EULA=TRUE GAMESTACK_PAPER_OWNER=YourJavaName python -m unittest discover -s tests/integration -p test_paper_docker.py -v
```

The printed private `evidence.json` records observed host/container versions, pins,
startup/shutdown timings, bindings, save markers, and result. Retain the entire
printed test directory, including failed runs. An incomplete report or skipped
test is not a pass. Inspect local logs if save-marker recognition fails; do not
weaken the assertion without establishing the pinned server's actual save output.
The harness verifies configured admin-service disable flags; listener inspection
and actual joins remain manual checks. It does not record arbitrary container
environments, logs, or player chat.

## 3. Prepare a disposable manual instance

Use a separate test host/VM if the working server occupies TCP 25565. These shell
variables must remain in this terminal; they identify only the new test instance.

```bash
PAPER_ACCEPT_ROOT=$(mktemp -d "$HOME/gamestack-paper-acceptance.XXXXXXXX")
PAPER_ACCEPT_NAME=paper-manual
export PAPER_ACCEPT_ROOT PAPER_ACCEPT_NAME
gamestack --root "$PAPER_ACCEPT_ROOT" install packs/minecraft-paper/pack.yaml --name "$PAPER_ACCEPT_NAME" --bind-address 0.0.0.0
```

Accept the EULA yourself and enter test player names. Define a helper using the
runtime's existing project-name calculation so evaluator Docker commands address
exactly this instance:

```bash
paper_compose() {
  python - "$@" <<'PY'
import os, subprocess, sys
from pathlib import Path
from gamestack.runtime import Runtime
root = Path(os.environ['PAPER_ACCEPT_ROOT']).resolve(strict=True)
assert root.parent == Path.home().resolve() and root.name.startswith('gamestack-paper-acceptance.')
runtime = Runtime(root)
directory, _ = runtime.inspect(os.environ['PAPER_ACCEPT_NAME'])
raise SystemExit(subprocess.run(runtime.compose_command(directory) + sys.argv[1:]).returncode)
PY
}
paper_compose ps --all
```

Keep a per-step record: UTC time, candidate, command/action, expected result,
observed result, exit status, evidence file/screenshot, and recovery result. Keep
raw logs private; redact player names, addresses, chat, and secrets before sharing.

## 4. Players, persistence, and shutdown

1. Join from the second machine with the allowlisted owner using Java 26.2.
2. Have the legitimate unlisted friend attempt to join: expect rejection. Run
   `/whitelist add FriendName`, verify admission, then `/whitelist remove FriendName`
   and verify rejection on a new connection. Record `/whitelist list` privately.
3. Place distinctive blocks in Overworld, Nether, and End. Record dimensions,
   coordinates, screenshots, inventory quantities, and logout positions. Use two
   players logging out in different dimensions; repeat for the remaining dimension.
4. Stop/start, then test `restart`, then recreate the stopped container as below.
   After each transition check all recorded world/player state and repeat friend
   add/remove tests. Verify the owner still has operator privileges. Compare saved
   allowlist state *before* issuing new commands so resets cannot be hidden.

```bash
PAPER_ACCEPT_CID=$(paper_compose ps --quiet server)
time gamestack --root "$PAPER_ACCEPT_ROOT" stop "$PAPER_ACCEPT_NAME"
docker inspect --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.FinishedAt}}' "$PAPER_ACCEPT_CID"
docker logs --tail 150 "$PAPER_ACCEPT_CID"
gamestack --root "$PAPER_ACCEPT_ROOT" start "$PAPER_ACCEPT_NAME"
gamestack --root "$PAPER_ACCEPT_ROOT" restart "$PAPER_ACCEPT_NAME"
```

Require save-complete messages, exit 0, no OOM, and no forced kill. Record the
shutdown within restart from logs as well. A successful CLI stop alone is not
proof of a consistent save. To recreate, stop and check shutdown again, then:

```bash
paper_compose rm --force server
gamestack --root "$PAPER_ACCEPT_ROOT" start "$PAPER_ACCEPT_NAME"
paper_compose ps --quiet server
```

The container ID must change while world/player and access-control state survive.
Do not use volume deletion, delete data, or edit generated Compose/metadata.

## 5. Network and external join

```bash
PAPER_ACCEPT_CID=$(paper_compose ps --quiet server)
docker inspect --format '{{json .NetworkSettings.Ports}}' "$PAPER_ACCEPT_CID"
PAPER_ACCEPT_PID=$(docker inspect --format '{{.State.Pid}}' "$PAPER_ACCEPT_CID")
sudo nsenter -t "$PAPER_ACCEPT_PID" -n ss -lntup
```

Expect only TCP 25565 published and the game listener; investigate any additional
listener. Confirm disabled RCON/query/JMX properties in `data/server.properties`
and the fixed `ENABLE_SSH=FALSE` setting in the pack. From the second machine,
verify game connectivity and that administrative endpoints are unavailable; record
any unrelated host services separately. Configuration flags alone do not prove
network exposure. Never share a full `docker inspect` environment dump.

You manually forward only TCP 25565 if needed and have an external friend join
from a genuinely separate internet connection. Record success, or the specific
CGNAT/double-NAT/ISP limitation. Existing external-join evidence can satisfy this
step. Do not claim universal reachability or change broad firewall policies.

## 6. Isolated crash and failure scenarios

Use a fresh disposable instance/VM for these steps, repeating section 3 in a fresh
terminal. Preserve the persistence-test world. Capture command exit status with
`echo $?` immediately after each failing GameStack command. Require failure rather
than a healthy/success claim, retained files, useful next steps, and recovery after
removing the fault. A preparation message followed by a startup failure is not a
false startup-success claim. Record hashes of a stopped-state sentinel file and
configuration before/after faults; do not expect live world/log hashes to be static.

### Crash

On the disposable world only:

```bash
PAPER_ACCEPT_CID=$(paper_compose ps --quiet server)
docker inspect --format '{{.RestartCount}} {{.HostConfig.RestartPolicy.Name}}' "$PAPER_ACCEPT_CID"
docker top "$PAPER_ACCEPT_CID" -eo pid,comm
```

Identify the **host PID of java** in that exact container. Run `sudo kill -KILL
JAVA_HOST_PID`, replacing the placeholder with that verified numeric PID. This
simulates a process crash without an administrative Docker stop/kill suppressing
restart policy. Recheck restart count, `gamestack status`, health, and actual rejoin.
Keep the crash evidence separate from normal shutdown results.

### Occupied port

Before installing a fresh instance, run `python -m http.server 25565 --bind
127.0.0.1` in a second terminal. Attempt the section 3 install; expect startup
failure and retained instance files. Stop the HTTP fixture with Ctrl-C, then use
`gamestack --root "$PAPER_ACCEPT_ROOT" start "$PAPER_ACCEPT_NAME"` to recover.
Do not reinstall over the retained instance.

### Blocked initial server downloads

Prepare a fresh instance with `--prepare-only` added to section 3's install command.
Then create its container without starting it:

```bash
paper_compose pull server
paper_compose create server
PAPER_ACCEPT_CID=$(paper_compose ps --all --quiet server)
docker inspect --format '{{json .NetworkSettings.Networks}}' "$PAPER_ACCEPT_CID"
```

Record the unique Compose network name and disconnect **only this container**:
`docker network disconnect NETWORK_NAME "$PAPER_ACCEPT_CID"`. Run GameStack start;
expect download/startup failure within the existing bounded timeout. Recover with
`docker network connect NETWORK_NAME "$PAPER_ACCEPT_CID"`, then GameStack start.
Do not alter host firewall rules or disconnect the working server. If the engine
will not start a disconnected container, record that as fixture failure, not proof
of blocked-download handling; use an offline disposable VM with the image cached
and no downloaded Paper artifacts instead.

### Unwritable data

Prepare a fresh instance without starting it, then:

```bash
PAPER_ACCEPT_MODE=$(stat -c %a "$PAPER_ACCEPT_ROOT/$PAPER_ACCEPT_NAME/data")
chmod u-w "$PAPER_ACCEPT_ROOT/$PAPER_ACCEPT_NAME/data"
gamestack --root "$PAPER_ACCEPT_ROOT" start "$PAPER_ACCEPT_NAME"
# Record failure, then restore even if the preceding command failed:
chmod "$PAPER_ACCEPT_MODE" "$PAPER_ACCEPT_ROOT/$PAPER_ACCEPT_NAME/data"
gamestack --root "$PAPER_ACCEPT_ROOT" start "$PAPER_ACCEPT_NAME"
```

### Insufficient disk

Use another disposable VM. Prepare its fresh instance without starting it. Mount a
bounded 64 MiB temporary filesystem over its **empty test data directory**. First
run this guard; stop if it fails. It rejects symlinks, unexpected roots, and data
that would be hidden by the mount:

```bash
python - <<'PYGUARD'
import os
from pathlib import Path
root = Path(os.environ['PAPER_ACCEPT_ROOT'])
data = root / os.environ['PAPER_ACCEPT_NAME'] / 'data'
assert root.resolve(strict=True).parent == Path.home().resolve()
assert root.name.startswith('gamestack-paper-acceptance.')
assert root == root.resolve(strict=True) and data == data.resolve(strict=True)
assert data.is_dir() and not any(data.iterdir())
print('Validated empty disposable data directory:', data)
PYGUARD
```

Then mount:

```bash
sudo mount -t tmpfs -o "size=64m,uid=$(id -u),gid=$(id -g),mode=700" tmpfs "$PAPER_ACCEPT_ROOT/$PAPER_ACCEPT_NAME/data"
gamestack --root "$PAPER_ACCEPT_ROOT" start "$PAPER_ACCEPT_NAME"
```

Confirm actual ENOSPC evidence in private logs; an unrelated download failure is
not a disk-test pass. Stop the test container. Before unmounting, preserve all
partial files in a new private directory on the VM's main disk:

```bash
gamestack --root "$PAPER_ACCEPT_ROOT" stop "$PAPER_ACCEPT_NAME"
PAPER_DISK_COPY=$(mktemp -d "$HOME/gamestack-paper-disk-evidence.XXXXXXXX")
cp -a "$PAPER_ACCEPT_ROOT/$PAPER_ACCEPT_NAME/data/." "$PAPER_DISK_COPY/"
diff -qr "$PAPER_ACCEPT_ROOT/$PAPER_ACCEPT_NAME/data" "$PAPER_DISK_COPY"
```

Only after copy and comparison succeed, unmount with `sudo umount
"$PAPER_ACCEPT_ROOT/$PAPER_ACCEPT_NAME/data"`. Copy the retained files back with
`cp -a "$PAPER_DISK_COPY/." "$PAPER_ACCEPT_ROOT/$PAPER_ACCEPT_NAME/data/"` and
retry GameStack start. Record whether partial downloads recover. Keep the copy;
never exhaust the host's main disk or unmount before preserving the evidence.

### Health failure

On a healthy disposable instance:

```bash
PAPER_ACCEPT_CID=$(paper_compose ps --quiet server)
docker pause "$PAPER_ACCEPT_CID"
gamestack --root "$PAPER_ACCEPT_ROOT" start "$PAPER_ACCEPT_NAME"
# Record nonzero result; always recover the fixture:
docker unpause "$PAPER_ACCEPT_CID"
gamestack --root "$PAPER_ACCEPT_ROOT" start "$PAPER_ACCEPT_NAME"
```

Record whether Compose rejects the paused state or reaches its health timeout.
To prove the health-timeout path specifically, use `docker top` as in the crash
step, send `sudo kill -STOP JAVA_HOST_PID`, run GameStack start, and always send
`sudo kill -CONT JAVA_HOST_PID` afterward before retrying. The container remains
running while the game stops responding. A health failure alone is not expected
to trigger the container restart policy.

## 7. Removal and closeout

Last, stop the disposable manual instance and take a private file-hash inventory.
Run `gamestack --root "$PAPER_ACCEPT_ROOT" rm "$PAPER_ACCEPT_NAME"` and answer its
confirmation. Require container removal, all prior files retained unchanged, and
a new removal marker. Retry the original install command: require rejection and
unchanged retained files. The automated harness also checks retired-name rejection.

Return the evidence table and sanitized automated report. Mark a criterion passed
only from its corresponding evidence; record failures and reruns without erasing
the original results. Fix demonstrated defects and rerun affected checks plus the
unit suite. Repeat player persistence/lifecycle after pack or runtime changes.
The pack remains experimental and unsupported/unsellable after this milestone;
backup, restore, updates, and release clearance are separate gates.

## Manual backup validation

**Passed — user-confirmed manual command validation, 2026-09-06.** The user
reports that the backup command works as intended when exercised manually.
This is additional evidence beyond the earlier playable milestone sign-off.
Backups remain uncompressed `.tar` archives; no backup behavior changed.

That 2026-09-06 confirmation did not enumerate individual scenarios. On 2026-09-25,
the user explicitly signed off all backup and restore checklist criteria, and
separately confirmed corrupted-backup rejection and in-game world recovery. Detailed
per-scenario logs, host details, and tested revision were not supplied. The opt-in
Paper harness was not run by the agent. See [acceptance.md](acceptance.md).

For repeat validation:
Use only this runbook's disposable instance. Run `gamestack backup INSTANCE` while
running, check healthy resume, then stop and repeat to confirm it stays stopped.
Record IDs from `gamestack backup list INSTANCE` and run
`gamestack backup verify INSTANCE BACKUP-ID` for each. Keep archived configuration
private. Integrity checks do not replace a later actual restore test.

## Manual restore validation

**Passed — user-confirmed manual testing, 2026-09-06.** The user reports that
manual testing of restore passed. This records the manual result; individual
scenarios, host details, command output, and the tested revision were not supplied.
The user signed off all restore checklist criteria on 2026-09-25; see
[acceptance.md](acceptance.md#restore-acceptance--passed-by-user-confirmation).
Safe updates and final release acceptance remain open.

For repeat validation:

Use this runbook's disposable instance and the same root/account throughout. Do
not use an irreplaceable world. Record the tested revision and pack/image pins.

1. Join in Minecraft Java, build a recognizable structure, and note its coordinates.
   Run `gamestack backup INSTANCE`; record the resulting ID as the earlier backup.
2. Change the structure and your inventory. Run `gamestack restore INSTANCE` and
   choose the earlier backup. Confirm the printed scope and disconnection warning.
3. Record the generated safety backup ID and retained recovery directory. Confirm
   healthy completion, reconnect, and check the earlier structure/player state.
4. Run `gamestack restore INSTANCE SAFETY-BACKUP-ID`. Reconnect after healthy
   completion and verify the later structure/player state returns.
5. Stop the server, restore the earlier backup again, and verify the result says
   `stopped (health not tested)`. Start it explicitly and check the world again.
6. Verify both archive IDs with `gamestack backup verify INSTANCE BACKUP-ID`.
   Confirm current GameStack configuration files were unchanged and preserve all
   archives/recovery folders. Complete the failure/recovery scenarios in
   [acceptance.md](acceptance.md#restore-acceptance--passed-by-user-confirmation) using synthetic data.

Record actual outcomes for repeat runs; unit tests or archive verification alone
do not establish in-game recovery. For failures or interruptions, follow the
[restore recovery procedure](../../docs/cli.md#interrupted-restore-recovery).

## 7. Safe update acceptance (separate release gate)

The user accepted this gate on 2026-09-26. The commands below remain the
repeatable procedure; the local failure run used port 25566, while a direct
Ubuntu harness result and live interruption log were not supplied. See the
[acceptance record](acceptance.md#safe-update-acceptance--passed-by-user-sign-off-2026-09-26).

Use a new disposable Ubuntu instance, not the working server. The commands
above use Bash syntax. In fish, set variables with `set NAME value`; do not use
`${NAME:?message}` or `NAME=$(command)`. `--root` names the parent directory
of the installed instance folder, and the instance name must match that folder.
Confirm `instance.yaml`, `pack.yaml`, and `compose.yaml` exist there before
running a deliberate failure test.

Build 129 was the
newest stable Paper 26.2 build returned by the official Downloads Service on
2026-09-25 (published 2026-09-23). The included candidate changes the GamePack
version to `0.1.0-dev2` and Paper build from 121 to 129; its image digest stays
at the reviewed build 121 image pin. Verify the API again before release:
`https://fill.papermc.io/v3/projects/paper/versions/26.2/builds`.
The candidate lock records build 129 JAR SHA-256
`b1d8f6bfa1b6101fa8e947b53041cb3bdf5540e7b83b6547ca19ba7edefeb083`.
Record candidate and lock file hashes with the tested revision.

Opt-in automated recreation/persistence, checksum failure, and recovery test:

```bash
GAMESTACK_PAPER_UPDATE_TEST=1 GAMESTACK_MINECRAFT_EULA=TRUE GAMESTACK_PAPER_OWNER=YourJavaName python -m unittest discover -s tests/integration -p test_paper_update_docker.py -v
```

The tester must agree to the EULA personally. This harness retains its private
disposable world and writes `update-evidence.json`; an opt-in skip or incomplete
report is not a pass. It does not establish a player reconnect.

For manual validation, start a disposable build 121 instance using section 3.
Build a recognizable in-game marker, note inventory/location and the reported
Paper build, then run:

```bash
gamestack --root "$PAPER_ACCEPT_ROOT" update "$PAPER_ACCEPT_NAME" --pack packs/minecraft-paper/candidates/26.2-129/pack.yaml
gamestack --root "$PAPER_ACCEPT_ROOT" status "$PAPER_ACCEPT_NAME"
```

Check the pre-update backup ID with `gamestack backup verify`. Reconnect and
confirm world/player state and Paper build 129. Create a second recognizable
marker, then run `gamestack --root "$PAPER_ACCEPT_ROOT" update
"$PAPER_ACCEPT_NAME" --recover`. Reconnect and confirm build 121 and the
original marker. The second marker must be absent from the active world and
present in the printed `post-update-data` copy. Keep both copies and the backup.

On another disposable instance, change only the candidate lock's JAR hash to an
incorrect 64-character lowercase hex value, apply it, and confirm the command
fails after recreation, preserves `.update.json`, and blocks backup/start.
Run `--recover` and confirm the pre-update world returns. Exercise interruption
and insufficient-space cases on disposable worlds, recording the journal phase,
retained files, and successful retry. Do not mark the update checklist passed
without each observed result; report redacted host/revision, pins, backup IDs,
health, container image ID, JAR hash, and player observations.
