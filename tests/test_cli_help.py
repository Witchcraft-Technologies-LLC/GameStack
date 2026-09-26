"""Help must be useful and available even on an unconfigured/offline host."""
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

from gamestack.cli import main, parser


class HelpTests(unittest.TestCase):
    def help(self, arguments):
        output = io.StringIO()
        with patch('gamestack.cli.Runtime') as runtime, patch('gamestack.cli.load_pack') as load, \
                patch('builtins.input') as prompt, redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            main(arguments)
        self.assertEqual(exited.exception.code, 0)
        runtime.assert_not_called()
        load.assert_not_called()
        prompt.assert_not_called()
        return output.getvalue()

    def test_every_help_route_is_offline_and_includes_examples(self):
        routes = [[], ['list'], ['install'], ['pack'], ['pack', 'validate'], ['backup'],
                  ['backup', 'list'], ['backup', 'verify'], ['restore'], ['rm'],
                  ['start'], ['stop'], ['restart'], ['status'], ['doctor']]
        for route in routes:
            for flag in ('--help', '-h'):
                with self.subTest(route=route, flag=flag):
                    output = self.help([*route, flag])
                    self.assertIn('usage: gamestack', output)
                    self.assertIn('Examples:', output)
                    self.assertIn('--help', output)
                    if route:
                        self.assertIn('Global options go BEFORE', output)

    def test_nested_backup_help_is_specific_with_or_without_operands(self):
        for arguments in (['backup', 'list'], ['backup', 'list', 'friends']):
            output = self.help([*arguments, '--help'])
            self.assertIn('usage: gamestack backup list', output)
            self.assertIn('does not recheck', output)
            self.assertNotIn('backup_id', output)
        for arguments in (['backup', 'verify'], ['backup', 'verify', 'friends', 'some-id']):
            output = self.help([*arguments, '--help'])
            self.assertIn('usage: gamestack backup verify', output)
            self.assertIn('backup_id', output)
            self.assertIn('does not authenticate', output)

    def test_backup_names_and_operational_parsing_remain_compatible(self):
        for arguments in (['list'], ['verify'], ['friends'], ['list', 'friends'], ['verify', 'friends', 'some-id']):
            self.assertEqual(parser().parse_args(['backup', *arguments]).arguments, arguments)

    def test_help_explains_safety_and_script_requirements(self):
        output = self.help(['restore', '--help'])
        for phrase in ('entire data folder', 'safety backup', 'identical GamePack', 'Scripts require BACKUP-ID and --yes', 'interrupted-restore-recovery'):
            self.assertIn(phrase, output)
        self.assertIn('unencrypted', self.help(['backup', '--help']))
        self.assertIn('cannot be started or restored', self.help(['rm', '--help']))
        output = self.help(['install', '--help'])
        for option in ('--values', '--name', '--prepare-only', '--bind-address'):
            self.assertIn(option, output)
        self.assertIn('explicit agreements', output)

    def test_help_with_unusable_root_does_not_validate_or_create_storage(self):
        output = self.help(['--root', '/', 'restore', '--help'])
        self.assertIn('usage: gamestack restore', output)
        output = self.help(['--help'])
        self.assertIn('--debug', output)
        self.assertIn('default:', output)
