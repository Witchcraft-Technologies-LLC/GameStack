"""Recovery invariants using private synthetic worlds, never real servers."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gamestack import backup
from gamestack.cli import main
from gamestack.pack import GameStackError, load_pack
from gamestack.runtime import Runtime

PACK = Path(__file__).resolve().parents[1] / 'packs/example/pack.yaml'


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Runtime(Path(self.temp.name).resolve() / 'world storage ü')
        self.pack = load_pack(PACK)
        self.directory = self.runtime.prepare(self.pack, 'friends', {'SERVER_NAME': 'test', 'SERVER_PASSWORD': 'secret-test'})
        (self.directory / 'data/nested ü').mkdir()
        (self.directory / 'data/nested ü/save file').write_bytes(b'world' * 1000)
        self.before = self.snapshot()

    def snapshot(self):
        return {str(p.relative_to(self.directory)): p.read_bytes() for p in self.directory.rglob('*')
                if p.is_file() and 'backups' not in p.relative_to(self.directory).parts and p.name != '.operation.lock'}

    def create(self):
        return backup.create(self.directory, 'friends', self.pack, 'exited')

    def run_backup(self, states=('running', 'exited'), **kwargs):
        with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'backup_state', side_effect=states), \
                patch.object(self.runtime, 'command') as command, patch.object(self.runtime, 'start_server', **kwargs) as start:
            result = self.runtime.backup('friends')
            return result, command, start

    def test_publication_sync_failures_preserve_verified_copies(self):
        for fail_at in (1, 2, 3):
            with self.subTest(fail_at=fail_at):
                calls = []
                def sync(directory):
                    calls.append(directory)
                    if len(calls) == fail_at:
                        raise OSError('synthetic disk failure')
                previous = {p.name for p in backup.folder(self.directory).glob('*')}
                with patch('gamestack.backup.sync_directory', side_effect=sync):
                    with self.assertRaisesRegex(GameStackError, 'synced to disk'):
                        self.create()
                created = [p for p in backup.folder(self.directory).iterdir() if p.name not in previous]
                self.assertEqual(len(created), 2 if fail_at < 3 else 1)
                for path in created:
                    backup.verify(path, 'friends')
                self.assertEqual(self.snapshot(), self.before)

    def test_archive_space_estimate_boundary(self):
        required = backup.RESERVE + backup.archive_size(backup.inventory(self.directory))
        for free in (required - 1, required):
            with patch('gamestack.backup.shutil.disk_usage') as usage:
                usage.return_value.free = free
                if free < required:
                    with self.assertRaisesRegex(GameStackError, 'bytes required'):
                        backup.preflight(self.directory)
                else:
                    backup.preflight(self.directory)

    @unittest.skipUnless(os.name == 'posix', 'Deep paths require host long-path support')
    def test_inventory_deep_tree_does_not_use_python_recursion(self):
        path = self.directory / 'data'
        for _ in range(100):
            path /= 'd'
            path.mkdir()
        (path / 'save').write_bytes(b'deep world')
        limit = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(80)
            entries = backup.inventory(self.directory)
        finally:
            sys.setrecursionlimit(limit)
        names = [entry[0] for entry in entries]
        self.assertEqual(names, sorted(names))
        self.assertIn('data/' + 'd/' * 100 + 'save', names)

    def test_archive_roundtrip_contents_and_private_permissions(self):
        path = self.create()
        manifest = backup.verify(path, 'friends')
        names = [r['path'] for r in manifest['entries']]
        self.assertEqual(names, sorted(names))
        self.assertEqual(set(names), {'data', 'data/nested ü', 'data/nested ü/save file',
                         'configuration/instance.yaml', 'configuration/pack.yaml', 'configuration/compose.yaml'})
        with tarfile.open(path) as archive:
            self.assertEqual(archive.extractfile('data/nested ü/save file').read(), b'world' * 1000)
        self.assertEqual(self.snapshot(), self.before)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        second = self.create()
        self.assertNotEqual(path, second)
        self.assertEqual([r.path for r in backup.list_backups(self.directory)], [second, path])

    def test_running_and_stopped_flows(self):
        (_, state), command, start = self.run_backup()
        self.assertEqual(state, 'healthy')
        self.assertIn('stop', command.call_args.args[0])
        start.assert_called_once()
        for initial in ('exited', 'created', 'absent'):
            with self.subTest(initial=initial):
                (_, state), command, start = self.run_backup((initial,))
                self.assertEqual(state, 'stopped')
                command.assert_not_called()
                start.assert_not_called()
        self.assertEqual(self.snapshot(), self.before)

    def test_failed_capture_restarts_and_preserves_all_copies(self):
        old = self.create().read_bytes()
        for failure in (OSError('secret-test'), GameStackError('Capture failed')):
            with patch('gamestack.backup.create', side_effect=failure), \
                    patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'backup_state', side_effect=['running', 'exited']), \
                    patch.object(self.runtime, 'command'), patch.object(self.runtime, 'start_server') as start:
                with self.assertRaisesRegex(GameStackError, 'Server restarted and healthy') as caught:
                    self.runtime.backup('friends')
                self.assertNotIn('secret-test', str(caught.exception))
                start.assert_called_once()
        self.assertEqual(backup.list_backups(self.directory)[0].path.read_bytes(), old)
        self.assertEqual(self.snapshot(), self.before)

    def test_restart_failure_retains_verified_backup(self):
        with self.assertRaisesRegex(GameStackError, 'Verified backup retained'):
            self.run_backup(side_effect=GameStackError('secret-test'))
        records = backup.list_backups(self.directory)
        self.assertEqual(len(records), 1)
        backup.verify(records[0].path, 'friends')
        with patch('gamestack.backup.create', side_effect=OSError('secret-test')):
            with self.assertRaisesRegex(GameStackError, 'Backup failed;.*restart'):
                self.run_backup(side_effect=GameStackError('secret-test'))

    def test_unverified_stop_and_interrupt_do_not_restart(self):
        for problem in ('absent', GameStackError('bad shutdown')):
            with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'backup_state', side_effect=['running', problem]), \
                    patch.object(self.runtime, 'command'), patch.object(self.runtime, 'start_server') as start:
                with self.assertRaises(GameStackError):
                    self.runtime.backup('friends')
                start.assert_not_called()
        with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'backup_state', return_value='running'), \
                patch.object(self.runtime, 'command', side_effect=GameStackError('stop failed')), patch.object(self.runtime, 'start_server') as start:
            with self.assertRaises(GameStackError):
                self.runtime.backup('friends')
            start.assert_not_called()
        with patch('gamestack.backup.create', side_effect=KeyboardInterrupt), \
                patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'backup_state', side_effect=['running', 'exited']), \
                patch.object(self.runtime, 'command'), patch.object(self.runtime, 'start_server') as start:
            with self.assertRaises(KeyboardInterrupt):
                self.runtime.backup('friends')
            start.assert_not_called()
        self.assertEqual(backup.list_backups(self.directory), [])

    def test_lock_and_space_fail_before_stop(self):
        with self.runtime.lock(self.directory), patch.object(self.runtime, 'doctor') as doctor:
            with self.assertRaises(GameStackError):
                self.runtime.backup('friends')
            doctor.assert_not_called()
        with patch('gamestack.backup.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'backup_state', return_value='running'), \
                    patch.object(self.runtime, 'command') as command:
                with self.assertRaisesRegex(GameStackError, 'bytes required'):
                    self.runtime.backup('friends')
                command.assert_not_called()
        real = backup.preflight
        with patch('gamestack.backup.preflight', side_effect=[real(self.directory), GameStackError('space exhausted')]):
            with self.assertRaisesRegex(GameStackError, 'Server restarted and healthy'):
                self.run_backup()

    def test_partial_and_publish_failures_preserve_source_and_old_backup(self):
        old = self.create()
        contents = old.read_bytes()
        for target in ('gamestack.backup.verify', 'gamestack.backup.os.link', 'gamestack.backup.tarfile.TarFile.addfile'):
            with self.subTest(target=target), patch(target, side_effect=OSError('secret-test')):
                with self.assertRaises(OSError):
                    self.create()
            self.assertEqual(old.read_bytes(), contents)
            self.assertEqual(self.snapshot(), self.before)
        with self.assertLogs('gamestack.backup', level='WARNING'):
            self.assertEqual(len(backup.list_backups(self.directory)), 1)
        self.assertEqual(len(list(old.parent.glob('*.partial'))), 3)

    def test_collision_never_overwrites(self):
        path = self.create()
        data = path.read_bytes()
        fixed_id = path.stem
        from datetime import datetime, timezone
        timestamp = datetime.strptime(fixed_id.split('-')[0], '%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
        with patch('gamestack.backup.datetime', wraps=datetime) as clock, patch('gamestack.backup.uuid.uuid4') as unique:
            clock.now.return_value = timestamp
            unique.return_value.hex = fixed_id.split('-')[1]
            with self.assertRaises(FileExistsError):
                self.create()
        self.assertEqual(path.read_bytes(), data)

    def test_links_and_special_files_rejected(self):
        link = self.directory / 'data/link'
        try:
            link.symlink_to(self.directory / 'instance.yaml')
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaises(GameStackError):
            self.create()
        link.unlink()
        (self.directory / 'backups').symlink_to(self.directory / 'data', target_is_directory=True)
        with self.assertRaises(GameStackError):
            backup.list_backups(self.directory)
        (self.directory / 'backups').unlink()
        if hasattr(os, 'mkfifo'):
            os.mkfifo(link)
            with self.assertRaises(GameStackError):
                self.create()
            link.unlink()

    def test_unwritable_capture_and_invalid_destination(self):
        destination = self.directory / 'backups'
        destination.write_bytes(b'preserve')
        with patch.object(self.runtime, 'doctor'), patch.object(self.runtime, 'backup_state', return_value='running'), \
                patch.object(self.runtime, 'command') as command:
            with self.assertRaisesRegex(GameStackError, 'not a directory'):
                self.runtime.backup('friends')
            command.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'preserve')
        destination.unlink()
        destination.mkdir(mode=0o700)
        if os.name == 'posix':
            destination.chmod(0o755)
            with self.assertRaisesRegex(GameStackError, 'not private'):
                backup.preflight(self.directory)
            destination.chmod(0o700)
        real_open = os.open
        def deny_archive(path, flags, *args, **kwargs):
            if str(path).endswith('.partial'):
                raise PermissionError('secret-test')
            return real_open(path, flags, *args, **kwargs)
        with patch('gamestack.backup.os.open', side_effect=deny_archive):
            with self.assertRaisesRegex(GameStackError, 'Server restarted and healthy'):
                self.run_backup()
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(backup.list_backups(self.directory), [])

    def test_partial_unlink_failure_keeps_completed_backup(self):
        real_unlink = Path.unlink
        def deny_partial(path, *args, **kwargs):
            if str(path).endswith('.partial'):
                raise PermissionError('secret-test')
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', deny_partial), self.assertLogs('gamestack.backup', level='WARNING'):
            path = self.create()
        backup.verify(path, 'friends')
        self.assertEqual(path.read_bytes(), path.with_suffix('.tar.partial').read_bytes())

    def test_windows_signature_ignores_unstable_identity_metadata(self):
        first = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=5, st_mtime_ns=7,
                                st_dev=1, st_ino=2, st_ctime_ns=3)
        second = SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_size=5, st_mtime_ns=7,
                                 st_dev=4, st_ino=5, st_ctime_ns=6)
        with patch.object(backup.os, 'name', 'nt'):
            self.assertEqual(backup.signature(first), backup.signature(second))
            second.st_size += 1
            self.assertNotEqual(backup.signature(first), backup.signature(second))
            second.st_size -= 1
            second.st_mtime_ns += 1
            self.assertNotEqual(backup.signature(first), backup.signature(second))

    def test_source_change_detected(self):
        original = backup.HashReader.read
        def change(reader, size=-1):
            data = original(reader, size)
            if data.startswith(b'world'):
                with (self.directory / 'data/nested ü/save file').open('ab') as stream:
                    stream.write(b'changed')
            return data
        with patch.object(backup.HashReader, 'read', change):
            with self.assertRaisesRegex(GameStackError, 'source changed'):
                self.create()
        self.assertEqual(backup.list_backups(self.directory), [])

    def test_offline_discovery_retired_instance_and_cli_names(self):
        path = self.create()
        (self.directory / 'removed.yaml').write_text('removed: true')
        (self.directory / 'data').rename(self.directory / 'retained-data')
        output = io.StringIO()
        with patch.object(Runtime, 'command', side_effect=AssertionError('Docker contacted')), contextlib.redirect_stdout(output):
            for args in (['list', 'friends'], ['verify', 'friends', path.stem]):
                self.assertEqual(main(['--root', str(self.runtime.root), 'backup', *args]), 0)
        self.assertIn('does not recheck integrity', output.getvalue())
        for instance in ('list', 'verify'):
            with patch.object(Runtime, 'backup', return_value=(path, 'stopped')) as create, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['backup', instance]), 0)
                create.assert_called_once_with(instance)
        for bad in ('../x', str(path), 'abc'):
            with self.assertRaises(GameStackError):
                backup.selected(self.directory, bad)

    def test_empty_listing_and_empty_data(self):
        empty = self.runtime.prepare(self.pack, 'empty', {'SERVER_NAME': 'test', 'SERVER_PASSWORD': 'secret-test'})
        self.assertEqual(backup.list_backups(empty), [])
        self.assertFalse((empty / 'backups').exists())
        backup.verify(backup.create(empty, 'empty', self.pack, 'absent'), 'empty')

    def test_authoritative_state_validation(self):
        base = dict(Status='running', Running=True, Paused=False, Restarting=False, Dead=False, OOMKilled=False, ExitCode=0)
        with patch.object(self.runtime, 'command', return_value=''):
            self.assertEqual(self.runtime.backup_state(self.directory), 'absent')
        for status in ('running', 'exited', 'created'):
            state = dict(base, Status=status, Running=status == 'running')
            with patch.object(self.runtime, 'command', side_effect=['a' * 64, json.dumps(state)]):
                self.assertEqual(self.runtime.backup_state(self.directory), status)
        for changes in ({'OOMKilled': True}, {'ExitCode': 137}, {'ExitCode': False}, {'Status': 'paused'}, {'Running': False}, {'Dead': True}, {'Status': 'restarting'}, {'Status': 'dead'}, {'Status': 'unknown'}, {'Paused': True}, {'Restarting': True}):
            with patch.object(self.runtime, 'command', side_effect=['a' * 64, json.dumps(dict(base, **changes))]):
                with self.assertRaises(GameStackError):
                    self.runtime.backup_state(self.directory)
        for raw in ('bad-id', 'a' * 64 + '\n' + 'b' * 64):
            with patch.object(self.runtime, 'command', return_value=raw):
                with self.assertRaises(GameStackError):
                    self.runtime.backup_state(self.directory)

    def test_corrupt_archives_and_manifest(self):
        path = self.create()
        original = path.read_bytes()
        with self.assertRaises(GameStackError):
            backup.verify(path, 'another')
        for data in (original[:100], original[:-10240], original.replace(b'world', b'WRONG', 1), original + b'x' * 512):
            path.write_bytes(data)
            with self.assertRaises(GameStackError):
                backup.verify(path, 'friends')
        path.write_bytes(original)
        with tarfile.open(path) as source:
            members = [(m, source.extractfile(m).read() if m.isfile() else None) for m in source]
        for mutation in ('traversal', 'duplicate', 'link', 'manifest', 'missing', 'oversized', 'extra'):
            with self.subTest(mutation=mutation):
                import copy
                modified = copy.deepcopy(members)
                if mutation == 'traversal':
                    modified[0][0].name = '../secret-test'
                elif mutation == 'duplicate':
                    modified.insert(1, modified[0])
                elif mutation == 'link':
                    modified[0][0].type = tarfile.SYMTYPE
                elif mutation == 'manifest':
                    member, data = modified[-1]
                    data = data.replace(b'"schema_version": 1', b'"schema_version": 2')
                    member.size = len(data)
                    modified[-1] = member, data
                elif mutation == 'missing':
                    modified.pop(0)
                elif mutation == 'oversized':
                    member, _ = modified[-1]
                    data = b' ' * (backup.MANIFEST_LIMIT + 1)
                    member.size = len(data)
                    modified[-1] = member, data
                else:
                    modified.append(modified[0])
                with tarfile.open(path, 'w') as destination:
                    for member, data in modified:
                        destination.addfile(member, io.BytesIO(data) if data is not None else None)
                with self.assertRaises(GameStackError) as caught:
                    backup.verify(path, 'friends')
                self.assertNotIn('secret-test', str(caught.exception))

    def test_cli_failure_redaction_and_interruption(self):
        for error, code in ((OSError('secret-test'), 1), (KeyboardInterrupt(), 130)):
            output = io.StringIO()
            with patch.object(Runtime, 'backup', side_effect=error), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(main(['--debug', 'backup', 'friends']), code)
            self.assertNotIn('secret-test', output.getvalue())
            self.assertNotIn('Verified backup:', output.getvalue())
