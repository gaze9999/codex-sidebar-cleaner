import argparse
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import maintain_sidebar as workflow
import organize_local_threads as organizer


class WorkflowTests(unittest.TestCase):
    def run_case(self, apply, codes, wait_error=None):
        args = argparse.Namespace(apply=apply, codex_home=Path('test-home'),
                                  output_dir=Path('test-output'), log_root=[Path('app-logs')],
                                  codex=None, section_name='Local', wait_seconds=7, reconcile_wait_seconds=7)
        audit = Mock()
        with patch.object(workflow, 'wait_for_reconciliation', side_effect=wait_error) as wait, \
             patch.object(workflow.subprocess, 'run', side_effect=[Mock(returncode=c) for c in codes]) as run:
            try:
                result = workflow.run_workflow(args, audit)
            except TimeoutError:
                self.assertEqual(len(run.call_args_list), 3)
                raise
        if apply and len(run.call_args_list) > 3:
            wait.assert_called_once_with(args.codex_home, 7, audit)
        return result, [call.args[0] for call in run.call_args_list], audit

    def test_apply_order_and_shared_options(self):
        result, commands, audit = self.run_case(True, [0, 0, 0, 0])
        self.assertEqual(result, 0)
        self.assertIn('--verify', commands[0])
        self.assertIn('--scan-projects', commands[1])
        self.assertIn('--apply', commands[2])
        self.assertNotIn('--reconcile', commands[3])
        self.assertIn('--reconcile', commands[2])
        self.assertIn('--apply', commands[3])
        self.assertTrue(all(command[4].endswith('clean_codex_catalog.py') for command in commands))
        for command in commands:
            self.assertEqual(command[command.index('--codex-home') + 1], 'test-home')
            self.assertEqual(command[command.index('--output-dir') + 1], 'test-output')
        self.assertFalse(audit.record.call_args.kwargs['app_reconciliation_pending'])

    def test_preview_never_applies_or_resets(self):
        result, commands, audit = self.run_case(False, [0, 0])
        self.assertEqual(result, 0)
        self.assertIn('--verify', commands[0])
        for command in commands:
            self.assertNotIn('--apply', command)
            self.assertNotIn('--reconcile', command)
        self.assertFalse(audit.record.call_args.kwargs['app_reconciliation_pending'])

    def test_each_failed_stage_stops_remaining_work(self):
        for index in range(4):
            with self.subTest(index=index):
                result, commands, audit = self.run_case(True, [0] * index + [9])
                self.assertEqual(result, 9)
                self.assertEqual(len(commands), index + 1)
                self.assertEqual(audit.record.call_args.args[0], 'workflow_stopped')

    def test_reconciliation_timeout_blocks_cleanup_and_organization(self):
        with self.assertRaises(TimeoutError):
            self.run_case(True, [0, 0, 0], wait_error=TimeoutError('pending'))

    def test_wait_requires_observed_completion(self):
        with patch.object(workflow, 'reconciliation_complete', side_effect=[False, True]), \
             patch.object(workflow.time, 'sleep') as sleep:
            workflow.wait_for_reconciliation(Path('test-home'), 30, Mock())
            sleep.assert_called_once_with(2)

    def test_wait_times_out_without_completion(self):
        with patch.object(workflow, 'reconciliation_complete', return_value=False), \
             self.assertRaises(TimeoutError):
            workflow.wait_for_reconciliation(Path('test-home'), 0, Mock())

    def test_organization_precheck_failure_prevents_server_and_moves(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(organizer.sys, 'argv', ['organize_local_threads.py', '--apply',
                                                '--codex-home', directory, '--output-dir', directory]), \
             patch.object(organizer.subprocess, 'run', return_value=Mock(returncode=5)) as run, \
             patch.object(organizer, 'eligible_catalog') as eligible, \
             patch.object(organizer, 'AppServer') as server:
            self.assertEqual(organizer.main(), 5)
            self.assertIn('--verify', run.call_args.args[0])
            eligible.assert_not_called()
            server.assert_not_called()

    def test_sync_completion_requires_timestamp_and_no_checkpoint(self):
        for complete, timestamp, checkpoint, expected in [
            (0, None, False, False), (1, None, False, False),
            (1, 1234, True, False), (0, 1234, False, False),
            (1, 1234, False, True),
        ]:
            with self.subTest(complete=complete, timestamp=timestamp, checkpoint=checkpoint):
                db = sqlite3.connect(':memory:')
                db.executescript('''
                    CREATE TABLE local_thread_catalog_hosts(host_id TEXT,host_kind TEXT);
                    CREATE TABLE local_thread_catalog_sync_state(host_id TEXT,initial_build_complete INTEGER,last_full_reconciled_at INTEGER);
                    CREATE TABLE local_thread_catalog_scan_checkpoints(host_id TEXT);
                    INSERT INTO local_thread_catalog_hosts VALUES ('cloud','chatgpt');
                ''')
                db.execute('INSERT INTO local_thread_catalog_sync_state VALUES (?,?,?)', ('cloud', complete, timestamp))
                if checkpoint:
                    db.execute("INSERT INTO local_thread_catalog_scan_checkpoints VALUES ('cloud')")
                with patch.object(workflow, 'connect', return_value=db), \
                     patch.object(workflow, 'validate_schema'):
                    self.assertEqual(workflow.reconciliation_complete(Path('test-home')), expected)


if __name__ == '__main__':
    unittest.main()
