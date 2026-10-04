"""Draft Satisfactory pack compatibility, without Docker or game downloads."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gamestack.cli import configure
from gamestack.pack import load_pack
from gamestack.runtime import Runtime
import yaml


PACK = Path(__file__).resolve().parents[1] / 'packs/satisfactory/pack.yaml'


class SatisfactoryTests(unittest.TestCase):
    def test_preparation_preserves_upstream_storage_and_health_contract(self):
        pack = load_pack(PACK)
        with patch('sys.stdin.isatty', return_value=False):
            values = configure(pack, {})
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(Path(tmp).resolve() / 'instances')
            with patch('gamestack.runtime.os.getuid', return_value=1000, create=True), \
                    patch('gamestack.runtime.os.getgid', return_value=1000, create=True):
                directory = runtime.prepare(pack, 'factory', values)
            service = yaml.safe_load((directory / 'compose.yaml').read_text())['services']['server']
            self.assertEqual(service['volumes'][0]['target'], '/config')
            self.assertEqual(service['volumes'][0]['source'], str(directory / 'data'))
            self.assertEqual(service['healthcheck']['test'], ['CMD', 'bash', '/home/steam/healthcheck.sh'])
            self.assertEqual(service['healthcheck']['start_period'], '600s')
            self.assertEqual(service['user'], '1000:1000')
            self.assertEqual(service['environment']['MAXPLAYERS'], '4')
            self.assertEqual(service['environment']['MAXOBJECTS'], '2162688')
            self.assertEqual(
                {(p['target'], p['protocol'], p['host_ip']) for p in service['ports']},
                {(7777, 'tcp', '127.0.0.1'), (7777, 'udp', '127.0.0.1'), (8888, 'tcp', '127.0.0.1')},
            )
