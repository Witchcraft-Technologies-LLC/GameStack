# Experimental Satisfactory GamePack

This pack uses [wolveix/satisfactory-server](https://github.com/wolveix/satisfactory-server).
The image digest is retained from the draft pack; image availability, its exact
source revision, licensing/notices, and live-game acceptance remain unverified.
It is not yet a supported GamePack. The wrapper's [license](https://github.com/wolveix/satisfactory-server/blob/main/LICENSE)
and Satisfactory's game terms must be reviewed before distribution.

## Requirements and preparation

Target host: Linux x86-64 with Docker Compose and 8–16 GB RAM recommended by
upstream. **Install using a non-root account with UID 1000 and GID 1000** (check
with `id -u` and `id -g`). The stock upstream image only supports running its
entire container as that non-root identity. `user: installing-user` preserves
GameStack's storage ownership; other identities require a separately built and
verified image, not merely different PUID/PGID values.

```bash
gamestack pack validate packs/satisfactory/pack.yaml
gamestack install packs/satisfactory/pack.yaml --name factory --prepare-only
gamestack start factory
```

Validation should report schema 2. Preparation creates private configuration and
a `data/` directory inside the instance; start downloads the game and waits up to
600 seconds for health. Initial downloads can exceed that wait: check
`gamestack status factory` and `gamestack logs factory` before retrying start.
Permission errors commonly indicate an account identity mismatch.

Bindings default to localhost. To allow friends to connect, explicitly select a
reachable bind address during installation. The pack publishes TCP and UDP 7777
for player connections and TCP 8888 for reliable messaging. Firewall and router
configuration are manual.

## Persistence, health, and recovery

GameStack mounts the instance's `data/` directory at `/config`, which upstream uses
for game files, saves, blueprints, configuration, logs, and its own backups.
GameStack backups capture that whole directory, including the large downloaded
game files. Ensure sufficient disk space for backup and restore safety copies.

The pack uses upstream's `/home/steam/healthcheck.sh`, which checks the HTTPS game
API on port 7777. GameStack supplies the Compose healthcheck timing and `CMD`
prefix. Shutdown has a 120-second grace period; save consistency on shutdown and
GameStack backup/restore/update workflows still require live acceptance testing.

The game version is not pinned: upstream downloads the selected Steam branch and
updates it on each start by default. The image digest alone does not freeze the
game version, so lifecycle operations can update the game outside GameStack's
backup-before-update workflow. Safe game updates remain a release gate.

Before claiming support, verify clean install, lifecycle and health, persistence
after container recreation, backup/retention, restore, update/recovery, and the
Quick Start on the target host. No live server or world migration is performed by
editing this pack; existing installations retain their snapshotted configuration.
