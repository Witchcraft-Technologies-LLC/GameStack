# GameStack

**Self-host game servers without becoming a server admin.**

GameStack is a beginner-friendly toolkit for deploying and managing private game servers on spare PCs, home servers, and small Linux machines.

The goal is simple:

> **Turn a spare PC into a private game server for your friends in minutes.**

GameStack builds on top of existing Docker images and open-source game-server projects. It focuses on the parts that are usually frustrating for new self-hosters:

- installation
- configuration
- backups
- updates
- recovery
- diagnostics
- safe defaults

You should not need to understand Docker Compose, SteamCMD, bind mounts, or Linux service management just to host a game night.

---

## Status

GameStack is currently in **early development: the first v0.1 engine milestone is implemented**. It is not a finished release and no GamePack is supported yet.

The CLI can validate GamePack YAML, prompt for settings, prepare private instance storage, and list configured instances with state and health, and run start/stop/restart/status/doctor commands, plus `rm` to remove an instance while retaining its data. An experimental [Minecraft Paper pack](packs/minecraft-paper/README.md) now supplies the first real-game configuration. Its playable milestone passed user-confirmed manual acceptance on 2026-09-06. Manual backup creation, listing, integrity verification, and [safe restore](docs/cli.md#restore-backups) are implemented. Backup and restore acceptance passed by user confirmation; explicit Paper updates are implemented, with live update and final release acceptance pending. V0.1 keeps all completed backups without automatic pruning.

- [Try the development CLI](docs/cli.md)
- [Write a GamePack](docs/gamepacks.md)
- [v0.1 implementation plan and release gates](docs/v0.1-plan.md)
- [Third-party components](THIRD_PARTY.md)
- [CI, standalone builds, and GitHub releases](docs/releases.md)

The initial release will focus on proving the core experience with a small number of supported games rather than trying to support everything at once.

Planned early targets include:

- Foundry VTT
- Valheim
- Project Zomboid
- Satisfactory
- Minecraft Paper

Expect APIs, commands, and configuration formats to change during early development.

---

## Why GameStack?

There are already excellent Docker images and game-server management tools available.

GameStack is not trying to replace them.

Instead, it aims to make them easier to use.

A typical Docker-based setup might require you to understand:

```text
Docker Compose
environment variables
volume mounts
filesystem permissions
restart policies
backup scripts
update scripts
server logs
```

GameStack aims to turn that into:

```text
Install
Configure
Play
```

with maintenance handled underneath.

---

## Who Is This For?

GameStack is designed for people who:

- have a spare gaming PC, mini-PC, NAS, or home server
- want a persistent server for a small group of friends
- are comfortable following basic setup instructions
- do not want to become a Linux or Docker expert
- care about keeping their worlds and campaign data safe

GameStack is **not** currently designed for:

- commercial hosting providers
- enterprise infrastructure
- large multi-node clusters
- hundreds of game servers
- multi-tenant hosting

---

## What Is a GamePack?

GameStack uses **GamePacks** to provide game-specific deployment logic.

A GamePack describes everything GameStack needs to run and maintain a particular server.

This roadmap example illustrates the intent; it is not the implemented schema. See [schema 1](docs/gamepacks.md) for the accepted format. A future Valheim GamePack might define:

```yaml
name: valheim

resources:
  memory:
    recommended: 6GB

network:
  ports:
    - 2456-2458/udp

backup:
  paths:
    - /data/worlds
  interval: 6h

updates:
  backup_before_update: true
```

The GameStack runtime handles the generic infrastructure around it.

That means support for new games can eventually be added without rebuilding the platform from scratch.

---

## Planned CLI

The initial interface will be a simple command-line tool.

Conceptually:

```bash
gamestack install valheim

gamestack status valheim

gamestack start valheim
gamestack stop valheim
gamestack restart valheim

gamestack backup valheim
gamestack restore valheim

gamestack update valheim

gamestack logs valheim

gamestack doctor valheim
```

The CLI is intended to hide unnecessary infrastructure details while still leaving advanced users in control.

---

## Backups First

Game worlds and campaign data can represent hundreds or thousands of hours of work.

GameStack treats that data as the most important part of the system.

The intended update flow is:

```text
Validate
   ↓
Backup
   ↓
Graceful shutdown
   ↓
Update
   ↓
Start
   ↓
Health check
```

Restore operations should similarly preserve the current state wherever practical before replacing anything.

GameStack should never silently delete a world because a container was removed or reconfigured.

---

## Diagnostics

When something breaks, GameStack should help explain why.

Instead of expecting users to interpret Docker errors, GameStack will provide diagnostics through commands such as:

```bash
gamestack doctor valheim
```

Example:

```text
Docker                  ✓
Docker Compose          ✓
Disk space              ✓
Game server             ✓
Persistent storage      ✓
Backup service          ✓
Filesystem permissions  ✓
Configured ports        ✓
```

Errors should explain:

1. what failed
2. why it likely failed
3. what the user can do next

---

## Project Philosophy

### Use existing projects

GameStack will generally rely on proven upstream Docker images and game-server projects rather than maintaining duplicate server implementations.

### Opinionated defaults

Most users should not need to configure every possible server setting.

GameStack should provide sensible defaults and expose advanced settings when necessary.

### Safe before clever

Backups, permissions, updates, and filesystem operations should prioritize user-data safety over convenience.

### Small before broad

Supporting five games well is more useful than supporting fifty games poorly.

### Beginner-first UX

Infrastructure details should remain accessible, but they should not be required knowledge.

---

## Architecture

The planned architecture separates generic GameStack functionality from game-specific behavior.

```text
                    GameStack CLI
                         │
        ┌────────────────┼────────────────┐
        │                │                │
     Backups          Updates        Diagnostics
        │                │                │
        └────────────────┼────────────────┘
                         │
                  GameStack Runtime
                         │
                   GamePack Config
                         │
                     Docker
                         │
             Upstream Game Server
```

The runtime and the first two real GamePacks will be developed together in this
public repository. The current layout is:

```text
gamestack/
├── README.md
├── PROJECT.md
├── AGENTS.md
├── LICENSE
├── src/
│   └── gamestack/
├── packs/
│   ├── example/          # Non-runnable schema reference
│   └── minecraft-paper/ # First real GamePack; experimental
├── tests/
├── docs/
└── scripts/             # Build and release tooling
```

Game-specific configuration and acceptance material live in `packs/<id>/`;
generic behavior lives in `src/gamestack/`. The second game has not been selected.
See the [repository boundaries and separation plan](docs/repository-layout.md).

---

## Supported Platforms

The first release is expected to target:

**Ubuntu Server LTS on x86-64**

Additional Linux distributions and architectures may be supported later based on demand and testing.

GameStack will intentionally avoid claiming broad compatibility until those platforms are actually tested.

---

## Roadmap

### V0.1 — First Working GamePack

Focus:

- CLI foundation
- GamePack loading
- Docker deployment
- persistent storage
- start / stop / restart
- backups
- restore
- updates
- health checks
- diagnostics
- documentation

The first GamePack is **Minecraft Paper**, currently an experimental private Java
server milestone. See its [walkthrough](packs/minecraft-paper/README.md) and
[acceptance gates](packs/minecraft-paper/acceptance.md).

---

### V0.2 — More Games

Build the second real GamePack in this repository while improving the reusable
runtime. Use both implementations to stabilize the pack interface before splitting
repositories.

Likely candidates:

- Valheim
- Project Zomboid
- Satisfactory
- Foundry VTT

---

### V0.3 — Better Automation

Potential additions:

- Discord notifications
- improved hardware recommendations
- migrations
- safer update rollback
- better network diagnostics

---

### Later

Potential future directions include:

- local web dashboard
- community-authored GamePacks
- GamePack registry
- remote-access tools
- tunneling
- off-site backups
- multi-server management

These are intentionally not part of the initial scope.

---

## What GameStack Is Not Building Right Now

To keep the project focused, the following are currently out of scope:

- AI or LLM integrations
- Kubernetes
- enterprise hosting
- multi-node orchestration
- mobile apps
- cloud-hosted game servers
- hundreds of supported games
- multi-tenant hosting
- automatic router reconfiguration

The immediate goal is much smaller:

> Make one game server incredibly easy to install, maintain, back up, restore, and update.

Then repeat.

---

## Open Source and Commercial GamePacks

GameStack follows an **open-core + commercial GamePacks** model. The engine stays
open source under AGPL-3.0, including shared installation, lifecycle, backup,
restore, update, and diagnostic tooling as those features are implemented. The
planned public pack specification and community ecosystem make the engine
understandable, trustworthy, and extensible.

The paid products will be polished official GamePacks: curated game configuration,
verified versions, acceptance testing, and game-specific setup and recovery guides.
No paid GamePacks are currently available.

For now, development stays in this repository under its existing license. After
two real packs establish a stable interface, the planned split is:

| Repository | Visibility | Planned license / terms |
|---|---|---|
| `gamestack` | Public | AGPL-3.0 |
| `pack-spec` | Public | Apache-2.0 |
| `community-packs` | Public | AGPL-3.0 |
| `official-packs` | Private | Commercial |
| `gamestack-site` | Private | Proprietary |

These are future repository boundaries, not repositories or licensing changes
created by this cleanup. See the [separation plan](docs/repository-layout.md) for
the extraction gates and current packaging boundaries.

---

## Contributing

GameStack is still early enough that the architecture may change significantly.

If you're interested in:

- game-server hosting
- Docker
- self-hosting
- backup tooling
- Linux
- GamePack development

issues and discussions will eventually be the best place to contribute ideas.

Before contributing code, read the [v0.1 implementation plan and release gates](docs/v0.1-plan.md) and the [GamePack documentation](docs/gamepacks.md).

---

## GamePack Ideas

Potential future GamePacks include:

| Game | Status |
|---|---|
| Foundry VTT | Planned |
| Valheim | Planned |
| Project Zomboid | Planned |
| Satisfactory | Planned |
| Minecraft Paper | Playable and backup/restore acceptance passed by user confirmation; explicit update implemented, live update and final release gates pending |
| Palworld | Considering |
| Enshrouded | Considering |
| V Rising | Considering |
| 7 Days to Die | Considering |
| Terraria | Considering |
| Core Keeper | Considering |

Game selection will be based on actual hosting demand, technical feasibility, and maintenance burden rather than trying to maximize the number of supported titles.

---

## Current Goal

The first meaningful milestone for GameStack is not:

> Support 100 games.

It is:

> A person who does not know Docker can take a fresh Linux machine and successfully launch, back up, restore, and update a private game server without asking for help.

If GameStack can consistently accomplish that, everything else can be built on top of it.

---

## License

GameStack is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See [LICENSE](LICENSE) for the full license text.

Third-party game-server software and Docker images remain subject to their respective licenses and terms.

GameStack does not provide or grant licenses for proprietary games or applications.
