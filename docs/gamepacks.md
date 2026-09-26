# Experimental GamePack schemas

Pack definitions and their game-specific guides, upstream records, and acceptance
material belong in `packs/<id>/`. The runtime validator and this provisional
specification stay in this repository through the first two real packs. See the
[repository separation plan](repository-layout.md) for the future public interface
and commercial pack boundaries.

## Schema 1

Use [packs/example/pack.yaml](../packs/example/pack.yaml) as the format reference. It intentionally names a nonexistent image and healthcheck: validation proves structure, not deployability. It is not a supported game or a distributable server.

```bash
gamestack pack validate packs/example/pack.yaml
```

Expected result: `GamePack is valid (schema 1)` with a reminder that image availability and game behavior are untested. Invalid YAML, duplicate/unknown fields, unsupported versions, or unsafe paths return exit status 1 with guidance. Error output omits source lines that might contain credentials.

## Fields

| Field | Meaning |
|---|---|
| `schema_version` | Integer `1`; other versions fail explicitly |
| `id` | Lowercase letter followed by lowercase letters, digits, or hyphens; at most 40 characters |
| `version` | Quoted nonempty GamePack version; retained with the installation |
| `name` | Human-readable display name |
| `image` | Full image reference ending in `@sha256:` and a 64-character lowercase digest; no floating tags |
| `data_path` | Normalized absolute Linux container path below `/`; all persistent data must live here |
| `stop_timeout` | Integer seconds, 10–600; choose based on verified upstream graceful shutdown behavior |
| `user` | Optional quoted non-root numeric `UID:GID`; only set if upstream supports it and storage is provisioned accordingly |
| `ports` | List of `host` and `container` integer ports, `protocol` (`tcp`/`udp`), and human-readable `purpose`; localhost-only in this milestone |
| `environment` | Mapping of uppercase environment names to `prompt`, boolean `secret`, and optional quoted `default`; defaults cannot be secrets |
| `healthcheck` | Nonempty list of executable and arguments present in the image; executed without a shell |

All settings are required; defaults satisfy non-secret settings when running noninteractively. Quote string values, including numeric settings and versions. There are no templates, environment substitution, arbitrary Compose fragments, scripts, multiple services, or host storage paths in packs. Every field shown in the example is required, even when ports/environment are empty collections.

The single `data/` folder must contain all saves needed for recovery. Pack authors must independently test that an image actually uses that location. Manual backup captures this entire directory and the saved configuration in a verified, private, uncompressed archive. Restore replaces the full data folder with a verified backup from the identical GamePack while retaining current GameStack configuration and a safety backup. Minecraft Paper restore acceptance passed by user confirmation; other packs require independent acceptance. No new pack fields are required; backup uses the declared graceful shutdown timeout and existing startup health gate. Pack authors must verify that clean shutdown saves all world data.

## Configuration and credentials

Installation prompts for declared settings. Secret prompts hide input. For noninteractive preparation, use `--values /path/outside/repository/values.yaml` containing a plain mapping:

```yaml
SERVER_NAME: Friends server
SERVER_PASSWORD: "replace-with-your-own-password"
```

Protect this input file yourself (on Linux, `chmod 600 /path/to/values.yaml`). Never commit it. The engine stores values inside the generated `compose.yaml`, with mode 0600 under a private 0700 instance directory on POSIX. Credentials are plaintext on disk and visible to accounts with Docker access. Dollar signs are escaped for Compose so literal input survives interpolation. Debug mode does not print configuration or subprocess output.

An installation snapshots `pack.yaml`, creates `data/` and `compose.yaml`, then writes `instance.yaml` as its completion marker. Paths remain stable across lifecycle operations. Do not move directories or hand-edit generated files; the engine rejects mismatches. Editing/upgrading existing configurations requires future migration tooling.

## Before calling a real GamePack supported

Document the upstream project, image digest, upstream license/notices, game software licensing, supported game version, hardware recommendations, exposed port purposes/protocols, storage and ownership, healthcheck, shutdown behavior, backup/update behavior, and known limitations. Run all GamePack acceptance criteria in AGENTS.md on the supported host. Schema validation is only the first check.

## Schema 2: first real GamePack

Schema 1 remains accepted and renders exactly the original localhost-only Compose
configuration. Schema 2 requires `startup_timeout` (integer 30–1800 seconds), used
for Compose health startup grace and the bounded start wait. `stop_timeout` is
unchanged. The optional `user: installing-user` resolves the installing account's
nonzero UID:GID on POSIX and persists it; hosting remains Linux x86-64. Preparation
of that mode on Windows or as root fails before creating storage. Numeric users
remain accepted; authors must provision matching storage themselves.

Each environment entry is either:

- `{value: "literal"}`: fixed, never prompted and forbidden in `--values`, even
  when the supplied value matches; or
- the schema 1 prompt/secret/default entry, optionally with `constraints` or
  `agreement`.

`constraints` requires a string of allowed `characters`, integer `min_length` and
`max_length` (1–1024), and optionally one `separator` character outside the allowed
set. Every separated item must satisfy the constraints; empty entries and spaces
are not silently removed. Constrained input is limited to 4096 characters. This
small character/length validator deliberately does not execute pack-supplied regex.

`agreement` is an HTTPS terms URL, with no default and `secret: false`. Interactive
acceptance defaults to no. A values file must supply the exact string `"TRUE"`;
YAML boolean `true` is not accepted. Validation occurs before instance creation or
server downloads. Agreement acceptance is retained in private instance configuration.

Schema 2 instance metadata stores `deployment.bind_address` and, for
`installing-user`, resolved `deployment.user`. The latter must match the world
folder owner when inspected; it is never recalculated from the current caller.
Generated configuration is checked against this metadata and the snapshotted pack.
No existing instance is migrated or rewritten.

Install `--bind-address` accepts a numeric IPv4 or IPv6 address; scoped and multicast
addresses are rejected. The default is `127.0.0.1`. Interactive schema 2 setup offers
all-IPv4-interface exposure, default no. Explicit `0.0.0.0` or `::` includes public
interfaces when present; these are not LAN-only controls. Schema 1 rejects an
explicit non-localhost binding. Router and firewall configuration remains manual.

The experimental [Paper pack](../packs/minecraft-paper/README.md) demonstrates this
schema. Validation does not certify licensing completeness or live-game acceptance.
