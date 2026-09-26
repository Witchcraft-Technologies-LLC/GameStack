# GameStack — private server for Minecraft Java (Paper)

**Experimental playable milestone.** Manual backup, integrity checks, and safe
restore are implemented and passed user-confirmed acceptance. Explicit updates
are implemented; live update acceptance remains pending. Use a disposable new world for evaluation. This pack is not yet a supported
or sellable release. The playable milestone passed all manual acceptance
checks, confirmed by the user on 2026-09-06; see [the acceptance record](acceptance.md).

NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.
GameStack provides the integration; PaperMC and itzg provide upstream server tooling.
Minecraft remains Mojang/Microsoft's software. Players need Minecraft Java Edition.

## Prepare the host

The acceptance target is **Ubuntu Server 24.04 LTS x86-64**, using a normal non-root
account with Docker Engine and the Docker Compose plugin installed and accessible.
Windows/macOS tooling does not establish hosting support. Run `gamestack doctor`
to check prerequisites. GameStack does not install Docker or change permissions
with sudo. Docker access grants powerful host access; use a trusted operating account.

Start testing with 8 GiB host RAM, an SSD, and space for a growing world. The pack
allocates 4 GiB Java heap and defaults to eight players, survival, normal difficulty.
These are initial testing targets, not measured capacity guarantees. No additional
plugins or Bedrock cross-play are included; Paper supplies its own built-in spark profiler. Paper may differ from vanilla technical/redstone
behavior. Initial downloads and authenticated player lookup require internet access.

## Install and connect

From a GameStack source checkout, first follow [development CLI setup](../../docs/cli.md#setup)
to create a virtual environment and install the CLI. Then:

```bash
gamestack pack validate packs/minecraft-paper/pack.yaml
gamestack install packs/minecraft-paper/pack.yaml --bind-address 0.0.0.0
```

Read and explicitly accept the linked Minecraft EULA, enter a server description,
your **Java profile name** (not Xbox gamertag), then the initial player names,
including yourself, separated by commas without spaces. For a solo evaluation,
enter your own name. Names use 3–16 ASCII letters, digits, or underscores.

Expected result: GameStack prints the persistent world folder and reports healthy
once the server responds, allowing up to ten minutes after container startup.
Image pulling has a separately bounded process budget. First startup downloads the
pinned server and generates a new world. A failed startup retains the instance;
check `gamestack status minecraft-paper` before retrying `gamestack start minecraft-paper`.
Do not rerun install over an existing instance.

Connect using Minecraft Java **26.2**, Multiplayer → Add Server, and the hosting
machine's LAN IP followed by `:25565`. Only allowlisted authenticated players can
join; the configured owner receives operator privileges. Health means a status
probe succeeded, not that a real player has joined successfully.

`0.0.0.0` exposes TCP 25565 on every IPv4 interface, including a public interface
if the host has one. Omit the flag to choose exposure interactively, or pass
`--bind-address 127.0.0.1` for host-only access. A specific numeric LAN address can
limit binding to that interface. No RCON, query, JMX, or SSH admin port is published.

For friends outside your home, manually forward **TCP 25565** on your router to the
host's reserved LAN address and allow that specific game port through your host
firewall as appropriate. Share your public address privately. GameStack changes
neither router nor firewall rules and does not certify internet reachability.
CGNAT, double NAT, ISP restrictions, and changing public addresses can prevent
connections. Do not forward administrative ports. Do not assume a host firewall
blocks Docker-published ports; verify exposure from another machine.

## Normal operations

```bash
gamestack status minecraft-paper
gamestack list
gamestack stop minecraft-paper
gamestack start minecraft-paper
gamestack restart minecraft-paper
gamestack doctor minecraft-paper
```

Start/restart waits for health. Stop allows 120 seconds for graceful shutdown;
Docker can force termination after that timeout. Do not treat forced termination
as proof of a consistent world save. A crash normally triggers container restart;
a healthcheck failure alone does not automatically restart the server.

As the owner, manage players in the in-game chat:

```text
/whitelist add FriendName
/whitelist remove FriendName
/whitelist list
```

Changes persist: initial allowlist/operator settings are skipped once the respective
files exist. Grant operator access only to trusted players. There is no GameStack
settings editor or arbitrary server-console command in this milestone.

## Data and recovery limits

The default data location is
`~/.local/share/gamestack/minecraft-paper/data/`, mounted as `/data`. It holds worlds,
player data, configuration, downloaded server artifacts, and logs. All dimensions
belong under this directory; exact layouts are checked during acceptance. The
container runs with your saved numeric UID:GID and does not recursively chown data.

```bash
gamestack rm minecraft-paper
```

This asks before stopping/removing the container and retains all instance files.
The retired name cannot be reused. Never delete the instance to repair a startup
failure. Do not hand-edit generated Compose or instance metadata. No import,
automatic update, or downgrade procedure is offered. Manual restore
is available as described below.

The image digest, Minecraft version, and Paper build are fixed. Restarting does not
select a newer build. Avoid manually replacing these values: world-format updates
need the later backup/update workflow. Read [acceptance.md](acceptance.md) before
claiming any persistence or recovery guarantee.

## Unattended preparation

Create a protected values file outside the checkout (for example, mode 0600):

```yaml
EULA: "TRUE" # Supply only after reading and accepting https://www.minecraft.net/en-us/eula
MOTD: "Our friends' server"
OPS: YourJavaName
WHITELIST: YourJavaName,FriendName
```

```bash
gamestack install packs/minecraft-paper/pack.yaml --values /path/to/private-values.yaml --bind-address 0.0.0.0 --prepare-only
```

This writes configuration without starting Docker or downloading Minecraft. Omit
`--prepare-only` to start. Fixed pack values such as TYPE, VERSION and ONLINE_MODE
must not appear in the values file. The quoted acceptance is required even during
preparation. Stored configuration is private but plaintext.

## Common failures

- Missing/declined EULA: review the terms; accept explicitly only if you agree.
- Bad profile names: use Java names and commas without spaces, not display names.
- Cannot join: check client version, allowlist, bind address, host address, forwarding,
  and CGNAT. Mojang profile lookup or authentication outages can prevent setup/join.
- Cannot start: run `gamestack doctor minecraft-paper`; check Docker access, available
  RAM/disk, port 25565 conflicts, internet access, and the reported world folder.
- Ownership mismatch: use the original host account. Preserve files and investigate
  the saved identity; do not recursively chown an unknown directory.
- Startup timeout: files and possibly a running container remain. Check status before
  retrying; do not delete the world. Slow downloads or world generation may be involved.

## Upstream and licenses

Selection reviewed 2026-09-05: Paper 26.2 build 121, Java 25 Temurin image. See
[upstream-lock.json](upstream-lock.json), [license inventory](license-inventory.json),
and [third-party review](../../THIRD_PARTY.md). Official sources were used because
Context7 was unavailable. No game JARs, images, or game assets are redistributed.

## Experimental manual backup

Run `gamestack backup minecraft-paper` (substitute your instance name). Players
are disconnected during stopped-state capture; an initially running server resumes
with a health check. The archive captures all dimensions, player data, allowlist,
operators, and other files under `data/`, plus saved GameStack configuration.

Use `gamestack backup list minecraft-paper`, then
`gamestack backup verify minecraft-paper BACKUP-ID` to recheck integrity offline.
Archives are uncompressed, unencrypted, private files under the instance's
`backups/` folder. Keep all copies private and allow room for a full data copy.
V0.1 keeps every completed backup and safety backup; no copies are pruned, so disk
usage grows. See the [backup guide](../../docs/cli.md#manual-backups) for
failures, retained partial artifacts, and restart guidance. Restore acceptance,
including in-game recovery, passed by user confirmation; integrity verification
alone does not make this pack supported.

## Experimental restore

Use `gamestack restore minecraft-paper` to choose a local backup, or
`gamestack restore minecraft-paper BACKUP-ID` to select one explicitly. Substitute
your instance name. Confirm the replacement and temporary player disconnection.
The entire data folder is restored, including worlds, player lists, plugins, and
server files. Current GameStack settings remain active; the saved GamePack must
match exactly. Version rollback and recovery onto another host are not included.

GameStack verifies and stages the backup, stops a running server, makes a verified
safety backup, and preserves the old data folder before replacement. Initially
running servers restart and must pass health verification; stopped or crashed
servers stay stopped. Missing data can be recovered with an explicit warning that
there is no current world to snapshot. Inaccessible data cannot be skipped.

The result prints the safety backup ID/location and retained recovery directory.
Safety archives appear in `gamestack backup list minecraft-paper` and can be
restored with the same command. Allow space for staged data plus a full safety
archive; no recovery copies are pruned. If restore is interrupted or health fails,
read the [restore recovery guide](../../docs/cli.md#interrupted-restore-recovery)
before starting again. See the [restore acceptance checklist](acceptance.md#restore-acceptance--passed-by-user-confirmation).
The user confirmed in-game world recovery and all backup/restore criteria.
Implementation and automated tests alone do not establish it.

## Explicit update and recovery

For a disposable instance on the reviewed build 121, use the included
[build 129 candidate](candidates/26.2-129/pack.yaml) and follow the
[CLI update guide](../../docs/cli.md#explicit-paper-updates):

```bash
gamestack update minecraft-paper --pack packs/minecraft-paper/candidates/26.2-129/pack.yaml
gamestack update minecraft-paper --recover
```

The first command requires a running, healthy server, creates a verified backup
while stopped, recreates on the pinned image, and checks the downloaded Paper
JAR checksum. The second returns the active world to its **pre-update** state
and retains later data separately. Both require confirmation (`--yes` for
scripts). Keep room for multiple full world copies. The current build 121 pack
and its settings are not silently changed; this workflow needs a selected
reviewed candidate. Live update acceptance is still pending.
