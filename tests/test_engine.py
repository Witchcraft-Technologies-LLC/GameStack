import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import yaml

from gamestack.cli import main
from gamestack.pack import CONFIG_LIMIT, GameStackError, load_pack, parse_yaml, read_yaml, validate
from gamestack.runtime import Runtime, checked_root, compose
from gamestack.filesystem import safe_child

EXAMPLE = Path(__file__).resolve().parents[1] / "packs/example/pack.yaml"


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "world storage ü"
        self.runtime = Runtime(self.root)
        self.pack = load_pack(EXAMPLE)
        self.values = {"SERVER_NAME": "Friends", "SERVER_PASSWORD": "synthetic-$VALUE-'password"}

    def prepare(self):
        return self.runtime.prepare(self.pack, "friends", self.values)

    def test_yaml_reads_are_bounded_and_parsing_is_shared(self):
        payload = b'key: value\n' + b' ' * (CONFIG_LIMIT - 11)
        self.assertEqual(parse_yaml(payload), {'key': 'value'})
        stream = io.BytesIO(payload + b'x' * 100)
        with patch.object(Path, 'open', return_value=stream):
            with self.assertRaisesRegex(GameStackError, 'too large'):
                read_yaml(Path('synthetic.yaml'))
        # Check the read bound even when a source grows or has no useful stat size.
        source = MagicMock()
        source.__enter__.return_value.read.return_value = b'key: value'
        with patch.object(Path, 'open', return_value=source):
            self.assertEqual(read_yaml(Path('synthetic.yaml')), {'key': 'value'})
        source.__enter__.return_value.read.assert_called_once_with(CONFIG_LIMIT + 1)
        for bad in (b'key: a\nkey: secret', b'- secret', b'key: \xff', b'key: ['):
            with self.subTest(bad=bad), self.assertRaises(GameStackError):
                parse_yaml(bad)

    def test_managed_children_reject_traversal_and_linked_ancestors(self):
        directory = self.prepare()
        for component in ('', '.', '..', '../other', 'data/../other', 'data/file', str(directory)):
            with self.subTest(component=component), self.assertRaises(GameStackError):
                safe_child(directory, component)
        link = self.root.parent / 'linked'
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        with self.assertRaises(GameStackError):
            safe_child(link / 'friends', 'data')
        self.assertEqual(safe_child(directory, 'new'), directory / 'new')

    def test_operations_use_configuration_revalidated_under_lock(self):
        for operation in ('backup', 'remove', 'start', 'stop', 'restart'):
            with self.subTest(operation=operation):
                runtime = Runtime(self.root / operation)
                directory = runtime.prepare(self.pack, 'friends', self.values)
                changed = copy.deepcopy(self.pack)
                changed['stop_timeout'] = 99
                original_lock = runtime.lock
                @contextlib.contextmanager
                def lock(path):
                    with original_lock(path):
                        (directory / 'pack.yaml').write_text(yaml.safe_dump(changed), encoding='utf-8')
                        (directory / 'compose.yaml').write_text(yaml.safe_dump(compose(changed, self.values, directory)), encoding='utf-8')
                        yield
                with patch.object(runtime, 'lock', side_effect=lock), patch.object(runtime, 'doctor'), \
                        patch.object(runtime, 'command', return_value='') as command, \
                        patch.object(runtime, 'start_server') as start, \
                        patch.object(runtime, 'backup_state', side_effect=['running', 'exited']), \
                        patch('gamestack.backup.create', return_value=directory / 'synthetic.tar') as create:
                    if operation in ('backup', 'remove'):
                        getattr(runtime, operation)('friends')
                    else:
                        runtime.lifecycle(operation, 'friends')
                if operation != 'start':
                    stop = next(c.args[0] for c in command.call_args_list if 'stop' in c.args[0])
                    self.assertIn('99', stop)
                if operation in ('backup', 'start', 'restart'):
                    self.assertEqual(start.call_args.args[1], changed)
                if operation == 'backup':
                    self.assertEqual(create.call_args.args[2], changed)

    def test_command_does_not_search_outside_path(self):
        # Windows CreateProcess also searches system directories when PATH is
        # empty. Resolve tooling ourselves so it cannot launch such a Docker.
        empty_path = self.root.parent / 'empty-path'
        empty_path.mkdir()
        with patch.dict(os.environ, {'PATH': str(empty_path)}), \
                patch('gamestack.runtime.subprocess.run') as process:
            with self.assertRaises(GameStackError):
                self.runtime.command(['docker', 'info'])
        process.assert_not_called()

    def test_command_uses_resolved_executable_with_spaces(self):
        executable = str(self.root.parent / 'tools with spaces' / 'docker.exe')
        arguments = ['docker', 'info']
        with patch('gamestack.runtime.shutil.which', return_value=executable), \
                patch('gamestack.runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'version')) as process:
            self.assertEqual(self.runtime.command(arguments, timeout=7), 'version')
        self.assertEqual(process.call_args.args[0], [executable, 'info'])
        self.assertEqual(process.call_args.kwargs['timeout'], 7)
        self.assertEqual(arguments, ['docker', 'info'])

    def test_list_missing_and_empty_root_does_not_create_files(self):
        self.assertEqual(self.runtime.list_instances(), [])
        self.assertFalse(self.root.exists())
        self.root.mkdir()
        self.assertEqual(self.runtime.list_instances(), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_list_shows_sorted_instance_names_with_health_without_secrets(self):
        self.prepare()
        self.runtime.prepare(self.pack, "another", self.values)
        output = io.StringIO()
        with patch.object(Runtime, "command", return_value='[{"State":"running","Health":"healthy"}]') as process, contextlib.redirect_stdout(output):
            self.assertEqual(main(["--root", str(self.root), "list"]), 0)
            self.assertEqual(process.call_count, 2)
        self.assertIn("  another: started (healthy)\n  friends: started (healthy)\n", output.getvalue())
        self.assertNotIn(self.values["SERVER_PASSWORD"], output.getvalue())
        self.assertNotIn("  example\n", output.getvalue())

    def test_quick_status_health_and_container_states(self):
        self.prepare()
        cases = [
            ({"State": "running", "Health": "healthy"}, "started (healthy)"),
            ({"State": "running", "Health": "unhealthy"}, "started (unhealthy)"),
            ({"State": "running", "Health": "starting"}, "started (starting)"),
            ({"State": "running", "Health": ""}, "started"),
            ({"State": "exited", "ExitCode": 0, "Health": "healthy"}, "stopped"),
            ({"State": "exited", "ExitCode": 1}, "crashed (nonzero exit)"),
            ({"State": "exited"}, "stopped (exit code unavailable)"),
            ({"State": "dead"}, "crashed"),
            ({"State": "created"}, "stopped"),
            ({"State": "restarting"}, "restarting"),
            ({"State": "paused"}, "paused"),
        ]
        for row, expected in cases:
            for raw in (json.dumps([row]), json.dumps(row) + "\n"):
                with self.subTest(row=row), patch.object(self.runtime, "command", return_value=raw):
                    self.assertEqual(self.runtime.quick_status("friends"), expected)
        with patch.object(self.runtime, "command", return_value="[]") as command:
            self.assertEqual(self.runtime.quick_status("friends"), "stopped (not created)")
            self.assertEqual(command.call_args.kwargs["timeout"], 5)
            self.assertEqual(command.call_args.args[0][-5:], ["ps", "--all", "--format", "json", "server"])
        self.assertFalse((self.root / "friends/.operation.lock").exists())

    def test_quick_status_malformed_output_is_unknown_and_redacted(self):
        for raw in ('secret-value', '[null]', '{"State":"secret-value"}', '[{}, {}]'):
            with patch.object(self.runtime, "command", return_value=raw), self.assertLogs("gamestack.runtime", level="WARNING") as logs:
                self.assertEqual(self.runtime.quick_status("friends"), "unknown (status unavailable)")
            self.assertNotIn("secret-value", "\n".join(logs.output))

    def test_list_keeps_instances_when_docker_fails(self):
        self.prepare()
        output = io.StringIO()
        with patch("gamestack.runtime.shutil.which", return_value=str(self.root / 'docker')), \
                patch("gamestack.runtime.subprocess.run", side_effect=FileNotFoundError("secret-value")), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(main(["--root", str(self.root), "list"]), 0)
        self.assertIn("friends: unknown (status unavailable)", output.getvalue())
        self.assertIn("gamestack doctor", output.getvalue())
        self.assertNotIn("secret-value", output.getvalue())

    def test_list_skips_invalid_instances_and_symlinks_with_guidance(self):
        self.prepare()
        broken = self.runtime.prepare(self.pack, "broken", self.values)
        (broken / "compose.yaml").write_text("secret-value: [", encoding="utf-8")
        (self.root / "partial").mkdir()
        (self.root / "linked").symlink_to(self.root / "friends", target_is_directory=True)
        (self.root / "notes.txt").write_text("unrelated", encoding="utf-8")
        with self.assertLogs("gamestack.runtime", level="WARNING") as logs:
            self.assertEqual(self.runtime.list_instances(), ["friends"])
        messages = "\n".join(logs.output)
        for instance in ("broken", "partial", "linked"):
            self.assertIn(f"gamestack doctor {instance}", messages)
        self.assertNotIn("secret-value", messages)
        self.assertEqual((broken / "compose.yaml").read_text(), "secret-value: [")

    def test_list_root_access_failure_returns_nonzero(self):
        output = io.StringIO()
        with patch.object(Path, "iterdir", side_effect=PermissionError("secret-value")), contextlib.redirect_stderr(output):
            self.assertEqual(main(["--root", str(self.root), "list"]), 1)
        self.assertIn("permissions", output.getvalue())
        self.assertNotIn("secret-value", output.getvalue())

    def test_doctor_identifies_missing_compose_without_leaking_output(self):
        with patch("gamestack.runtime.platform.system", return_value="Linux"), patch("gamestack.runtime.platform.machine", return_value="x86_64"), patch.object(self.runtime, "command", side_effect=["version", GameStackError("secret-value")]):
            with self.assertRaises(GameStackError) as caught:
                self.runtime.doctor()
        self.assertIn("Docker Compose is unavailable", str(caught.exception))
        self.assertIn("docker compose version", str(caught.exception))
        self.assertNotIn("secret-value", str(caught.exception))

    def test_remove_preserves_files_and_retires_instance(self):
        directory = self.prepare()
        (directory / "data/save").write_text("synthetic world")
        (directory / "backups").mkdir()
        (directory / "backups/save").write_text("synthetic backup")
        before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
        with patch.object(self.runtime, "doctor"), patch.object(self.runtime, "command", return_value="") as command:
            self.assertEqual(self.runtime.remove("friends"), directory)
        calls = [c.args[0] for c in command.call_args_list]
        self.assertEqual(calls[0][-4:], ["stop", "--timeout", "120", "server"])
        self.assertEqual(calls[1][-3:], ["rm", "--force", "server"])
        self.assertEqual(calls[2][-4:], ["ps", "--all", "--quiet", "server"])
        for relative, contents in before.items():
            self.assertEqual((directory / relative).read_bytes(), contents)
        self.assertEqual(self.runtime.list_instances(), [])
        with self.assertRaises(GameStackError):
            self.runtime.lifecycle("start", "friends")
        with self.assertRaises(GameStackError):
            self.prepare()
        self.assertFalse((directory / ".operation.lock").exists())

    def test_remove_failures_retain_active_configuration(self):
        directory = self.prepare()
        for outputs in ([GameStackError("stop failed")], ["", GameStackError("remove failed")], ["", "", "container-id"]):
            with patch.object(self.runtime, "doctor"), patch.object(self.runtime, "command", side_effect=outputs) as command:
                with self.assertRaises(GameStackError):
                    self.runtime.remove("friends")
                self.assertEqual(command.call_count, len(outputs))
            self.assertFalse((directory / "removed.yaml").exists())
            self.assertEqual(self.runtime.list_instances(), ["friends"])

    def test_remove_rejects_lock_and_unsafe_paths_before_docker(self):
        directory = self.prepare()
        with self.runtime.lock(directory), patch.object(self.runtime, "command") as command:
            with self.assertRaises(GameStackError):
                self.runtime.remove("friends")
            command.assert_not_called()
        (directory / "data").rmdir()
        (directory / "data").symlink_to(Path(self.tmp.name).resolve(), target_is_directory=True)
        with patch.object(self.runtime, "command") as command:
            for instance in ("friends", "../escape"):
                with self.assertRaises(GameStackError):
                    self.runtime.remove(instance)
            command.assert_not_called()

    def test_remove_cli_confirmation_and_cancellation(self):
        self.prepare()
        args = ["--root", str(self.root), "rm", "friends"]
        output = io.StringIO()
        with patch.object(Runtime, "remove") as remove, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with patch("gamestack.cli.sys.stdin.isatty", return_value=False):
                self.assertEqual(main(args), 1)
            with patch("gamestack.cli.sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="n"):
                self.assertEqual(main(args), 0)
            remove.assert_not_called()
            self.assertEqual(main(args + ["--yes"]), 0)
            remove.assert_called_once_with("friends")

    def test_remove_marker_write_failure_retains_recovery_files(self):
        directory = self.prepare()
        with patch.object(self.runtime, "doctor"), patch.object(self.runtime, "command", return_value=""), patch("gamestack.runtime.write_private", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.runtime.remove("friends")
        self.assertTrue((directory / "data").is_dir())
        self.assertTrue((directory / "instance.yaml").is_file())
        self.assertFalse((directory / ".operation.lock").exists())

    def test_schema_rejects_invalid_fields(self):
        for key, value in (("schema_version", True), ("schema_version", 2), ("id", "../world"),
                           ("image", "server:latest"), ("data_path", "/"), ("data_path", "/data/../etc"),
                           ("stop_timeout", True), ("healthcheck", []), ("user", "0:0"), ("unknown", 1)):
            with self.subTest(key=key, value=value):
                pack = copy.deepcopy(self.pack)
                pack[key] = value
                with self.assertRaises(GameStackError):
                    validate(pack)

    def test_yaml_rejects_duplicate_keys_and_unsafe_tags_without_source_leak(self):
        path = Path(self.tmp.name).resolve() / "invalid.yaml"
        for contents in ('id: a\nid: secret-value\n', '!!python/object:secret-value {}', 'id: [secret-value'):
            path.write_text(contents)
            with self.assertRaises(GameStackError) as caught:
                read_yaml(path)
            self.assertNotIn("secret-value", str(caught.exception))

    def test_prepare_persists_private_config_and_escapes_compose(self):
        directory = self.prepare()
        self.assertEqual(self.runtime.inspect("friends")[1], self.pack)
        document = read_yaml(directory / "compose.yaml")
        server = document["services"]["server"]
        self.assertEqual(server["environment"]["SERVER_PASSWORD"], self.values["SERVER_PASSWORD"].replace("$", "$$"))
        self.assertEqual(server["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(server["volumes"][0]["source"], str(directory / "data"))
        if os.name == "posix":
            self.assertEqual((directory / "compose.yaml").stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_existing_instance_and_world_never_overwritten(self):
        directory = self.prepare()
        world = directory / "data/save"
        world.write_text("irreplaceable synthetic world")
        with self.assertRaises(GameStackError):
            self.prepare()
        self.assertEqual(world.read_text(), "irreplaceable synthetic world")

    def test_paths_reject_unsafe_roots_traversal_and_symlinks(self):
        for root in (Path("/"), Path("/srv"), Path.home()):
            with self.assertRaises(GameStackError):
                checked_root(root)
        with self.assertRaises(GameStackError):
            self.runtime.directory("../elsewhere")
        directory = self.prepare()
        (directory / "data").rmdir()
        (directory / "data").symlink_to(Path(self.tmp.name).resolve(), target_is_directory=True)
        with self.assertRaises(GameStackError):
            self.runtime.inspect("friends")
        link = Path(self.tmp.name).resolve() / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(GameStackError):
            Runtime(link)

    def test_changed_compose_is_rejected(self):
        directory = self.prepare()
        document = read_yaml(directory / "compose.yaml")
        document["services"]["server"]["privileged"] = True
        (directory / "compose.yaml").write_text(yaml.safe_dump(document))
        with self.assertRaises(GameStackError):
            self.runtime.inspect("friends")

    def test_interrupted_prepare_retains_files_but_is_not_operable(self):
        with patch("gamestack.runtime.write_private", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertTrue((self.root / "friends/data").is_dir())
        with self.assertRaises(GameStackError):
            self.runtime.inspect("friends")

    def test_lifecycle_waits_for_health_and_does_not_delete_world(self):
        directory = self.prepare()
        (directory / "data/save").write_text("world")
        with patch.object(self.runtime, "doctor"), patch.object(self.runtime, "command", return_value="") as command:
            self.assertEqual(self.runtime.lifecycle("restart", "friends"), "healthy")
        calls = [c.args[0] for c in command.call_args_list]
        self.assertIn("stop", calls[0])
        self.assertIn("--wait", calls[1])
        self.assertIn("--no-recreate", calls[1])
        self.assertEqual((directory / "data/save").read_text(), "world")
        self.assertFalse((directory / ".operation.lock").exists())

    def test_failed_shutdown_prevents_restart(self):
        directory = self.prepare()
        with patch.object(self.runtime, "doctor"), patch.object(self.runtime, "command", side_effect=GameStackError("Failed")) as command:
            with self.assertRaises(GameStackError):
                self.runtime.lifecycle("restart", "friends")
        self.assertEqual(command.call_count, 1)
        self.assertTrue((directory / "data").exists())

    def test_lock_excludes_operations(self):
        directory = self.prepare()
        with self.runtime.lock(directory), patch.object(self.runtime, "doctor") as doctor:
            with self.assertRaises(GameStackError):
                self.runtime.lifecycle("start", "friends")
            doctor.assert_not_called()

    def test_subprocess_failures_do_not_expose_secrets(self):
        for error in (subprocess.CalledProcessError(1, ["secret-value"], stderr="secret-value"),
                      subprocess.TimeoutExpired(["secret-value"], 1), FileNotFoundError("secret-value")):
            with patch("gamestack.runtime.shutil.which", return_value=str(self.root / 'docker')), \
                    patch("gamestack.runtime.subprocess.run", side_effect=error) as process:
                with self.assertRaises(GameStackError) as caught:
                    self.runtime.command(["docker", "info"])
                self.assertNotIn("secret-value", str(caught.exception))
                process.assert_called_once()

    def test_status_sanitizes_untrusted_output(self):
        self.prepare()
        with patch.object(self.runtime, "doctor"), patch.object(self.runtime, "command", return_value='{"State":"secret-value","Health":"secret-value"}'):
            self.assertEqual(self.runtime.lifecycle("status", "friends"), "unknown / health unavailable")

    def test_cli_prepare_and_duplicate_exit_status(self):
        values = Path(self.tmp.name).resolve() / "values.yaml"
        values.write_text(yaml.safe_dump(self.values))
        args = ["--root", str(self.root), "install", str(EXAMPLE), "--name", "friends", "--values", str(values), "--prepare-only"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(main(args), 0)
            self.assertEqual(main(args), 1)
        self.assertNotIn(self.values["SERVER_PASSWORD"], output.getvalue())
        self.assertIn("already exists", output.getvalue())

    def test_failed_health_is_not_reported_successful_in_debug(self):
        self.prepare()
        output = io.StringIO()
        with patch("gamestack.runtime.shutil.which", return_value=str(self.root / 'docker')), \
                patch.object(Runtime, "doctor"), patch("gamestack.runtime.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["secret-value"], stderr="secret-value")), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(main(["--debug", "--root", str(self.root), "start", "friends"]), 1)
        self.assertNotIn("secret-value", output.getvalue())
        self.assertNotIn("friends: healthy", output.getvalue())
        self.assertTrue((self.root / "friends/data").is_dir())


if __name__ == "__main__":
    unittest.main()
