# Paper milestone acceptance record

Status: **PASSED — playable milestone (2026-09-06), backup/restore (2026-09-25), and safe updates (2026-09-26), accepted by the user**. Final v0.1 release acceptance remains separate.

## User-reported live evidence

On 2026-09-06, the user confirmed that manual testing was fully successful and
all tests were completed, and explicitly requested milestone sign-off. This
supersedes the earlier partial setup/join report and closes all 12 manual criteria
below, including their stated host, client, persistence, and failure conditions.

Evidence source: the user's confirmation in this session. Detailed host-version
outputs, timings, logs, and the exact tested revision were not supplied; no such
measurements are inferred here. Current checkout pins are Minecraft Java 26.2 /
Paper build 121 and the image reference in `upstream-lock.json`.
For that 2026-09-06 sign-off, no EULA was accepted and no Paper server was
started by the agent on the user's behalf.

## Checks completed in this workspace

- On 2026-09-25, 127 unit/filesystem tests passed against checkout `4cb76e3`
  using an isolated local install and Python 3.14.7. This includes synthetic
  failure, corruption, safety-copy, and interrupted-restore regression checks.
- On 2026-09-25, the opt-in synthetic Docker integration test passed on CachyOS
  x86-64, Docker 29.7.2, and Compose 5.5.1. The Paper integration test was
  skipped because its EULA and owner opt-ins were unset. Neither result is
  Ubuntu or in-game Paper acceptance.
- 61 unit/filesystem tests passed on 2026-09-06 using `.venv/bin/python`,
  including schema 1 and two new save-evidence regression tests. This is local
  verification, not Ubuntu acceptance.
- Both Docker integration tests were discovered and skipped by their opt-in gates.
  No agent-run Ubuntu harness result is claimed by this manual sign-off.

Historical checks recorded on 2026-09-05 (not rerun merely by updating this record):
- Schema 2 Paper pack validation passed.
- Wheel and source archive built; strict Twine checks passed.
- Installed CLI smoke checks passed for both built artifacts.
- Source archive checked for Paper pack/docs/inventory and absence of game JARs.
- Both real Docker integration tests were discovered and skipped by their opt-in gates.
- Registry-layer digests and package/JRE license inventory inspected without execution.
- `git diff --check` passed.

Windows/macOS execution, native bundle rebuilds, and commercial clearance remain
outside this playable milestone sign-off.

## Acceptance evidence

The [host runbook](acceptance-runbook.md) remains available for repeat testing and
regressions. Earlier rows record the evidence available at that time; the final
backup/restore and update sign-offs below supersede their pending status.

| Criterion / scenario | Confirmation date | Observed result | Evidence | Status |
|---|---|---|---|---|
| All 12 manual checklist items below | 2026-09-06 | User reports all manual tests completed successfully | User confirmation in this session | Passed |
| Backup and restore core workflows | 2026-09-25 | User reports manual backup and restore verification passed; failure and recovery cases were not included | User confirmation in this session; host, revision, archive IDs, and command output not supplied | Core workflows passed; scenario checks pending |
| Restore of a running instance | 2026-09-26 UTC | Backup picker listed four archives without rechecking integrity; selected `20260926T011409728404Z-7292fb7b4a2042a08ab99be5f33badd2`; CLI reported safety backup `20260926T011556524280Z-88420722d9f34096ab5c73602c31ea0f`, retained recovery files, and final state `healthy`; user reconnected and confirmed the world returned to the selected backup state | User-provided command transcript and in-game confirmation; host/revision and player-state checks not supplied | Running restore and world-state recovery passed; remaining scenarios pending |
| Corrupted backup rejection | 2026-09-26 UTC | User modified a backup and confirmed its integrity check failed | User confirmation in this session; archive ID, exact command/output, and whether a copy was used not supplied | Passed |
| All backup and restore checklist scenarios below | 2026-09-25 local / 2026-09-26 UTC | User explicitly confirms every backup and restore criterion was tested and accepted | User sign-off in this session; per-scenario logs, host details, and tested revision were not supplied | Passed by user attestation |
| Successful safe update and explicit recovery workflows | 2026-09-26 | User reports manually running the safe update and confirms all stages worked as intended; separately confirms the run included `gamestack update INSTANCE --recover` | User confirmation in this session; host, revision, selected pins, backup ID, command output, reconnect result, and deliberate failure/interruption observations not supplied | Core manual update and recovery passed; later user sign-off covers remaining criteria |
| Player checks after update and recovery | 2026-09-26 | User confirms reconnecting after both stages and checking expected build, world, and player state each time | User clarification in this session; detailed client observations and logs not supplied | Passed by user attestation |
| Deliberate wrong-checksum update and recovery | 2026-09-26 | Disposable Paper 121 → 129 reached healthy recreation, rejected the altered JAR checksum, stopped, blocked backup, and retained a verified pre-update archive; explicit recovery returned healthy Paper 121 with the synthetic marker and retained build 129 data/configuration | Direct local run on CachyOS x86-64, Docker 29.8.1, Compose 5.5.1, revision `e10287c` with documentation edits; private evidence at `/tmp/gamestack-paper-failure-dz593_0s/failure-evidence.json` | Passed locally; later user sign-off covers remaining criteria |
| Remaining safe update criteria | 2026-09-26 | User explicitly directs that the Ubuntu harness and interrupted-phase recovery checks be considered passed | User sign-off in this session; a direct Ubuntu harness run, host/revision details, and live interruption logs were not supplied. Unit regression tests cover interrupted file switches. | Passed by user attestation; direct-run evidence unavailable |

## Automated host checks

On a disposable Ubuntu Server 24.04 LTS x86-64 host with 8 GiB RAM and Docker/Compose,
use a non-root account. Record OS, Python, Docker, Compose, Java, image digest,
Paper build, available RAM/disk, startup time, and shutdown time.

After personally accepting the Minecraft EULA, opt in explicitly:

```bash
GAMESTACK_PAPER_TEST=1 GAMESTACK_MINECRAFT_EULA=TRUE GAMESTACK_PAPER_OWNER=YourJavaName python -m unittest discover -s tests/integration -p test_paper_docker.py -v
```

The harness uses a new temporary directory and unique instance. It publishes only
localhost TCP 25565 (which must be free) and retains test data for inspection.
It checks startup, non-root identity, configuration, graceful lifecycle, recreation,
server artifact hashes, world metadata, removal retention, and retired-name rejection.
A private `evidence.json` retains host/container versions, timings, published ports,
fixed shutdown markers, and completion/cleanup results, including failed runs.
It does not retain arbitrary logs or container environments. It is not run by
default CI and does not prove placed blocks or player inventory.

## Completed manual acceptance — user-confirmed

- [x] Install from the beginner walkthrough on clean Ubuntu 24.04 x86-64.
- [x] Join from a second machine with an allowlisted Java 26.2 account.
- [x] Reject a second authenticated account not on the allowlist.
- [x] Owner adds/removes a friend in-game; repeat after restart and recreation.
- [x] Place distinctive blocks in Overworld, Nether, and End; obtain a known inventory.
- [x] Stop cleanly, restart, recreate only the stopped container, and verify all
      blocks, dimensions, inventory, and player positions survive.
- [x] Check save-complete shutdown messages and exit code; no forced-kill shutdown.
- [x] Simulate a crash only on the disposable world; verify restart policy and health.
- [x] Confirm only TCP 25565 is published and RCON/query/JMX/SSH are disabled.
- [x] Verify an external friend can connect after manual forwarding where available;
      document network/CGNAT limits rather than claiming universal reachability.
- [x] Exercise occupied port, blocked downloads, insufficient disk, unwritable data,
      and health failure; no success output or loss of recoverable data.
- [x] Confirm `rm` retains files and does not permit accidental instance reuse.

## Later v0.1 gates

Safe updates and failed-update recovery were accepted by user sign-off on
2026-09-26. Full license/packaging clearance and final clean-host release
acceptance remain pending. Do not mark the pack supported or sellable on the basis
of schema validation or the opt-in smoke test.

V0.1 retention policy: keep every completed backup, including safety backups. There
is no automatic pruning; storage use grows until the owner manages copies outside
GameStack. Verify a copy before relying on it, and keep recovery material needed
for future recovery.

## Manual backup acceptance — passed by user confirmation

On 2026-09-25 (2026-09-26 UTC), the user explicitly confirmed all backup criteria
below were tested and accepted. The earlier command confirmation and the separate
corruption-check report are recorded above. Per-scenario logs, host details, and
the tested revision were not supplied; the opt-in Paper harness was not run here.

- [x] Place recognizable blocks and obtain inventory in a disposable world.
- [x] Run manual backup while online; confirm disconnect, verified artifact, and healthy resume.
- [x] Stop and back up again; confirm the server remains stopped.
- [x] List and verify both archives; check all dimensions, player data, permissions,
      and saved configuration are represented without disclosing credentials.
- [x] Alter backup bytes and confirm the integrity check fails (user-confirmed, 2026-09-26).
      Perform corruption tests on a disposable copy so an unmodified recovery copy remains.
- [x] Exercise failed capture/restart on disposable data; retain older backups and
      source files and record actionable failure output.

Failed-update recovery was accepted separately on 2026-09-26; see the safe
update acceptance record below.

## Restore acceptance — passed by user confirmation

On 2026-09-25 (2026-09-26 UTC), the user explicitly confirmed all restore criteria
below were tested and accepted. The running restore also has command and in-game
world-state evidence above. Per-scenario logs, host details, and the tested revision
were not supplied; the opt-in Paper harness was not run here. See the
[runbook](acceptance-runbook.md#manual-restore-validation) for repeat testing.

Use only the disposable instance in the acceptance runbook. Record host details,
runtime revision, pack/image pins, selected and safety backup IDs, command outcomes,
and final server states. Keep archive contents and configuration private.

- [x] Build a recognizable marker in-world and record its location; back up.
- [x] Change that marker and player state, then restore the earlier backup while
      running. Confirm healthy restart, reconnect, and verify the earlier state.
- [x] Restore the generated safety backup and confirm the later state returns.
- [x] Repeat while stopped: exact restore succeeds and stays stopped until start.
- [x] Confirm files created after the backup disappear from active data and remain
      in the safety backup/retained previous-data folder.
- [x] Confirm current GameStack configuration remains unchanged.
- [x] Exercise a disposable missing-data instance and a confirmed stopped crashed
      instance; check warnings, snapshot metadata, ownership, and stopped result.
- [x] Confirm corrupt/incompatible archives and insufficient space block replacement.
- [x] Exercise failed health verification and interrupted replacement using synthetic
      data; verify retained copies and the documented marker recovery procedure.

The opt-in Paper Docker harness checks restored file contents, safety-backup
roundtrip, and actual health after restore. It cannot substitute for player-observed
world recovery. Safe updates were accepted separately below; final release
acceptance remains open.

## Safe update acceptance — passed by user sign-off (2026-09-26)

On 2026-09-25, 141 unit/filesystem tests passed on the candidate checkout, and
the opt-in synthetic Docker integration passed on the local development host.
Both Paper Docker tests were skipped because their EULA/owner opt-ins were unset.
The source archive includes the update code, build 129 candidate, and harness.
Those automated results do not establish Ubuntu or in-game update acceptance. On
2026-09-26, the user separately confirmed a manual safe update completed with
all stages working as intended, including explicit `--recover`, and confirmed
reconnecting after both stages with the expected build, world, and player state.
The host/revision and detailed client observations for that run were not supplied.

Implementation includes `gamestack update INSTANCE --pack PATH` and `--recover`,
a pinned build 129 candidate, transaction journal, retained copies, and unit
regressions. The user explicitly agreed to the Minecraft EULA on 2026-09-26.
The canonical-port harness was not run locally because the working server binds
port 25565. The user subsequently accepted the Ubuntu harness criterion; no
direct Ubuntu harness result or host/revision record was supplied. A separate disposable instance
used port 25566 in both its baseline and candidate pack. The candidate lock
changed only the JAR hash to 64 zeroes, so its failure occurred after recreation.
The working instance remained running and healthy. Unit regressions exercised
interrupted file switches; the user accepted the remaining interruption
criterion, but no live interruption log was supplied.

Local failure evidence: update failed at the Paper JAR checksum, the server
stopped, `.update.json` remained at `start-new`, and backup creation was blocked.
Pre-update archive `20260926T131628439430Z-2af8a8a3d3694261916fc63f25bb6cbb`
and post-update archive `20260926T131733050510Z-b7ffe4346e7c401593b110ef5307f2d4`
both passed verification. Recovery restored build 121 and the synthetic marker;
the build 129 JAR matched official SHA-256
`b1d8f6bfa1b6101fa8e947b53041cb3bdf5540e7b83b6547ca19ba7edefeb083`
in the retained later world. Both saved configurations and world copies remain.
The disposable server was stopped after verification; the local test did not
replace the user's separate in-game checks.

- [x] Manually run the safe update and explicit `--recover`; user confirms all
      update stages worked as intended and clarifies that recovery was included
      (2026-09-26; host, revision, backup ID, and detailed output not supplied).
- [x] Ubuntu 24.04 build 121 → 129 opt-in harness criterion accepted by explicit
      user sign-off (2026-09-26). The harness was not directly run or evidenced
      here; exact Ubuntu host/revision and harness output were not supplied.
- [x] Reconnect after update and confirm the recognizable world and player state,
      and that the running server reports Paper 26.2 build 129.
- [x] Exercise a failed update on disposable data, confirm it blocks further
      mutations and retains both worlds/configurations, then run `--recover`.
- [x] Reconnect after recovery, confirm build 121 and the **pre-update** world
      state; inspect the separately retained later world copy.
- [x] Recovery after successful update with later gameplay and interruption
      at each config/data switch phase accepted by explicit user sign-off
      (2026-09-26). Unit interruption regressions passed; live interruption
      command/log evidence was not supplied.
