"""Opt-in disposable Paper 26.2 update and recovery acceptance."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from gamestack import backup, update
from gamestack.cli import configure
from gamestack.pack import GameStackError, load_pack
from gamestack.runtime import Runtime

BASE = Path(__file__).resolve().parents[2] / 'packs/minecraft-paper'
OLD = BASE / 'pack.yaml'
NEW = BASE / 'candidates/26.2-129/pack.yaml'
ENABLED = (os.environ.get('GAMESTACK_PAPER_UPDATE_TEST') == '1'
           and os.environ.get('GAMESTACK_MINECRAFT_EULA') == 'TRUE'
           and bool(os.environ.get('GAMESTACK_PAPER_OWNER')))


@unittest.skipUnless(ENABLED, 'Requires explicit update opt-in, EULA acceptance, and a Java player name')
class PaperUpdateIntegration(unittest.TestCase):
    def test_pinned_update_and_explicit_recovery(self):
        root = Path(tempfile.mkdtemp(prefix='gamestack-paper-update-')) / 'instances'
        runtime = Runtime(root)
        instance = 'paper-update-' + uuid.uuid4().hex[:10]
        old = load_pack(OLD)
        new = load_pack(NEW)
        with patch('sys.stdin.isatty', return_value=False):
            values = configure(old, {'EULA': os.environ['GAMESTACK_MINECRAFT_EULA'],
                                     'OPS': os.environ['GAMESTACK_PAPER_OWNER'],
                                     'WHITELIST': os.environ['GAMESTACK_PAPER_OWNER']})
        directory = runtime.prepare(old, instance, values)
        report = {'instance': instance, 'old_build': old['environment']['PAPER_BUILD']['value'],
                  'new_build': new['environment']['PAPER_BUILD']['value'],
                  'candidate_lock_sha256': hashlib.sha256(NEW.with_name('upstream-lock.json').read_bytes()).hexdigest(),
                  'result': 'incomplete'}
        print(f'Paper update data retained at {directory}', flush=True)
        try:
            self.assertEqual(runtime.lifecycle('start', instance), 'healthy')
            old_hash = update._paper_jar(directory, '26.2', '121')
            self.assertEqual(runtime.lifecycle('stop', instance), 'stopped')
            (directory / 'data/update-acceptance.txt').write_text('before update')
            self.assertEqual(runtime.lifecycle('start', instance), 'healthy')
            changed = update.run(runtime, instance, NEW)
            backup.verify(changed.backup, instance)
            update._paper_jar(directory, '26.2', '129', json.loads(NEW.with_name('upstream-lock.json').read_text())['paper']['sha256'])
            report['pre_update_backup_id'] = changed.backup.stem
            report['updated_health'] = runtime.lifecycle('status', instance)
            self.assertEqual(report['updated_health'], 'started (healthy)')
            self.assertEqual((directory / 'data/update-acceptance.txt').read_text(), 'before update')
            self.assertEqual(runtime.lifecycle('stop', instance), 'stopped')
            (directory / 'data/update-acceptance.txt').write_text('after update')
            self.assertEqual(runtime.lifecycle('start', instance), 'healthy')
            recovered = update.recover(runtime, instance)
            self.assertEqual(update._paper_jar(directory, '26.2', '121'), old_hash)
            self.assertEqual((directory / 'data/update-acceptance.txt').read_text(), 'before update')
            self.assertEqual((recovered.retained_data / 'update-acceptance.txt').read_text(), 'after update')
            report['later_world_retained'] = str(recovered.retained_data)
            report['recovered_health'] = runtime.lifecycle('status', instance)
            self.assertEqual(report['recovered_health'], 'started (healthy)')
            # An internally consistent but wrong reviewed checksum must fail after recreation.
            bad = root.parent / 'bad-candidate'
            bad.mkdir()
            (bad / 'pack.yaml').write_bytes(NEW.read_bytes())
            lock = json.loads(NEW.with_name('upstream-lock.json').read_text())
            lock['paper']['sha256'] = '0' * 64
            (bad / 'upstream-lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(GameStackError, 'checksum'):
                update.run(runtime, instance, bad / 'pack.yaml')
            self.assertTrue((directory / update.MARKER).exists())
            update.recover(runtime, instance)
            report['wrong_checksum_rejected'] = True
            report['result'] = 'passed'
        finally:
            try:
                runtime.command(runtime.compose_command(directory) + ['down', '--timeout', '120'], timeout=180)
            finally:
                evidence = root.parent / 'update-evidence.json'
                with evidence.open('x') as stream:
                    os.chmod(evidence, 0o600)
                    json.dump(report, stream, indent=2)
                print(f'Paper update evidence: {evidence}', flush=True)
