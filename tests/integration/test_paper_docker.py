"""Opt-in Paper smoke test; never accepts the Minecraft EULA implicitly."""
import hashlib
import json
import os
import platform
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from gamestack.backup import verify
from gamestack.cli import configure
from gamestack.pack import GameStackError, load_pack
from gamestack.runtime import Runtime
from unittest.mock import patch

PACK = Path(__file__).resolve().parents[2] / 'packs/minecraft-paper/pack.yaml'
ENABLED = (os.environ.get('GAMESTACK_PAPER_TEST') == '1'
           and os.environ.get('GAMESTACK_MINECRAFT_EULA') == 'TRUE'
           and bool(os.environ.get('GAMESTACK_PAPER_OWNER')))


def shutdown_markers(logs):
    """Extract fixed save markers without retaining player chat or private logs."""
    markers = ('Stopping server', 'Saving players', 'Saving worlds', 'All dimensions are saved')
    # Paper's console and file layouts differ. Require a logger prefix and an
    # exact server message below; player chat cannot supply save evidence.
    lines = [match.group(1) for line in logs.splitlines()
             if (match := re.fullmatch(
                 r'\[\d{2}:\d{2}:\d{2}(?: INFO\]|\] \[Server thread/INFO\]): (.*)', line))]
    return [marker for marker in markers
            if any(line == marker or (marker == 'All dimensions are saved' and
                   line == 'ThreadedAnvilChunkStorage: All dimensions are saved')
                   for line in lines)]


@unittest.skipUnless(ENABLED, 'Requires explicit Paper test opt-in, EULA acceptance, and a real Java owner name')
class PaperIntegration(unittest.TestCase):
    def test_pinned_lifecycle_and_recreation(self):
        # Retain the disposable world even on failure; no recursive test data deletion.
        root = Path(tempfile.mkdtemp(prefix='gamestack-paper-acceptance-')) / 'instances'
        print(f'Paper acceptance data retained at {root}', flush=True)
        runtime = Runtime(root)
        instance = 'paper-test-' + uuid.uuid4().hex[:10]
        pack = load_pack(PACK)
        owner = os.environ['GAMESTACK_PAPER_OWNER']
        with patch('sys.stdin.isatty', return_value=False):
            values = configure(pack, {'EULA': os.environ['GAMESTACK_MINECRAFT_EULA'],
                                      'OPS': owner, 'WHITELIST': owner})
        directory = runtime.prepare(pack, instance, values)
        base = runtime.compose_command(directory)
        def docker(*args):
            return subprocess.run(['docker', *args], check=True, capture_output=True,
                                  text=True, timeout=180).stdout.strip()
        def artifact_hashes():
            jars = list((directory / 'data').rglob('paper-26.2-121.jar'))
            self.assertTrue(jars, 'Pinned Paper artifact was not found')
            return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in jars}
        def file_hashes():
            result = {}
            for path in directory.rglob('*'):
                if path.is_file():
                    with path.open('rb') as stream:
                        result[str(path.relative_to(directory))] = hashlib.file_digest(stream, 'sha256').hexdigest()
            return result

        report = {
            'started_utc': datetime.now(timezone.utc).isoformat(),
            'result': 'incomplete', 'instance': instance,
            'os': platform.freedesktop_os_release(), 'architecture': platform.machine(),
            'python': platform.python_version(),
            'memory': [line for line in Path('/proc/meminfo').read_text().splitlines()
                       if line.startswith(('MemTotal:', 'MemAvailable:'))],
            'disk_free_bytes': shutil.disk_usage(root).free,
            'pack_sha256': hashlib.sha256(PACK.read_bytes()).hexdigest(),
            'lock_sha256': hashlib.sha256(PACK.with_name('upstream-lock.json').read_bytes()).hexdigest(),
            'image_reference': pack['image'], 'operations': [],
        }
        def lifecycle(action):
            started = time.monotonic()
            operation = {'action': action, 'result': 'failed'}
            report['operations'].append(operation)
            try:
                result = runtime.lifecycle(action, instance)
                operation['result'] = result
                return result
            finally:
                operation['seconds'] = round(time.monotonic() - started, 3)

        def stop_cleanly(container):
            since = datetime.now(timezone.utc).isoformat()
            self.assertEqual(lifecycle('stop'), 'stopped')
            state = json.loads(docker('inspect', '--format', '{{json .State}}', container))
            # Only persist known markers, never arbitrary logs/player chat or environment.
            logs = docker('logs', '--since', since, container)
            markers = shutdown_markers(logs)
            report.setdefault('shutdowns', []).append({
                'exit_code': state['ExitCode'], 'oom_killed': state['OOMKilled'],
                'markers': markers,
            })
            self.assertEqual(state['ExitCode'], 0)
            self.assertFalse(state['OOMKilled'])
            self.assertIn('All dimensions are saved', markers,
                          'Review private host logs: save completion was not established')

        try:
            report['docker'] = docker('version', '--format', '{{.Server.Version}}')
            report['compose'] = docker('compose', 'version', '--short')
            self.assertEqual(lifecycle('start'), 'healthy')
            first = runtime.command(base + ['ps', '--quiet', 'server']).strip()
            identity = docker('inspect', '--format', '{{.Config.User}}', first)
            self.assertEqual(identity, f'{os.getuid()}:{os.getgid()}')
            report['java'] = docker('exec', first, 'java', '--version')
            report['image_id'] = docker('inspect', '--format', '{{.Image}}', first)
            ports = json.loads(docker('inspect', '--format', '{{json .NetworkSettings.Ports}}', first))
            published = {port: bindings for port, bindings in ports.items() if bindings}
            report['published_ports'] = published
            self.assertEqual(published, {'25565/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '25565'}]})
            env = json.loads(docker('inspect', '--format', '{{json .Config.Env}}', first))
            for setting in ('ENABLE_RCON=FALSE', 'ENABLE_QUERY=FALSE', 'ENABLE_JMX=FALSE', 'ENABLE_SSH=FALSE'):
                self.assertTrue(setting in env, setting.split('=')[0] + ' must be disabled')
            properties = (directory / 'data/server.properties').read_text()
            for line in ['online-mode=true', 'white-list=true', 'enforce-whitelist=true',
                         'enable-rcon=false', 'enable-query=false']:
                self.assertIn(line, properties)
            self.assertTrue(list((directory / 'data').rglob('level.dat')))
            hashes = artifact_hashes()
            lock = json.loads(PACK.with_name('upstream-lock.json').read_text())
            self.assertIn(lock['paper']['sha256'], hashes.values())
            report['paper'] = lock['paper']
            report['artifact_hashes'] = hashes
            artifact, state = runtime.backup(instance)
            self.assertEqual(state, 'healthy')
            manifest = verify(artifact, instance)
            self.assertTrue(any(entry['path'].endswith('/level.dat') for entry in manifest['entries']))
            report['backup'] = {'id': artifact.stem, 'integrity': 'passed', 'restart': state}
            stop_cleanly(first)
            artifact, state = runtime.backup(instance)
            self.assertEqual(state, 'stopped')
            verify(artifact, instance)
            marker_file = directory / 'data/restore-acceptance.txt'
            marker_file.write_text('synthetic later state', encoding='utf-8')
            restored = runtime.restore(instance, artifact.stem, expected_state='exited', expected_data=True)
            self.assertEqual(restored.state, 'stopped (health not tested)')
            self.assertFalse(marker_file.exists())
            verify(restored.safety_backup, instance)
            runtime.restore(instance, restored.safety_backup.stem, expected_state='absent', expected_data=True)
            self.assertEqual(marker_file.read_text(), 'synthetic later state')
            lifecycle('start')
            running_restore = runtime.restore(instance, artifact.stem, expected_state='running', expected_data=True)
            self.assertEqual(running_restore.state, 'healthy')
            self.assertFalse(marker_file.exists())
            first = runtime.command(base + ['ps', '--quiet', 'server']).strip()
            report['restore'] = {'backup_id': artifact.stem, 'health': running_restore.state,
                                 'stopped_roundtrip': 'passed', 'safety_roundtrip': 'passed',
                                 'in_game_world_check': 'pending manual acceptance'}
            lifecycle('restart')
            self.assertEqual(artifact_hashes(), hashes)
            stop_cleanly(first)
            # Simulate saved in-game list edits using the actual persistent files,
            # while stopped. Player commands and real joins still need manual testing.
            for filename in ('whitelist.json', 'ops.json'):
                (directory / 'data' / filename).write_text('[]\n')
            runtime.command(base + ['rm', '--force', 'server'])
            lifecycle('start')
            second = runtime.command(base + ['ps', '--quiet', 'server']).strip()
            self.assertNotEqual(first, second)
            self.assertEqual(artifact_hashes(), hashes)
            for filename in ('whitelist.json', 'ops.json'):
                self.assertEqual(json.loads((directory / 'data' / filename).read_text()), [])
            stop_cleanly(second)
            before_removal = file_hashes()
            runtime.remove(instance)
            after_removal = file_hashes()
            self.assertEqual(set(after_removal) - set(before_removal), {'removed.yaml'})
            self.assertTrue(all(after_removal.get(path) == digest
                                for path, digest in before_removal.items()))
            self.assertTrue(list((directory / 'data').rglob('level.dat')))
            self.assertEqual(artifact_hashes(), hashes)
            retained = file_hashes()
            with self.assertRaises(GameStackError):
                runtime.prepare(pack, instance, values)
            self.assertEqual(retained, file_hashes())
            report['retired_name_rejected_without_changes'] = True
            report['result'] = 'passed'
        except Exception as exc:
            report['failure_type'] = type(exc).__name__
            raise
        finally:
            # Only this unique test project's container; retain all world/config files.
            try:
                runtime.command(base + ['down', '--timeout', '120'], timeout=180)
                report['cleanup'] = 'container removed; files retained'
            except GameStackError:
                report['cleanup'] = 'failed; inspect this test project on the host'
                report['result'] = 'incomplete'
                raise
            finally:
                evidence = root.parent / 'evidence.json'
                with evidence.open('x', encoding='utf-8') as stream:
                    os.chmod(evidence, 0o600)
                    json.dump(report, stream, indent=2)
                print(f'Paper acceptance evidence: {evidence}', flush=True)
