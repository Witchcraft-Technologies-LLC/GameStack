"""Restoration and failure boundaries using private synthetic worlds only."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import io
import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import tarfile
import unittest
from unittest.mock import patch

import yaml

from gamestack import backup, restore
from gamestack.cli import main
from gamestack.pack import GameStackError, load_pack
from gamestack.runtime import Runtime

PACK = Path(__file__).resolve().parents[1] / 'packs/example/pack.yaml'


class RestoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Runtime(Path(temporary.name).resolve() / 'world storage ü')
        self.pack = load_pack(PACK)
        self.directory = self.runtime.prepare(self.pack, 'friends', {'SERVER_NAME': 'test', 'SERVER_PASSWORD': 'secret-test'})
        self.data = self.directory / 'data'
        (self.data / 'nested ü').mkdir()
        (self.data / 'empty').mkdir()
        self.save = self.data / 'nested ü/save file'
        self.save.write_bytes(b'earlier world')
        os.chmod(self.save, 0o640)
        os.utime(self.save, (1700000000, 1700000000))
        self.archive = backup.create(self.directory, 'friends', self.pack, 'exited')
        self.archive_bytes = self.archive.read_bytes()
        self.save.write_bytes(b'later world')
        (self.data / 'later-only').write_bytes(b'new')
        self.config = {f: (self.directory / f).read_bytes() for f in backup.CONFIG}

    @contextmanager
    def server(self, initial='exited', start_error=None, stop_error=None, remove_error=None):
        state = [initial]
        def command(args, *unused):
            if 'stop' in args:
                if stop_error:
                    raise stop_error
                state[0] = 'exited'
            if 'rm' in args:
                if remove_error:
                    raise remove_error
                state[0] = 'absent'
            return ''
        def start(*args):
            state[0] = 'running'
            if start_error:
                raise start_error
        with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'restore_state', side_effect=lambda _: state[0]), \
                patch.object(self.runtime, 'backup_state', side_effect=lambda _: state[0]), \
                patch.object(self.runtime, 'command', side_effect=command) as cmd, \
                patch.object(self.runtime, 'start_server', side_effect=start) as startup:
            yield state, cmd, startup

    def run_restore(self, initial='exited', present=True):
        return self.runtime.restore('friends', self.archive.stem, expected_state=initial, expected_data=present)

    def assert_current(self):
        self.assertEqual(self.save.read_bytes(), b'later world')
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)

    def test_exact_roundtrip_and_restorable_safety_copy(self):
        with self.server():
            result = self.run_restore()
        self.assertEqual(self.save.read_bytes(), b'earlier world')
        self.assertFalse((self.data / 'later-only').exists())
        self.assertTrue((self.data / 'empty').is_dir())
        self.assertEqual(self.save.stat().st_mtime, 1700000000)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(self.save.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(result.recovery_directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(result.safety_backup.stat().st_mode), 0o600)
        self.assertEqual((result.recovery_directory / 'previous-data/nested ü/save file').read_bytes(), b'later world')
        self.assertFalse((self.directory / restore.MARKER).exists())
        self.assertEqual(self.config, {f: (self.directory / f).read_bytes() for f in backup.CONFIG})
        self.assertEqual(self.archive.read_bytes(), self.archive_bytes)
        backup.verify(result.safety_backup, 'friends')
        with self.server():
            self.runtime.restore('friends', result.safety_backup.stem, expected_state='exited', expected_data=True)
        self.assert_current()

    def test_running_stopped_created_absent_and_crashed(self):
        for initial in ('running', 'exited', 'created', 'absent', 'crashed'):
            with self.subTest(initial=initial), self.server(initial) as (_, cmd, start):
                result = self.run_restore(initial)
                self.assertEqual(start.call_count, int(initial == 'running'))
                self.assertEqual(result.state, 'healthy' if initial == 'running' else 'stopped (health not tested)')
                self.assertEqual(backup.verify(result.safety_backup, 'friends')['initial_state'], initial)
                self.assertEqual(sum('stop' in call.args[0] for call in cmd.call_args_list), int(initial == 'running'))

    def test_missing_data_with_valid_configuration(self):
        self.data.rename(self.directory / 'retained-test-data')
        with self.server('absent') as (_, _, start):
            result = self.run_restore('absent', False)
        self.assertIsNone(result.safety_backup)
        start.assert_not_called()
        self.assertEqual(self.save.read_bytes(), b'earlier world')
        self.assertEqual(len(backup.list_backups(self.directory)), 1)

    def test_changed_user_settings_remain_active(self):
        path = self.directory / 'compose.yaml'
        config = yaml.safe_load(path.read_text(encoding='utf-8'))
        config['services']['server']['environment']['SERVER_NAME'] = 'current name'
        path.write_text(yaml.safe_dump(config), encoding='utf-8')
        before = path.read_bytes()
        with self.server():
            self.run_restore()
        self.assertEqual(path.read_bytes(), before)

    def test_incompatible_pack_and_invalid_archived_configuration(self):
        for filename in ('pack.yaml', 'compose.yaml', 'instance.yaml'):
            with self.subTest(filename=filename):
                path = self.directory / filename
                original = path.read_bytes()
                document = yaml.safe_load(original)
                if filename == 'pack.yaml':
                    document['name'] = 'different definition same version'
                elif filename == 'compose.yaml':
                    document['services']['server']['volumes'].append('/:/unsafe')
                else:
                    document['instance'] = 'wrong-instance'
                path.write_text(yaml.safe_dump(document))
                bad = backup.create(self.directory, 'friends', self.pack, 'exited')
                path.write_bytes(original)
                with self.server('running') as (_, cmd, start), self.assertRaisesRegex(GameStackError, 'Archived configuration'):
                    self.runtime.restore('friends', bad.stem, expected_state='running', expected_data=True)
                cmd.assert_not_called()
                start.assert_not_called()
                self.assert_current()

    def test_corrupt_archive_rejected_before_stop(self):
        self.archive.write_bytes(b'not a tar')
        with self.server('running') as (_, cmd, start), self.assertRaisesRegex(GameStackError, 'verification failed'):
            self.run_restore('running')
        cmd.assert_not_called()
        start.assert_not_called()
        self.assertEqual(self.save.read_bytes(), b'later world')

    def test_archive_changed_after_verification_is_rejected(self):
        real = backup.verify
        def change(*args):
            result = real(*args)
            self.archive.write_bytes(self.archive_bytes)
            return result
        with self.server('running') as (_, cmd, _), patch.object(backup, 'verify', side_effect=change), \
                self.assertRaisesRegex(GameStackError, 'changed'):
            self.run_restore('running')
        cmd.assert_not_called()
        self.assert_current()

    def test_space_failure_before_stop(self):
        with self.server('running') as (_, cmd, _), patch.object(backup.shutil, 'disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaisesRegex(GameStackError, 'bytes required'):
                self.run_restore('running')
        cmd.assert_not_called()
        self.assert_current()

    def test_restore_space_includes_staging_and_shared_archive_estimate(self):
        manifest = backup.verify(self.archive, 'friends')
        staged = sum(entry['size'] + 4096 for entry in manifest['entries']
                     if entry['path'] == 'data' or entry['path'].startswith('data/'))
        for present in (False, True):
            required = backup.RESERVE + staged
            if present:
                required += backup.archive_size(backup.inventory(self.directory))
            for free in (required - 1, required):
                with self.subTest(present=present, free=free), patch.object(backup.shutil, 'disk_usage') as usage:
                    usage.return_value.free = free
                    if free < required:
                        with self.assertRaisesRegex(GameStackError, 'bytes required'):
                            restore.check_space(self.directory, manifest, present)
                    else:
                        restore.check_space(self.directory, manifest, present)

    def test_safety_failure_resumes_untouched_original(self):
        with self.server('running') as (_, _, start), patch.object(backup, 'create', side_effect=OSError('secret-test')), \
                self.assertRaisesRegex(GameStackError, 'Original server restarted and healthy') as caught:
            self.run_restore('running')
        start.assert_called_once()
        self.assertNotIn('secret-test', str(caught.exception))
        self.assert_current()
        self.assertFalse((self.directory / restore.MARKER).exists())

    def test_failed_stop_never_replaces_or_restarts(self):
        with self.server('running', stop_error=GameStackError('stop failed')) as (_, _, start), \
                self.assertRaisesRegex(GameStackError, 'stop failed'):
            self.run_restore('running')
        start.assert_not_called()
        self.assert_current()
        self.assertEqual(len(backup.list_backups(self.directory)), 1)

    def test_failed_container_removal_retains_marker_and_snapshot(self):
        with self.server(remove_error=OSError('secret-test')), self.assertRaises(GameStackError):
            self.run_restore()
        self.assert_current()
        self.assertEqual(len(backup.list_backups(self.directory)), 2)
        self.assertEqual(json.loads((self.directory / restore.MARKER).read_text())['phase'], 'remove-container')

    def test_failed_health_attempts_stop_and_keeps_recovery(self):
        with self.server('running', start_error=GameStackError('secret-test')) as (state, _, _), \
                self.assertRaisesRegex(GameStackError, 'Server stop confirmed') as caught:
            self.run_restore('running')
        self.assertEqual(state[0], 'exited')
        self.assertNotIn('secret-test', str(caught.exception))
        self.assertEqual(self.save.read_bytes(), b'earlier world')
        marker = json.loads((self.directory / restore.MARKER).read_text())
        backup.verify(Path(marker['safety_backup']), 'friends')
        self.assertIn('After reconciling', str(caught.exception))

    def test_failed_health_stop_reports_uncertain_state(self):
        with self.server('running', start_error=GameStackError('health')) as (_, cmd, _):
            original = cmd.side_effect
            stops = [0]
            def fail_second_stop(args, *other):
                if 'stop' in args:
                    stops[0] += 1
                    if stops[0] == 2:
                        raise OSError('secret-test')
                return original(args, *other)
            cmd.side_effect = fail_second_stop
            with self.assertRaisesRegex(GameStackError, 'stop NOT confirmed'):
                self.run_restore('running')

    def test_interruptions_before_and_after_each_rename_keep_copies(self):
        original = Path.rename
        for boundary in (1, 2):
            for after in (False, True):
                with self.subTest(boundary=boundary, after=after):
                    # Separate fixture for each interrupted transaction.
                    case = RestoreTests()
                    case.setUp()
                    try:
                        count = [0]
                        def interrupt(path, destination):
                            count[0] += 1
                            if count[0] == boundary and not after:
                                raise KeyboardInterrupt()
                            result = original(path, destination)
                            if count[0] == boundary and after:
                                raise KeyboardInterrupt()
                            return result
                        with case.server('running') as (_, _, start), patch.object(Path, 'rename', interrupt), self.assertRaises(KeyboardInterrupt):
                            case.run_restore('running')
                        start.assert_not_called()
                        marker = json.loads((case.directory / restore.MARKER).read_text())
                        backup.verify(Path(marker['safety_backup']), 'friends')
                        self.assertEqual(case.archive.read_bytes(), case.archive_bytes)
                        work = Path(marker['work_directory'])
                        copies = [p for p in (case.data, work / 'previous-data') if p.exists()]
                        self.assertTrue(any((p / 'nested ü/save file').read_bytes() == b'later world' for p in copies))
                    finally:
                        case.doCleanups()

    def test_interrupt_during_snapshot_does_not_restart(self):
        with self.server('running') as (_, _, start), patch.object(backup, 'create', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_restore('running')
        start.assert_not_called()
        self.assert_current()

    def test_marker_blocks_mutations_but_not_offline_backup_access_or_status(self):
        restore.write_marker(self.directory, {'phase': 'install-staged'})
        operations = [lambda: self.runtime.lifecycle('start', 'friends'), lambda: self.runtime.lifecycle('restart', 'friends'),
                      lambda: self.runtime.backup('friends'), lambda: self.runtime.remove('friends'), self.run_restore]
        for operation in operations:
            with self.subTest(operation=operation), patch.object(self.runtime, 'doctor') as doctor, self.assertRaisesRegex(GameStackError, 'unfinished restore'):
                operation()
            doctor.assert_not_called()
        self.assertEqual(backup.list_backups(self.directory)[0].path, self.archive)
        backup.verify(self.archive, 'friends')
        self.data.rename(self.directory / 'retained-data')
        with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'command', return_value=''):
            self.assertEqual(self.runtime.lifecycle('status', 'friends'), 'not created')
            self.assertEqual(self.runtime.lifecycle('stop', 'friends'), 'stopped')

    def test_lock_and_changed_confirmation_fail_without_mutation(self):
        with self.runtime.lock(self.directory), self.assertRaisesRegex(GameStackError, 'lock'):
            self.run_restore()
        with self.server('running') as (_, cmd, _), self.assertRaisesRegex(GameStackError, 'since confirmation'):
            self.run_restore()
        cmd.assert_not_called()
        self.assert_current()

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership and symlinks')
    def test_links_mounts_and_foreign_account_fail_before_stop(self):
        link = self.data / 'escape'
        link.symlink_to(self.directory)
        with self.server('running') as (_, cmd, _), self.assertRaises(GameStackError):
            self.run_restore('running')
        cmd.assert_not_called()
        link.unlink()
        with self.server('running') as (_, cmd, _), patch('gamestack.restore.os.path.ismount', return_value=True), self.assertRaisesRegex(GameStackError, 'nested mounts'):
            self.run_restore('running')
        cmd.assert_not_called()
        with self.server('running') as (_, cmd, _), patch.object(os, 'getuid', return_value=os.getuid() + 1), self.assertRaisesRegex(GameStackError, 'operating account'):
            self.run_restore('running')
        cmd.assert_not_called()

    def test_inaccessible_data_is_not_missing(self):
        original = Path.lstat
        def inaccessible(path, *args, **kwargs):
            if path == self.data:
                raise PermissionError('secret-test')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', inaccessible), self.assertRaises(PermissionError):
            restore.data_exists(self.directory)

    def test_crash_state_is_restore_only(self):
        state = {'Status': 'exited', 'Running': False, 'OOMKilled': True, 'ExitCode': 137,
                 'Paused': False, 'Restarting': False, 'Dead': False}
        with patch.object(self.runtime, 'command', side_effect=['a' * 64, json.dumps(state)]):
            self.assertEqual(self.runtime.restore_state(self.directory), 'crashed')
        with patch.object(self.runtime, 'command', side_effect=['a' * 64, json.dumps(state)]), self.assertRaises(GameStackError):
            self.runtime.backup_state(self.directory)
        for field in ('Paused', 'Restarting', 'Dead', 'Running'):
            broken = {**state, field: True}
            with patch.object(self.runtime, 'command', side_effect=['a' * 64, json.dumps(broken)]), self.assertRaises(GameStackError):
                self.runtime.restore_state(self.directory)

    def test_malicious_archive_members_never_extract(self):
        with tarfile.open(self.archive) as source:
            members = [(m, source.extractfile(m).read() if m.isfile() else None) for m in source]
        for mutation in ('traversal', 'absolute', 'symlink', 'hardlink', 'device', 'duplicate'):
            with self.subTest(mutation=mutation):
                modified = copy.deepcopy(members)
                member = modified[0][0]
                if mutation == 'traversal':
                    member.name = '../outside'
                elif mutation == 'absolute':
                    member.name = '/outside'
                elif mutation == 'symlink':
                    member.type = tarfile.SYMTYPE
                    member.linkname = '../outside'
                elif mutation == 'hardlink':
                    member.type = tarfile.LNKTYPE
                    member.linkname = '../outside'
                elif mutation == 'device':
                    member.type = tarfile.CHRTYPE
                else:
                    modified.insert(1, modified[0])
                with tarfile.open(self.archive, 'w') as destination:
                    for item, payload in modified:
                        destination.addfile(item, io.BytesIO(payload) if payload is not None else None)
                with self.server('running') as (_, cmd, _), self.assertRaises(GameStackError):
                    self.run_restore('running')
                cmd.assert_not_called()
                self.assertFalse((self.directory / 'outside').exists())
                self.assertEqual(self.save.read_bytes(), b'later world')

    @unittest.skipUnless(os.name == 'posix', 'POSIX special permissions')
    def test_special_permissions_are_not_restored(self):
        os.chmod(self.save, 0o4640)
        archive = backup.create(self.directory, 'friends', self.pack, 'exited')
        os.chmod(self.save, 0o640)
        with self.server('running') as (_, cmd, _), self.assertRaisesRegex(GameStackError, 'special permission'):
            self.runtime.restore('friends', archive.stem, expected_state='running', expected_data=True)
        cmd.assert_not_called()
        self.assert_current()

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink substitution')
    def test_staging_rejects_replaced_parent_directory(self):
        outside = self.directory.parent / 'outside'
        outside.mkdir()
        mkdir = Path.mkdir
        def substitute(path, *args, **kwargs):
            mkdir(path, *args, **kwargs)
            if path.name == 'nested ü' and any(p.name.startswith('.restore-') for p in path.parents):
                path.rmdir()
                path.symlink_to(outside, target_is_directory=True)
        with self.server('running') as (_, command, _), patch.object(Path, 'mkdir', autospec=True, side_effect=substitute):
            with self.assertRaisesRegex(GameStackError, 'unsafe'):
                self.run_restore('running')
        command.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_current()

    def test_staging_write_failure_preserves_live_world(self):
        original = os.open
        def fail_write(path, flags, *args, **kwargs):
            if Path(path).name == 'save file' and flags & os.O_WRONLY:
                raise OSError('secret-test')
            return original(path, flags, *args, **kwargs)
        with self.server('running') as (_, cmd, _), patch.object(os, 'open', side_effect=fail_write), self.assertRaises(GameStackError) as caught:
            self.run_restore('running')
        self.assertNotIn('secret-test', str(caught.exception))
        cmd.assert_not_called()
        self.assert_current()
        self.assertTrue(list(self.directory.glob('.restore-*')))

    def test_unrepresentable_archived_timestamp_fails_safely(self):
        with self.server('running') as (_, cmd, _), patch.object(os, 'utime', side_effect=OverflowError('secret-test')), \
                self.assertRaises(GameStackError) as caught:
            self.run_restore('running')
        self.assertNotIn('secret-test', str(caught.exception))
        cmd.assert_not_called()
        self.assert_current()

    def test_state_change_after_staging_prevents_stop(self):
        with self.server('running') as (_, cmd, _), patch.object(self.runtime, 'restore_state', side_effect=['running', 'crashed']), \
                self.assertRaisesRegex(GameStackError, 'state changed'):
            self.run_restore('running')
        cmd.assert_not_called()
        self.assert_current()

    def test_unverified_shutdown_blocks_snapshot_and_restart(self):
        with self.server('running') as (_, _, start), patch.object(self.runtime, 'backup_state', return_value='crashed'), \
                patch.object(backup, 'create') as snapshot, self.assertRaisesRegex(GameStackError, 'shutdown could not be verified'):
            self.run_restore('running')
        start.assert_not_called()
        snapshot.assert_not_called()
        self.assert_current()

    def test_marker_publication_failure_prevents_replacement(self):
        with self.server() as (_, cmd, _), patch.object(restore, 'write_marker', side_effect=OSError('secret-test')), \
                self.assertRaises(GameStackError):
            self.run_restore()
        cmd.assert_not_called()
        self.assert_current()
        self.assertEqual(len(backup.list_backups(self.directory)), 2)

    def test_failed_rename_retains_recovery_and_nonzero_result(self):
        with self.server('running') as (_, _, start), patch.object(Path, 'rename', side_effect=OSError('secret-test')), \
                self.assertRaises(GameStackError) as caught:
            self.run_restore('running')
        self.assertNotIn('secret-test', str(caught.exception))
        start.assert_not_called()
        self.assert_current()
        self.assertTrue((self.directory / restore.MARKER).exists())
        self.assertEqual(len(backup.list_backups(self.directory)), 2)

    def test_existing_destination_is_never_replaced(self):
        destination = self.directory / 'unexpected'
        destination.mkdir()
        with self.assertRaisesRegex(GameStackError, 'unexpectedly exists'):
            restore.move_directory(self.data, destination)
        self.assert_current()
        self.assertTrue(destination.is_dir())

    def test_marker_clear_failure_remains_guarded(self):
        with self.server(), patch.object(restore, 'clear_marker', side_effect=OSError('secret-test')), self.assertRaises(GameStackError):
            self.run_restore()
        self.assertEqual(self.save.read_bytes(), b'earlier world')
        self.assertEqual(json.loads((self.directory / restore.MARKER).read_text())['phase'], 'complete')
        with self.assertRaises(GameStackError):
            restore.require_no_transaction(self.directory)

    def test_removed_instance_cannot_be_restored(self):
        (self.directory / 'removed.yaml').write_text('instance: friends\n')
        with self.assertRaisesRegex(GameStackError, 'removed'):
            self.run_restore()
        self.assert_current()

    def cli(self, args, answers=(), interactive=True, missing=False, failure=None):
        output = io.StringIO()
        result = restore.RestoreResult(self.archive.stem, None, self.directory / '.restore-test', 'stopped (health not tested)')
        with patch('sys.stdin.isatty', return_value=interactive), patch('builtins.input', side_effect=answers), \
                patch.object(Runtime, 'doctor'), patch.object(Runtime, 'restore_state', return_value='exited'), \
                patch.object(Runtime, 'restore', return_value=result, side_effect=failure) as run, \
                patch.object(restore, 'data_exists', return_value=not missing), redirect_stdout(output), redirect_stderr(output):
            code = main(['--root', str(self.runtime.root), 'restore', 'friends', *args])
        return code, output.getvalue(), run

    def test_cli_picker_retries_and_confirms(self):
        code, output, run = self.cli([], ['bad', '0', '1', 'yes'])
        self.assertEqual(code, 0)
        run.assert_called_once_with('friends', self.archive.stem, expected_state='exited', expected_data=True)
        self.assertIn('Enter a number', output)
        self.assertIn('Restored backup:', output)

    def test_cli_cancellation_changes_nothing(self):
        for args, answers in (([], ['']), ([], ['1', 'n']), ([self.archive.stem], ['no'])):
            code, output, run = self.cli(args, answers)
            self.assertEqual(code, 0)
            run.assert_not_called()
            self.assertIn('cancelled', output)
        self.assert_current()

    def test_cli_noninteractive_requires_id_and_yes(self):
        for args in ([], ['--yes'], [self.archive.stem]):
            code, _, run = self.cli(args, interactive=False)
            self.assertEqual(code, 1)
            run.assert_not_called()
        code, output, run = self.cli([self.archive.stem, '--yes'], interactive=False, missing=True)
        self.assertEqual(code, 0)
        self.assertIn('No current world can be backed up', output)
        run.assert_called_once()

    def test_cli_empty_list_invalid_id_and_interrupt(self):
        with patch.object(backup, 'list_backups', return_value=[]):
            code, output, run = self.cli([])
        self.assertEqual(code, 0)
        self.assertIn('No completed backups', output)
        run.assert_not_called()
        for invalid in ('../escape', 'latest', str(self.archive)):
            code, _, run = self.cli([invalid, '--yes'])
            self.assertEqual(code, 1)
            run.assert_not_called()
        code, output, _ = self.cli([self.archive.stem, '--yes'], failure=KeyboardInterrupt())
        self.assertEqual(code, 130)
        self.assertNotIn('Restored backup:', output)
