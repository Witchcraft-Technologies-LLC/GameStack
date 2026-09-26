# CI and GitHub releases

The `CI and release` workflow tests pull requests and pushes to `main` or `fix/ci-*` branches. CI fix branches can therefore run the full test and build matrix before a PR is opened. A manual run on `main` additionally tags and publishes the tested commit. Ordinary pushes never publish. This is release infrastructure for the experimental engine; the first version remains `0.1.0rc1`. Backup/restore acceptance passed by user confirmation; explicit Paper update acceptance passed by user sign-off; final GamePack release acceptance remains pending.

## Release a version

1. Update both `pyproject.toml` and `src/gamestack/__init__.py` in a reviewed change. Use a canonical three-part Python version, such as `0.1.0.dev1`, `0.1.0rc1`, or `0.1.0`. Package metadata and CLI output must agree. No workflow edits or commits version numbers for you.
2. Merge the change to `main` and check **Release checks**. Review workflow changes particularly carefully, because manual runs can publish repository releases.
3. Open **Actions → CI and release → Run workflow**, select **main**, and run it. No version input, personal token, PyPI token, or signing certificate is required.
4. The workflow captures that commit, repeats the complete checks, then creates annotated tag `v<version>` and a draft release. It uploads all tested files and `SHA256SUMS` before publishing the draft. New commits to main do not change the selected release commit.

Development and prerelease versions become GitHub prereleases and are not marked latest stable. A stable version is marked latest. Publication uses the repository's temporary `GITHUB_TOKEN` with `contents: write` only in the publishing job. The organization/repository must permit Actions and release/tag writes; tag rules must allow this workflow's operation. The workflow does not change repository settings.

Manual runs are serialized and do not cancel an active release. GitHub concurrency may replace an older pending run with a newer pending run; it is not a FIFO queue. PR and push runs cancel superseded runs for that PR or branch. A manual run on any branch other than main fails before building.

## Required checks and artifacts

The aggregate **Release checks** job fails if any dependency fails, is cancelled, or is skipped. Configure it as a required main-branch check and require pull requests in repository rules. This setup is recommended, not automatically applied by the workflow. There are no path filters that could leave this required check permanently pending.

Checks include:

- Python 3.11–3.14 on Ubuntu 22.04 x86-64, Windows 2022 x86-64, and macOS 14 ARM64.
- Wheel and source builds, strict metadata checks, and fresh-environment installs of each.
- Native PyInstaller builds with Python 3.11 on all three OS targets, and CLI smoke tests of the bundles both before and after archiving/extraction. Artifact smoke tests use an empty subprocess search path to exclude host Docker tooling; they verify preparation, listing with unavailable status, offline empty-backup listing, invalid backup-ID rejection, and refusal to remove without confirmation. Real Docker behavior is covered separately by Linux integration tests.
- Real Docker lifecycle, health, persistence through container recreation, manual backup/verification with automatic resume and stopped-state preservation, and removal with retained synthetic data on Ubuntu. This runs against both the installed Python CLI and Linux executable.
- Publisher regression tests: version mismatch, checksum failures, conflicting tags, existing releases/drafts, failed release gates, and incomplete uploads.

Artifacts have versioned names:

```text
gamestack-0.1.0rc1-py3-none-any.whl
gamestack-0.1.0rc1.tar.gz
gamestack-0.1.0rc1-linux-x86_64.tar.gz
gamestack-0.1.0rc1-windows-x86_64.zip
gamestack-0.1.0rc1-macos-arm64.tar.gz
SHA256SUMS
```

Wheel/source and native artifacts are built once per run. The aggregate job assembles their checksums; publication downloads that exact verified artifact set, checks every digest and filename, and uploads those files without rebuilding. It does not check out or execute repository code. CI artifacts are retained for 14 days; safe Docker failure diagnostics for seven days. Published release assets remain on the release.

Action dependencies are pinned to verified upstream commit SHAs. Dependabot checks action updates weekly and build-tool requirements monthly. Review those updates and their CI results before merging. Build tools are pinned directly; transitive dependencies and GitHub runner images can still change, so this does not claim byte-for-byte reproducible builds.

## Install a release

Download the desired archive and `SHA256SUMS` from the same GitHub Release. Compare the downloaded file's hash with its entry before installing:

```bash
# Linux
sha256sum gamestack-0.1.0rc1-linux-x86_64.tar.gz
# macOS
shasum -a 256 gamestack-0.1.0rc1-macos-arm64.tar.gz
```

On Windows, use `Get-FileHash .\gamestack-0.1.0rc1-windows-x86_64.zip -Algorithm SHA256` in PowerShell. Checksums detect corruption; they are not an independent publisher signature.

Extract a native archive and keep its `gamestack` directory intact. Run `./gamestack/gamestack --help` on Linux/macOS or `.\gamestack\gamestack.exe --help` on Windows. You may put that directory on PATH. The `_internal` directory is required; moving only the executable breaks it. Python is bundled; Docker and Compose remain separate prerequisites.

Alternatively, in a Python 3.11+ virtual environment, install the downloaded wheel with `python -m pip install ./gamestack-0.1.0rc1-py3-none-any.whl`. Dependency downloads may be needed. The source archive includes the engine, tests, build scripts, example, documentation, and license notices.

Native bundles have no Windows certificate signature or Apple Developer ID/notarization. macOS tooling may apply an ad-hoc signature needed to execute on ARM64; that is not publisher verification. OS download protection may prompt or block execution. Python packages remain an installation alternative.

Linux executable compatibility is tested on Ubuntu 22.04 x86-64, not all Linux systems. Windows/macOS builds support local pack tooling; they do not establish Docker Desktop, native hosting, Intel Mac, or ARM Linux support. Synthetic nginx acceptance does not make nginx a supported GamePack and does not establish save consistency for real games.

## Failures and retries

Artifact smoke workspaces retry Windows permission failures after 0.5, 1, 2, and 4 seconds (7.5 seconds total). Persistent failures still fail the job. Cleanup identifies Windows without starting a platform probe. GameStack resolves Docker through PATH before execution, preventing Windows system-directory lookup from bypassing the smoke test’s empty PATH and launching host Docker processes. Normal use requires Docker on PATH.

- Before checks pass, no tag or release is created. Fix the failure and start a fresh manual run.
- If tag creation succeeds but no release exists, a fresh run may reuse only an annotated tag pointing to the same tested commit. Lightweight or conflicting tags fail; existing tags are never moved.
- Any existing release, including a draft, blocks publication. For an interrupted upload, inspect the draft and tag. Explicitly delete the incomplete draft through GitHub, retain the matching tag, then start a fresh run of the same main commit. Never delete or overwrite a published release merely to retry.
- If main has advanced past a tagged commit after a failed release, do not repoint the tag. Use a new version in a new main commit unless the existing draft can be deliberately recovered outside this automation.
- API/upload failures leave the draft unpublished. A timeout during final publication can be ambiguous: inspect GitHub before retrying; the next run refuses an existing release.
- A failing Docker test records only the unique test project, pinned image, state summary, and cleanup outcome. It does not upload container logs or environment values. Cleanup targets only that test project, never global Docker resources.

## Local verification and rebuilding

Use a Python 3.11+ virtual environment and install the package before running
checks. `PYTHONPATH=src` alone is insufficient: artifact smoke tests intentionally
clear it and run outside the checkout. For development tests only:

```bash
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -e . packaging
python -m unittest discover -s tests -v
```

For release artifacts, install the pinned build tools and run:

```bash
python -m pip install -r scripts/requirements-ci.txt
python -m pip install --no-build-isolation -e .
python -m unittest discover -s tests -v
python scripts/ci.py version
python -m build --no-isolation
python -m twine check --strict dist/*
python scripts/ci.py package-smoke
python scripts/ci.py native --target linux-x86_64
```

Build from a clean checkout. Choose `windows-x86_64` or `macos-arm64` on the matching native host. Python 3.11 is used for official bundles. Installed interpreter/license layouts must include the Python license; missing notices fail bundling.

On a disposable Linux Docker host, explicitly enable the integration test:

```bash
GAMESTACK_DOCKER_TEST=1 python -m unittest discover -s tests/integration -v
```

Set `GAMESTACK_EXECUTABLE` to an absolute bundled executable path to exercise it instead. The test pulls a pinned public nginx image, creates a uniquely named container and synthetic temporary data, and removes only its test resources. It never touches existing game instances. Docker socket access is required; a test that cannot reach Docker fails rather than silently passing.

Use `actionlint .github/workflows/tests.yml` to validate workflow syntax. Local tests do not substitute for the first successful full GitHub run; Windows/macOS runners and publishing are only verified when exercised there.

For opt-in filesystem performance measurements, see [the benchmark procedure and results](performance.md).
