"""Portable build and artifact checks. Run from the repository root."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.metadata
import os
import platform
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import zipfile

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def version():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    value = metadata["version"]
    parsed = Version(value)
    if (str(parsed) != value or parsed.local is not None or parsed.epoch or
            not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?", value)):
        raise ValueError("Use a canonical public package version without a local suffix or epoch")
    actual = run([sys.executable, "-m", "gamestack", "--version"])
    if actual != value or importlib.metadata.version("gamestack") != value:
        raise ValueError("Package metadata and CLI versions disagree")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"version={value}\nprerelease={str(parsed.is_prerelease or parsed.is_devrelease).lower()}\n")
    return value


@contextmanager
def smoke_workspace():
    """Remove synthetic files, allowing briefly held Windows handles to close."""
    temporary = tempfile.TemporaryDirectory()
    try:
        yield temporary.name
    finally:
        delays = (0.5, 1, 2, 4)
        for attempt in range(len(delays) + 1):
            try:
                temporary.cleanup()
                break
            except PermissionError:
                # platform.system() can itself spawn a subprocess on Python
                # 3.11 Windows; cleanup must not probe or launch host tooling.
                if sys.platform != "win32" or attempt == len(delays):
                    raise
                time.sleep(delays[attempt])


def smoke(command, expected):
    """Exercise the installed artifact from outside the checkout, with synthetic input."""
    with smoke_workspace() as temporary:
        workspace = Path(temporary).resolve()
        storage = workspace / "world storage ü"
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        # Artifact checks must not launch host Docker/Compose processes, which can
        # outlive the CLI and keep its temporary working directory locked on Windows.
        empty_path = workspace / "empty-path"
        empty_path.mkdir()
        env["PATH"] = str(empty_path)
        def invoke(arguments, success=True):
            result = subprocess.run([*command, *arguments], cwd=workspace, env=env,
                                    text=True, capture_output=True, timeout=60)
            if (result.returncode == 0) != success:
                raise AssertionError(f"CLI smoke failed: {arguments[0]} (exit {result.returncode})")
            return result.stdout
        assert invoke(["--version"]).strip() == expected
        assert "install" in invoke(["--help"])
        assert "backup_id" in invoke(["restore", "--help"])
        pack = workspace / "pack.yaml"
        shutil.copyfile(ROOT / "packs/example/pack.yaml", pack)
        invoke(["pack", "validate", str(pack)])
        values = workspace / "values.yaml"
        values.write_text('SERVER_NAME: CI\nSERVER_PASSWORD: "synthetic-ci-password"\n', encoding="utf-8")
        common = ["--root", str(storage)]
        invoke([*common, "install", str(pack), "--prepare-only", "--name", "smoke", "--values", str(values)])
        assert "No completed backups found" in invoke([*common, "backup", "list", "smoke"])
        invoke([*common, "backup", "verify", "smoke", "invalid-id"], success=False)
        assert "smoke: unknown (status unavailable)" in invoke([*common, "list"])
        invoke([*common, "restore", "smoke", "--yes"], success=False)
        invoke([*common, "restore", "smoke", "invalid-id", "--yes"], success=False)
        invoke([*common, "rm", "smoke"], success=False)
        assert (storage / "smoke/instance.yaml").exists()
        assert not (storage / "smoke/removed.yaml").exists()


def package_smoke():
    value = version()
    artifacts = sorted((ROOT / "dist").glob("*"))
    assert len(artifacts) == 2, "Expected exactly a wheel and source archive"
    for artifact in artifacts:
        with tempfile.TemporaryDirectory() as temporary:
            environment = Path(temporary).resolve() / "venv"
            run([sys.executable, "-m", "venv", environment])
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run([python, "-m", "pip", "install", artifact])
            smoke([str(python), "-m", "gamestack"], value)


def native(target):
    value = version()
    expected = {"linux-x86_64": ("Linux", "x86_64"), "windows-x86_64": ("Windows", "amd64"),
                "macos-arm64": ("Darwin", "arm64")}
    if (platform.system(), platform.machine().lower()) != expected.get(target):
        raise ValueError("Native target does not match the build host")
    work = ROOT / "build/native"
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "gamestack",
         "--distpath", work / "dist", "--workpath", work / "work", "--specpath", work,
         ROOT / "scripts/launcher.py"])
    bundle = work / "dist/gamestack"
    shutil.copytree(ROOT / "docs", bundle / "docs")
    shutil.copytree(ROOT / "packs/example", bundle / "example")
    shutil.copytree(ROOT / "packs/minecraft-paper", bundle / "packs/minecraft-paper")
    for filename in ("LICENSE", "THIRD_PARTY.md", "README.md"):
        shutil.copyfile(ROOT / filename, bundle / filename)
    notices = bundle / "licenses"
    shutil.copytree(ROOT / "scripts/licenses", notices)
    for dependency in ("PyYAML", "pyinstaller", "pyinstaller-hooks-contrib", "packaging", "altgraph"):
        distribution = importlib.metadata.distribution(dependency)
        for file in distribution.files or []:
            if "license" in str(file).lower() or "copying" in str(file).lower():
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    destination = notices / dependency / str(file)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.exists():
        import sysconfig
        python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.exists():
        raise RuntimeError("Python distribution license missing; include it before publishing")
    shutil.copyfile(python_license, notices / "Python-LICENSE.txt")
    (notices / "build-environment.txt").write_text(run([sys.executable, "-m", "pip", "freeze"]) + "\n", encoding="utf-8")
    executable = bundle / ("gamestack.exe" if os.name == "nt" else "gamestack")
    smoke([str(executable)], value)
    destination = ROOT / "native-dist"
    destination.mkdir(exist_ok=True)
    archive = shutil.make_archive(str(destination / f"gamestack-{value}-{target}"),
                                 "zip" if os.name == "nt" else "gztar", bundle.parent, bundle.name)
    with tempfile.TemporaryDirectory() as temporary:
        extracted = Path(temporary).resolve()
        if archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as stream:
                stream.extractall(extracted)
        else:
            with tarfile.open(archive) as stream:
                stream.extractall(extracted, filter="data")
        smoke([str(extracted / "gamestack" / executable.name)], value)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"executable={executable}\n")


def checksums(directory):
    paths = sorted(p for p in directory.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    if len(paths) != 5:
        raise ValueError("Expected wheel, source archive, and three native archives")
    lines = []
    for path in paths:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    (directory / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("version", "package-smoke", "native", "checksums"))
    parser.add_argument("--target")
    parser.add_argument("--directory", type=Path, default=Path("release-assets"))
    args = parser.parse_args()
    if args.command == "version":
        print(version())
    elif args.command == "package-smoke":
        package_smoke()
    elif args.command == "native":
        native(args.target)
    else:
        checksums(args.directory)
