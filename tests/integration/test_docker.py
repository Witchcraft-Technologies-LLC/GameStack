"""Explicit opt-in tests using only synthetic data and a disposable CI container."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

import yaml

IMAGE = "nginx@sha256:dc5069ad14f19660b141b21236140b91656bf89bbc3e2417c70ae650cd66104c"


@unittest.skipUnless(os.environ.get("GAMESTACK_DOCKER_TEST") == "1", "Set GAMESTACK_DOCKER_TEST=1 on a disposable Docker host")
class DockerIntegration(unittest.TestCase):
    def test_lifecycle_and_persistence(self):
        command = [os.environ["GAMESTACK_EXECUTABLE"]] if os.environ.get("GAMESTACK_EXECUTABLE") else [sys.executable, "-m", "gamestack"]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "gamestack"
            instance = "ci-" + uuid.uuid4().hex[:12]
            directory = root / instance
            project = "gamestack-" + hashlib.sha256(str(directory).encode()).hexdigest()[:16]
            pack = base / "pack.yaml"
            pack.write_text(yaml.safe_dump({
                "schema_version": 1, "id": instance, "version": "0.1.0", "name": "Synthetic CI server",
                "image": IMAGE, "data_path": "/data", "stop_timeout": 10, "ports": [], "environment": {},
                "healthcheck": ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"],
            }), encoding="utf-8")
            def docker(*args):
                return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=180).stdout.strip()
            def cli(*args):
                result = subprocess.run([*command, "--root", str(root), *args], capture_output=True, text=True, timeout=960)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            compose = ["compose", "--project-name", project, "--file", str(directory / "compose.yaml")]
            try:
                cli("install", str(pack), "--prepare-only")
                save = directory / "data/save.txt"
                save.write_text("synthetic persistent world", encoding="utf-8")
                cli("start", instance)
                self.assertIn("healthy", cli("status", instance))
                self.assertIn("started (healthy)", cli("list"))
                self.assertIn("Verified backup:", cli("backup", instance))
                self.assertIn("healthy", cli("status", instance))
                cli("stop", instance)
                self.assertIn("stopped", cli("list"))
                self.assertIn("stopped", cli("backup", instance))
                archives = sorted((directory / "backups").glob("*.tar"))
                self.assertEqual(len(archives), 2)
                for archive in archives:
                    self.assertIn("integrity verified", cli("backup", "verify", instance, archive.stem))
                self.assertIn("does not recheck integrity", cli("backup", "list", instance))
                save.write_text("later synthetic world", encoding="utf-8")
                self.assertIn("health not tested", cli("restore", instance, archives[0].stem, "--yes"))
                self.assertEqual(save.read_text(), "synthetic persistent world")
                safety = sorted((directory / "backups").glob("*.tar"))[-1]
                self.assertIn("health not tested", cli("restore", instance, safety.stem, "--yes"))
                self.assertEqual(save.read_text(), "later synthetic world")
                cli("start", instance)
                self.assertIn("healthy", cli("restore", instance, archives[0].stem, "--yes"))
                self.assertEqual(save.read_text(), "synthetic persistent world")
                self.assertIn("healthy", cli("status", instance))
                cli("restart", instance)
                first_id = docker(*compose, "ps", "--quiet", "server")
                self.assertEqual(docker("exec", first_id, "cat", "/data/save.txt"), "synthetic persistent world")
                cli("stop", instance)
                docker(*compose, "rm", "--force", "server")
                cli("start", instance)
                second_id = docker(*compose, "ps", "--quiet", "server")
                self.assertNotEqual(first_id, second_id)
                self.assertEqual(docker("exec", second_id, "cat", "/data/save.txt"), "synthetic persistent world")
                cli("rm", instance, "--yes")
                self.assertEqual(docker(*compose, "ps", "--all", "--quiet", "server"), "")
                self.assertEqual(save.read_text(encoding="utf-8"), "synthetic persistent world")
                self.assertNotIn(instance, cli("list"))
            finally:
                already_failed = sys.exc_info()[0] is not None
                # Query fixed state fields only, never configuration, logs, or health output.
                report = {"project": project, "image": IMAGE}
                try:
                    report["states"] = docker("ps", "--all", "--filter", f"label=com.docker.compose.project={project}",
                                              "--format", "{{.State}}")
                except subprocess.SubprocessError:
                    report["states"] = "unavailable"
                diagnostics = Path("ci-diagnostics")
                diagnostics.mkdir(exist_ok=True)
                try:
                    if (directory / "compose.yaml").exists():
                        # Only the unique project created by this test; never prune Docker globally.
                        docker(*compose, "down", "--timeout", "10")
                except subprocess.SubprocessError:
                    report["cleanup"] = "failed; inspect the unique project before retrying"
                    if not already_failed:
                        raise
                finally:
                    (diagnostics / f"{instance}.json").write_text(json.dumps(report), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
