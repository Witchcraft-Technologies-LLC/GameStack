"""Safe update state transitions against private synthetic Paper worlds."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import os
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from gamestack import backup, update
from gamestack.cli import main
from gamestack.pack import GameStackError, load_pack
from gamestack.runtime import Runtime

PACK = Path(__file__).resolve().parents[1] / 'packs/minecraft-paper/pack.yaml'


@unittest.skipUnless(hasattr(os, "getuid") and os.getuid() > 0, "Paper requires a non-root POSIX user")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runtime = Runtime(self.root / 'instances')
        self.old = load_pack(PACK)
        self.new = copy.deepcopy(self.old)
        self.new['version'] = '0.1.1'
        self.new['image'] = 'itzg/minecraft-server@sha256:' + 'a' * 64
        self.new['environment']['PAPER_BUILD']['value'] = '122'
        self.source = self.root / 'candidate'
        self.source.mkdir()
        self.pack_path = self.source / 'pack.yaml'
        self.new_jar = b'new Paper JAR'
        self.lock = {'image': {'reference': self.new['image'], 'platform': 'linux/amd64'},
                     'paper': {'version': '26.2', 'build': 122,
                               'sha256': hashlib.sha256(self.new_jar).hexdigest()}}
        self.write_candidate()
        values = {key: setting['value'] if 'value' in setting else
                  {'EULA': 'TRUE', 'MOTD': 'test', 'OPS': 'TestOwner', 'WHITELIST': 'TestOwner'}[key]
                  for key, setting in self.old['environment'].items()}
        self.directory = self.runtime.prepare(self.old, 'paper', values)
        self.data = self.directory / 'data'
        (self.data / 'paper-26.2-121.jar').write_bytes(b'old Paper JAR')
        (self.data / 'world.txt').write_text('before')
        self.state = 'running'
        self.fail = None
        self.unhealthy_after_up = False
        self.health_ok = True
        self.start_fails = False
        self.commands = []

    def write_candidate(self):
        self.pack_path.write_text(yaml.safe_dump(self.new))
        self.pack_path.with_name('upstream-lock.json').write_text(json.dumps(self.lock))

    def command(self, args, timeout=30):
        self.commands.append(args)
        if self.fail and self.fail in args:
            raise GameStackError('injected failure')
        if args[:3] == ['docker', 'image', 'pull']:
            return ''
        if args[:3] == ['docker', 'image', 'inspect']:
            return 'linux/amd64' if '{{.Os}}/{{.Architecture}}' in args else 'sha256:' + 'b' * 64
        if args[:3] == ['docker', 'inspect', '--format']:
            if args[3] == '{{.Image}}':
                return 'sha256:' + 'b' * 64
            return json.dumps({'Running': self.state == 'running', 'Paused': False,
                               'Restarting': False, 'ExitCode': 0})
        if 'stop' in args:
            self.state = 'exited'
            return ''
        if 'rm' in args:
            self.state = 'absent'
            return ''
        if 'up' in args:
            self.state = 'running'
            if '--force-recreate' in args:
                (self.data / 'paper-26.2-122.jar').write_bytes(self.new_jar)
                if self.unhealthy_after_up:
                    self.health_ok = False
            return ''
        if 'ps' in args:
            return 'abcdef123456' if self.state != 'absent' else ''
        return ''

    def start(self, *args):
        if self.start_fails:
            raise GameStackError('injected old restart failure')
        self.state = 'running'
        self.health_ok = True

    def run_update(self):
        with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'command', side_effect=self.command), \
             patch.object(self.runtime, 'backup_state', side_effect=lambda _: self.state), \
             patch.object(self.runtime, 'quick_status', side_effect=lambda _: 'started (healthy)' if self.state == 'running' and self.health_ok else 'started (unhealthy)' if self.state == 'running' else 'stopped'), \
             patch.object(self.runtime, 'start_server', side_effect=self.start):
            return update.run(self.runtime, 'paper', self.pack_path)

    def recover(self):
        with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'command', side_effect=self.command), \
             patch.object(self.runtime, 'backup_state', side_effect=lambda _: self.state), \
             patch.object(self.runtime, 'quick_status', side_effect=lambda _: 'started (healthy)' if self.state == 'running' and self.health_ok else 'started (unhealthy)' if self.state == 'running' else 'stopped'), \
             patch.object(self.runtime, 'start_server', side_effect=self.start):
            return update.recover(self.runtime, 'paper')

    def test_candidate_restricts_all_other_changes_and_lock(self):
        self.assertEqual(update.candidate(self.old, self.pack_path)[0], self.new)
        for edit in (lambda p: p['ports'][0].update(host=25566),
                     lambda p: p['environment']['MOTD'].update(default='leak'),
                     lambda p: p['healthcheck'].append('extra'),
                     lambda p: p.update(data_path='/other')):
            with self.subTest(edit=edit):
                saved = copy.deepcopy(self.new)
                edit(self.new)
                self.write_candidate()
                with self.assertRaises(GameStackError):
                    update.candidate(self.old, self.pack_path)
                self.new = saved
        self.lock['paper']['sha256'] = '0' * 64
        self.write_candidate()
        self.assertEqual(update.candidate(self.old, self.pack_path)[1]['paper']['sha256'], '0' * 64)
        self.lock['paper']['build'] = 123
        self.write_candidate()
        with self.assertRaises(GameStackError):
            update.candidate(self.old, self.pack_path)

    def test_update_and_recover_retains_both_worlds(self):
        result = self.run_update()
        self.assertEqual(self.state, 'running')
        self.assertEqual(load_pack(self.directory / 'pack.yaml'), self.new)
        backup.verify(result.backup, 'paper')
        self.assertFalse((self.directory / update.MARKER).exists())
        (self.data / 'world.txt').write_text('after')
        recovered = self.recover()
        self.assertEqual((self.data / 'world.txt').read_text(), 'before')
        self.assertEqual((recovered.retained_data / 'world.txt').read_text(), 'after')
        self.assertEqual(load_pack(self.directory / 'pack.yaml'), self.old)
        self.assertTrue(result.backup.exists())
        self.assertEqual(self.state, 'running')

    def test_candidate_changed_after_confirmation_is_rejected_before_downtime(self):
        expected = update.candidate(self.old, self.pack_path)
        self.new['environment']['PAPER_BUILD']['value'] = '123'
        self.lock['paper']['build'] = 123
        self.write_candidate()
        with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'command', side_effect=self.command), \
             patch.object(self.runtime, 'quick_status', return_value='started (healthy)'), \
             self.assertRaisesRegex(GameStackError, 'changed since confirmation'):
            update.run(self.runtime, 'paper', self.pack_path, expected=expected)
        self.assertEqual(self.state, 'running')
        self.assertFalse((self.directory / update.MARKER).exists())
        self.assertFalse(self.commands)

    def test_cli_confirmation_and_output_do_not_expose_saved_settings(self):
        document = yaml.safe_load((self.directory / 'compose.yaml').read_text())
        document['services']['server']['environment']['MOTD'] = 'private-personal-setting'
        (self.directory / 'compose.yaml').write_text(yaml.safe_dump(document))
        output, errors = io.StringIO(), io.StringIO()
        with patch('gamestack.cli.Runtime', return_value=self.runtime), \
             patch('sys.stdin.isatty', return_value=False), \
             redirect_stdout(output), redirect_stderr(errors):
            code = main(['update', 'paper', '--pack', str(self.pack_path)])
        self.assertEqual(code, 1)
        self.assertIn('--yes', errors.getvalue())
        self.assertNotIn('private-personal-setting', output.getvalue() + errors.getvalue())
        with patch('gamestack.cli.Runtime', return_value=self.runtime), \
             patch('gamestack.update.run', side_effect=GameStackError('injected failure')), \
             redirect_stdout(output), redirect_stderr(errors):
            code = main(['update', 'paper', '--pack', str(self.pack_path), '--yes'])
        self.assertEqual(code, 1)
        self.assertNotIn('private-personal-setting', output.getvalue() + errors.getvalue())

    def test_pull_failure_has_no_downtime(self):
        self.fail = 'pull'
        with self.assertRaises(GameStackError):
            self.run_update()
        self.assertEqual(self.state, 'running')
        self.assertEqual((self.data / 'world.txt').read_text(), 'before')
        self.assertFalse((self.directory / update.MARKER).exists())

    def test_failed_new_jar_blocks_mutation_until_recovery(self):
        self.lock['paper']['sha256'] = '0' * 64
        self.write_candidate()
        with self.assertRaisesRegex(GameStackError, 'Update failed'):
            self.run_update()
        self.assertTrue((self.directory / update.MARKER).exists())
        self.assertEqual(self.state, 'exited')
        with self.assertRaisesRegex(GameStackError, 'update needs recovery'):
            self.runtime.backup('paper')
        result = self.recover()
        self.assertEqual((self.data / 'world.txt').read_text(), 'before')
        self.assertEqual(self.state, 'running')
        self.assertTrue(result.retained_data.exists())

    def test_interruption_during_each_configuration_switch_is_recoverable(self):
        original = update._replace_private
        for interrupted_call in (1, 2):
            with self.subTest(interrupted_call=interrupted_call):
                count = [0]
                def interrupt(source, destination):
                    count[0] += 1
                    if count[0] == interrupted_call:
                        raise KeyboardInterrupt()
                    return original(source, destination)
                with patch.object(update, '_replace_private', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
                    self.run_update()
                self.assertTrue((self.directory / update.MARKER).exists())
                self.recover()
                self.assertEqual(load_pack(self.directory / 'pack.yaml'), self.old)
                self.assertEqual((self.data / 'world.txt').read_text(), 'before')
                self.assertEqual(self.state, 'running')
                # A fresh candidate can be applied after recovery.

    def test_interrupted_recovery_after_world_switch_resumes(self):
        self.run_update()
        (self.data / 'world.txt').write_text('after')
        original = update.restore.move_directory
        def interrupt(source, destination):
            original(source, destination)
            if destination.name == 'post-update-data':
                raise KeyboardInterrupt()
        with patch.object(update.restore, 'move_directory', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.recover()
        self.assertTrue((self.directory / update.MARKER).exists())
        result = self.recover()
        self.assertEqual((self.data / 'world.txt').read_text(), 'before')
        self.assertEqual((result.retained_data / 'world.txt').read_text(), 'after')

    def test_recovery_resumes_after_old_configuration_and_data_switches(self):
        for target_name in ('pack.yaml', 'compose.yaml', 'instance.yaml', 'data'):
            with self.subTest(target=target_name):
                self.run_update()
                (self.data / 'world.txt').write_text('after')
                if target_name == 'data':
                    original = update.restore.move_directory
                    def interrupt(source, destination):
                        original(source, destination)
                        if destination == self.data:
                            raise KeyboardInterrupt()
                    patcher = patch.object(update.restore, 'move_directory', side_effect=interrupt)
                else:
                    original = update._replace_private
                    def interrupt(source, destination):
                        original(source, destination)
                        if destination.name == target_name:
                            raise KeyboardInterrupt()
                    patcher = patch.object(update, '_replace_private', side_effect=interrupt)
                with patcher, self.assertRaises(KeyboardInterrupt):
                    self.recover()
                result = self.recover()
                self.assertEqual((self.data / 'world.txt').read_text(), 'before')
                self.assertEqual((result.retained_data / 'world.txt').read_text(), 'after')
                self.assertEqual(load_pack(self.directory / 'pack.yaml'), self.old)

    def test_image_mismatch_and_unhealthy_candidate_leave_recovery(self):
        for cause in ('image', 'health'):
            with self.subTest(cause=cause):
                original = self.command
                if cause == 'image':
                    def changed(args, timeout=30):
                        if args[:4] == ['docker', 'inspect', '--format', '{{.Image}}']:
                            return 'sha256:' + 'c' * 64
                        return original(args, timeout)
                    self.command = changed
                else:
                    self.new_jar = b'new Paper JAR'
                    self.lock['paper']['sha256'] = hashlib.sha256(self.new_jar).hexdigest()
                    self.write_candidate()
                    self.unhealthy_after_up = True
                with self.assertRaisesRegex(GameStackError, 'Update failed'):
                    self.run_update()
                self.assertTrue((self.directory / update.MARKER).exists())
                self.command = original
                self.fail = None
                self.unhealthy_after_up = False
                self.recover()
                self.assertEqual(self.state, 'running')

    def test_recovery_pull_failure_keeps_updated_world_running(self):
        self.run_update()
        (self.data / 'world.txt').write_text('after')
        self.fail = 'pull'
        with self.assertRaises(GameStackError):
            self.recover()
        self.assertEqual(self.state, 'running')
        self.assertEqual((self.data / 'world.txt').read_text(), 'after')
        self.assertFalse((self.directory / update.MARKER).exists())

    def test_backup_space_failure_is_before_downtime(self):
        with patch.object(backup.shutil, 'disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaisesRegex(GameStackError, 'bytes required'):
                self.run_update()
        self.assertEqual(self.state, 'running')
        self.assertFalse((self.directory / update.MARKER).exists())

    def test_failed_capture_and_restart_can_recover_without_backup(self):
        self.start_fails = True
        with patch.object(backup, 'create', side_effect=GameStackError('capture failed')):
            with self.assertRaisesRegex(GameStackError, 'capture failed'):
                self.run_update()
        self.assertTrue((self.directory / update.MARKER).exists())
        self.assertEqual(self.state, 'exited')
        self.start_fails = False
        result = self.recover()
        self.assertIsNone(result.backup)
        self.assertIsNone(result.retained_data)
        self.assertEqual(self.state, 'running')
        self.assertEqual((self.data / 'world.txt').read_text(), 'before')

    def test_failed_backup_restarts_old_server(self):
        with patch.object(backup, 'create', side_effect=GameStackError('capture failed')):
            with self.assertRaisesRegex(GameStackError, 'capture failed'):
                self.run_update()
        self.assertEqual(self.state, 'running')
        self.assertEqual(load_pack(self.directory / 'pack.yaml'), self.old)
        self.assertFalse((self.directory / update.MARKER).exists())


if __name__ == '__main__':
    unittest.main()
