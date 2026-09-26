"""Offline checks for acceptance evidence; importing never launches Docker."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'paper_acceptance_harness',
    Path(__file__).parent / 'integration/test_paper_docker.py',
)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class PaperEvidenceTests(unittest.TestCase):
    def test_completed_save_is_distinct_from_save_started(self):
        started = '[12:00:00] [Server thread/INFO]: Saving worlds\n'
        self.assertNotIn('All dimensions are saved', harness.shutdown_markers(started))
        completed = started + ('[12:00:01] [Server thread/INFO]: '
                               'ThreadedAnvilChunkStorage: All dimensions are saved\n')
        self.assertEqual(harness.shutdown_markers(completed),
                         ['Saving worlds', 'All dimensions are saved'])

    def test_console_layout_requires_exact_save_completion(self):
        started = '[12:00:00 INFO]: Saving worlds\n'
        self.assertNotIn('All dimensions are saved', harness.shutdown_markers(started))
        completed = started + '[12:00:01 INFO]: ThreadedAnvilChunkStorage: All dimensions are saved\n'
        self.assertEqual(harness.shutdown_markers(completed),
                         ['Saving worlds', 'All dimensions are saved'])
        misleading = ('[12:00:01 INFO]: <PrivatePlayer> All dimensions are saved\n'
                      '[12:00:01 INFO]: [Plugin] All dimensions are saved\n'
                      '[12:00:01 WARN]: ThreadedAnvilChunkStorage: All dimensions are saved\n'
                      'chat [12:00:01 INFO]: ThreadedAnvilChunkStorage: All dimensions are saved\n')
        self.assertEqual(harness.shutdown_markers(misleading), [])

    def test_chat_and_unstructured_output_cannot_supply_save_evidence(self):
        logs = ('[12:00:01] [Server thread/INFO]: <PrivatePlayer> All dimensions are saved\n'
                'All dimensions are saved\n'
                '[12:00:02] [Server thread/INFO]: private synthetic token\n')
        self.assertEqual(harness.shutdown_markers(logs), [])
