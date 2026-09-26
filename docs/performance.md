# Filesystem benchmark

Run `python scripts/benchmark.py` from an installed development environment.
The default is five runs per workload; `--runs 1` is a quick runnable smoke check.
It uses private temporary synthetic data and never contacts Docker or reads
installed worlds. Allow at least 1 GiB of temporary free disk space and several
minutes. Redirect JSON-lines output outside the checkout to retain every sample.

Workloads: one 256 MiB file; 10,000 files of 1 KiB each; and 100 nested directories
with one small file. The deep workload requires host long-path support. Each run
creates a fresh instance and deletes only its own temporary benchmark directory.
Creation includes mandatory archive verification. Staging includes verification,
space checks, checked writes, and file/directory syncs; it does not stop a server
or replace live data. Peak memory means traced Python allocations, not process RSS.
Tracing stays enabled during timings, so these are comparative measurements,
not production throughput estimates. OS caches are not flushed.

## Before/after results

Measured 2026-09-11 UTC on Linux x86-64 (CachyOS, kernel 7.2.3-1-cachyos,
glibc 2.44), Python 3.14.7. Baseline used the pre-change runtime; both revisions
used this same benchmark, on the same shared host. Results below show five-run
medians and observed min–max ranges in seconds. This is not an Ubuntu, Windows,
or macOS performance claim.

| Workload | Phase | Before median [range] | After median [range] |
|---|---|---:|---:|
| 256 MiB file | inventory | 0.00109 [0.00107–0.00111] | 0.00132 [0.00129–0.00142] |
| 256 MiB file | create | 1.04994 [1.03261–1.06149] | 1.04345 [1.03660–1.04937] |
| 256 MiB file | verify | 0.49581 [0.49200–0.50265] | 0.49642 [0.49053–0.50326] |
| 256 MiB file | stage | 1.10255 [1.09484–1.12865] | 1.09878 [1.08305–1.11312] |
| 10,000 small files | inventory | 0.22952 [0.21537–0.24848] | 0.22885 [0.22724–0.26190] |
| 10,000 small files | create | 6.37308 [6.33104–6.54424] | 6.48875 [6.36872–6.59408] |
| 10,000 small files | verify | 2.34632 [2.29929–2.45006] | 2.32270 [2.29420–2.35249] |
| 10,000 small files | stage | 12.83040 [12.62109–13.03509] | 10.53859 [10.43569–10.82857] |
| 100 levels | inventory | 0.00686 [0.00678–0.00767] | 0.00560 [0.00544–0.00564] |
| 100 levels | create | 0.06121 [0.05874–0.06168] | 0.05848 [0.05830–0.05864] |
| 100 levels | verify | 0.01955 [0.01914–0.02011] | 0.01913 [0.01909–0.01981] |
| 100 levels | stage | 10.73496 [10.52836–10.77326] | 0.42397 [0.41995–0.42527] |

| Workload | Peak Python MiB before → after | Archive bytes (unchanged) |
|---|---:|---:|
| 256 MiB file | 3.43 → 3.40 | 268,451,840 |
| 10,000 small files | 60.13 → 43.33 | 27,340,800 |
| 100 levels | 2.38 → 1.70 | 194,560 |

Staging fell 17.9% for small files and 96.1% for deep paths, outside the observed
ranges. Checking the complete ancestor chain once per staged member removes
repeated prefix traversal while still rejecting linked ancestors. Peak Python
allocations fell about 28% in these two workloads. Iterative inventory also
removes dependence on Python's recursion limit; its deep-tree median fell 18%.
Small-file inventory timings overlap, so no speed improvement is claimed there.

Full ancestor validation adds about 0.0002 seconds to the tiny large-file
inventory. Large-file end-to-end timings overlap the baseline ranges; archive
sizes are unchanged. Keep the safety checks despite that small fixed cost.
Repeated source inventories, full archive verification, staged checksums, and
state checks remain in place. No timing assertions run in CI.

## Verification record

The installed suite passed 127 tests on each of Python 3.11.16, 3.12.14,
3.13.15, and 3.14.7 on Linux. Wheel/source metadata and
installed-artifact smoke checks passed, as did Linux native bundle smoke checks.
The synthetic Docker lifecycle/backup/restore harness passed for both Python and
the native executable. An archive created by the baseline runtime verified and
staged successfully with the updated runtime. These checks do not establish
manual in-game acceptance or completion of the v0.1 release gates.

The full Paper harness passed on this Linux host after explicit user EULA
acceptance. It exercised verified backups, stopped and running restores,
safety-copy recovery, restart, container recreation, and removal with data
retained. Its initially failing shutdown assertion exposed a harness bug:
Docker console lines use `[time INFO]:`, while file logs use
`[time] [Server thread/INFO]:`. The parser now recognizes both observed layouts,
requires exact server messages, and rejects chat/plugin text in regression tests.
Synthetic Paper worlds and evidence were retained under the test's private
`/tmp/gamestack-paper-acceptance-*` directories; test containers were removed.

Native checks include the release Python 3.11 build. Windows/macOS CI runners,
the exact Ubuntu deployment target, and manual in-game restored-world checks
remain unverified by this change. No pack support or release gate was waived.
