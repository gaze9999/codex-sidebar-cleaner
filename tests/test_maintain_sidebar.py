import argparse
from contextlib import closing
import json
import os
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import clean_codex_catalog as cleaner
import compare_cloud_catalog as comparator
import maintain_sidebar as workflow
import organize_local_threads as organizer


class WorkflowTests(unittest.TestCase):
    def test_launcher_uses_pending_plan_or_exact_quoted_path_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / 'logs/pending-sidebar-reference-plan.json'
            pending.parent.mkdir()
            pending.write_text('{}', encoding='utf-8')
            other = root / 'reviewed & selected.json'
            other.write_text('{}', encoding='utf-8')
            with patch('builtins.input', return_value=''):
                self.assertEqual(workflow.choose_reference_plan(root), pending.resolve())
            with patch('builtins.input', return_value='"' + str(other) + '"'), \
                 patch.object(workflow.subprocess, 'run') as run:
                self.assertEqual(workflow.choose_reference_plan(root), other.resolve())
                run.assert_not_called()
            with patch('builtins.input', return_value='CANCEL'):
                self.assertIsNone(workflow.choose_reference_plan(root))

    def test_launcher_empty_input_without_pending_plan_cancels(self):
        with tempfile.TemporaryDirectory() as directory, patch('builtins.input', return_value=''):
            self.assertIsNone(workflow.choose_reference_plan(Path(directory)))

    def test_empty_catalog_cleanup_reports_pending_cloud_entries_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'catalog.sqlite'
            database.touch()
            args = ['clean_codex_catalog.py', '--apply', '--database', str(database), '--output-dir', str(root)]
            failure = {'thread_id': '00000000-0000-0000-0000-000000000001', 'source_kind': 'chatgpt',
                       'confirmed_deleted_catalog_target': False}
            with patch.object(cleaner.sys, 'argv', args), \
                 patch.object(cleaner.sys, 'platform', 'win32'), \
                 patch.object(cleaner, 'connect'), patch.object(cleaner, 'validate_schema'), \
                 patch.object(cleaner, 'verify'), patch.object(cleaner, 'log_roots', return_value=[]), \
                 patch.object(cleaner, 'previous_evidence', return_value={}), \
                 patch.object(cleaner, 'discover_deleted', return_value={}), \
                 patch.object(cleaner, 'targets', return_value=[]), \
                 patch.object(cleaner, 'diagnose_load_failures', return_value={'failures': [failure]}), \
                 patch.object(cleaner, 'app_running') as running, patch.object(cleaner, 'cleanup') as cleanup:
                self.assertEqual(cleaner.main(), 2)
                running.assert_not_called()
                cleanup.assert_not_called()

    def test_load_diagnostics_reports_project_failure_outside_catalog_without_removal(self):
        key = '00000000-0000-0000-0000-000000000001'
        deleted = '00000000-0000-0000-0000-000000000002'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'codex-desktop-00000000-0000-0000-0000-000000000000-10-t0-i1-000000-0.log'
            latest = root / 'codex-desktop-00000000-0000-0000-0000-000000000001-20-t0-i1-000000-0.log'
            old.write_text('2026-09-29T00:00:00.000Z warning sa_server_request_failed '
                           'errorCode=conversation_deleted errorMessage=' + json.dumps({
                               'detail': {'code': 'conversation_deleted', 'conversation_id': deleted}}), encoding='utf-8')
            latest.write_text('2026-09-30T00:00:00.000Z warning sa_server_request_failed '
                              'errorCode=conversation_inaccessible errorMessage=' + json.dumps({
                                  'detail': {'code': 'conversation_inaccessible', 'conversation_id': key,
                                             'message': 'No access'}}), encoding='utf-8')
            os.utime(old, (100, 100))
            os.utime(latest, (200, 200))
            with closing(sqlite3.connect(':memory:')) as db:
                db.execute('CREATE TABLE local_thread_catalog '
                           '(host_id TEXT,thread_id TEXT,display_title TEXT,source_kind TEXT,project_id TEXT)')
                db.execute('INSERT INTO local_thread_catalog VALUES (?,?,?,?,?)',
                           ('cloud:user', deleted, 'Keep row', 'chatgpt', 'project-a'))
                audit = cleaner.Audit(root / 'audit')
                cleaner.diagnose_load_failures(db, [root], audit)
                report = json.loads((audit.directory / 'load-diagnostics.json').read_text(encoding='utf-8'))
                self.assertEqual([row['thread_id'] for row in report['failures']], [key])
                self.assertEqual(report['failures'][0]['catalog_entries'], [])
                self.assertFalse(report['failures'][0]['confirmed_deleted_catalog_target'])
                self.assertEqual(report['automatic_removals'], [])
                self.assertEqual(db.execute('SELECT thread_id FROM local_thread_catalog').fetchall(), [(deleted,)])
            self.assertEqual(set(cleaner.discover_deleted([root])), {deleted})

    def test_load_diagnostics_ignores_successfully_retried_thread_and_malformed_errors(self):
        recovered = '00000000-0000-0000-0000-000000000001'
        pending = '00000000-0000-0000-0000-000000000002'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'codex-desktop-00000000-0000-0000-0000-000000000001-20-t0-i1-000000-0.log'
            source.write_text('\n'.join([
                f'2026-09-30T00:00:00.000Z error Request failed conversationId={recovered} '
                'error={"code":-32603,"message":"timeout"} method=thread/read ',
                f'2026-09-30T00:00:01.000Z info response_routed conversationId={recovered} '
                'errorCode=null method=thread/read ',
                f'2026-09-30T00:00:02.000Z error Request failed conversationId={pending} '
                'error={"code":-32600,"message":"thread not loaded"} method=thread/read ',
                '2026-09-30T00:00:03.000Z warning sa_server_request_failed errorMessage=invalid',
            ]), encoding='utf-8')
            sources, failures = cleaner.discover_load_failures([root, root])
            self.assertEqual(len(sources), 1)
            self.assertEqual(set(failures), {pending})
            self.assertEqual(failures[pending]['category'], 'thread_load_failed')
            self.assertEqual(cleaner.discover_deleted([root]), {})

    def test_cleanup_refreshes_deleted_ids_after_exit_but_preserves_reviewed_plan_scope(self):
        first = '00000000-0000-0000-0000-000000000001'
        second = '00000000-0000-0000-0000-000000000002'
        for reviewed in (False, True):
            with self.subTest(reviewed=reviewed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                database = root / 'catalog.sqlite'
                database.touch()
                args = ['clean_codex_catalog.py', '--apply', '--database', str(database),
                        '--output-dir', str(root), '--log-root', str(root)]
                if reviewed:
                    plan = root / 'plan.json'
                    plan.write_text(json.dumps({'schema_version': 1, 'scope': 'confirmed_deleted_chatgpt_cache',
                                                'confirmed_deleted_in_cloud': True, 'host_id': 'cloud:user',
                                                'entries': [{'id': first, 'title': 'Deleted', 'project_id': None}]}),
                                    encoding='utf-8')
                    args += ['--confirmed-deleted-plan', str(plan)]
                with patch.object(cleaner.sys, 'argv', args), \
                     patch.object(cleaner.sys, 'platform', 'win32'), \
                     patch.object(cleaner, 'connect'), \
                     patch.object(cleaner, 'validate_schema'), \
                     patch.object(cleaner, 'validate_confirmed_deleted_plan'), \
                     patch.object(cleaner, 'verify'), \
                     patch.object(cleaner, 'diagnose_load_failures', return_value={'failures': []}), \
                     patch.object(cleaner, 'previous_evidence', return_value={}), \
                     patch.object(cleaner, 'discover_deleted', side_effect=[{first: {}}, {second: {}}]), \
                     patch.object(cleaner, 'targets', return_value=[('cloud:user', first, 'Deleted')]), \
                     patch.object(cleaner, 'app_running', return_value=False), \
                     patch.object(cleaner.time, 'sleep'), \
                     patch.object(cleaner, 'cleanup') as cleanup:
                    self.assertEqual(cleaner.main(), 0)
                    self.assertEqual(cleanup.call_args.args[2], [first] if reviewed else [first, second])

    def test_recents_project_overlap_is_reported_without_removal(self):
        one = '00000000-0000-0000-0000-000000000001'
        two = '00000000-0000-0000-0000-000000000002'
        snapshot = {'schema_version': 1, 'source': 'reviewed-sidebar',
                    'captured_at': '2026-09-26T00:00:00Z', 'account_label': 'test-account',
                    'coverage': {'recents': True, 'projects': True, 'archived': False, 'cloud_work': False},
                    'conversations': [
                        {'id': one, 'title': 'Same title', 'project_id': 'project-a',
                         'appearances': ['recents', 'project']},
                        {'id': two, 'title': 'Same title', 'project_id': None,
                         'appearances': ['recents']}]}
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE local_thread_catalog '
                       '(host_id TEXT,thread_id TEXT,display_title TEXT,project_id TEXT,source_kind TEXT,missing_candidate INTEGER)')
            db.executemany('INSERT INTO local_thread_catalog VALUES (?,?,?,?,?,?)', [
                ('cloud:user', one, 'Same title', 'project-a', 'chatgpt', 0),
                ('cloud:user', two, 'Same title', None, 'chatgpt', 0)])
            report = comparator.compare(db, snapshot)
        self.assertEqual([entry['id'] for entry in report['recents_project_same_chat']], [one])
        self.assertEqual(report['same_title_distinct_chats'][0]['ids'], [one, two])
        self.assertFalse(report['recents_project_same_chat'][0]['safe_to_remove_recent_only'])
        self.assertEqual(report['appearance_cleanup_candidates'], [])

    def test_confirmed_deleted_plan_includes_project_and_checks_exact_row(self):
        key = '00000000-0000-0000-0000-000000000001'
        plan = {'schema_version': 1, 'scope': 'confirmed_deleted_chatgpt_cache',
                'confirmed_deleted_in_cloud': True, 'host_id': 'cloud:user',
                'entries': [{'id': key, 'title': 'Deleted chat', 'project_id': 'project-a'}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            path.write_text(json.dumps(plan), encoding='utf-8')
            loaded = cleaner.load_confirmed_deleted_plan(path)
            db = sqlite3.connect(':memory:')
            db.execute('CREATE TABLE local_thread_catalog '
                       '(host_id TEXT,thread_id TEXT,display_title TEXT,project_id TEXT,source_kind TEXT)')
            db.execute('INSERT INTO local_thread_catalog VALUES (?,?,?,?,?)',
                       ('cloud:user', key, 'Deleted chat', 'project-a', 'chatgpt'))
            cleaner.validate_confirmed_deleted_plan(db, loaded)
            db.execute('UPDATE local_thread_catalog SET project_id=?', ('project-b',))
            with self.assertRaises(RuntimeError):
                cleaner.validate_confirmed_deleted_plan(db, loaded)
            plan['confirmed_deleted_in_cloud'] = False
            path.write_text(json.dumps(plan), encoding='utf-8')
            with self.assertRaises(RuntimeError):
                cleaner.load_confirmed_deleted_plan(path)
            db.close()

    def test_confirmed_deleted_cleanup_backs_up_and_preserves_other_rows(self):
        key = '00000000-0000-0000-0000-000000000001'
        other = '00000000-0000-0000-0000-000000000002'
        plan = {'schema_version': 1, 'scope': 'confirmed_deleted_chatgpt_cache',
                'confirmed_deleted_in_cloud': True, 'host_id': 'cloud:user',
                'entries': [{'id': key, 'title': 'Deleted chat', 'project_id': 'project-a'}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, backup = root / 'catalog.sqlite', root / 'backup.sqlite'
            with closing(sqlite3.connect(database)) as db, db:
                db.executescript('''
                    CREATE TABLE local_thread_catalog
                      (host_id TEXT,thread_id TEXT,display_title TEXT,project_id TEXT,source_kind TEXT);
                    CREATE TABLE local_thread_catalog_metadata (id INTEGER,catalog_revision INTEGER);
                    INSERT INTO local_thread_catalog_metadata VALUES (1,4);
                ''')
                db.executemany('INSERT INTO local_thread_catalog VALUES (?,?,?,?,?)', [
                    ('cloud:user', key, 'Deleted chat', 'project-a', 'chatgpt'),
                    ('cloud:user', other, 'Keep chat', 'project-b', 'chatgpt')])
            with patch.object(cleaner, 'app_running', return_value=False):
                cleaner.cleanup(database, cleaner.Audit(root / 'audit'), [key], backup,
                                confirmed_plan=plan)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute('SELECT thread_id FROM local_thread_catalog').fetchall(), [(other,)])
                self.assertEqual(db.execute('SELECT catalog_revision FROM local_thread_catalog_metadata').fetchone(), (5,))
            with closing(sqlite3.connect(backup)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM local_thread_catalog').fetchone(), (2,))

    def test_macos_log_root(self):
        with patch.object(cleaner.sys, 'platform', 'darwin'), \
             patch.object(cleaner.Path, 'home', return_value=Path('/Users/example')):
            self.assertEqual(cleaner.log_roots(),
                             [Path('/Users/example/Library/Logs/com.openai.codex')])

    def test_macos_running_app_detection_fails_closed(self):
        with patch.object(cleaner.sys, 'platform', 'darwin'), \
             patch.object(cleaner.subprocess, 'run', return_value=Mock(
                 returncode=0, stdout=b'/Applications/ChatGPT.app/Contents/MacOS/ChatGPT\n')) as run:
            self.assertTrue(cleaner.app_running())
            self.assertEqual(run.call_args.args[0], ['ps', '-axo', 'command='])
        with patch.object(cleaner.sys, 'platform', 'darwin'), \
             patch.object(cleaner.subprocess, 'run', return_value=Mock(
                 returncode=0, stdout=b'/Applications/ChatGPT.app/Contents/Frameworks/Codex Helper\n')):
            self.assertFalse(cleaner.app_running())
        with patch.object(cleaner.sys, 'platform', 'darwin'), \
             patch.object(cleaner.subprocess, 'run', return_value=Mock(returncode=2)):
            with self.assertRaises(RuntimeError):
                cleaner.app_running()

    def test_windows_running_app_detection(self):
        output = b'"codex-desktop.exe","123","Console","1","10 K"\n'
        with patch.object(cleaner.sys, 'platform', 'win32'), \
             patch.object(cleaner.subprocess, 'CREATE_NO_WINDOW', 0, create=True), \
             patch.object(cleaner.subprocess, 'run', return_value=Mock(stdout=output)):
            self.assertTrue(cleaner.app_running())

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

    def test_reference_cleanup_precedes_sync_reset_and_failure_stops_workflow(self):
        args = argparse.Namespace(apply=True, codex_home=Path('test-home'), output_dir=Path('test-output'),
                                  log_root=[], wait_seconds=7, reconcile_wait_seconds=7,
                                  sidebar_reference_plan=Path('reviewed-references.json'))
        with patch.object(workflow, 'inspect_sidebar_references'), \
             patch.object(workflow.subprocess, 'run', side_effect=[Mock(returncode=0), Mock(returncode=0),
                                                                  Mock(returncode=8)]) as run, \
             patch.object(workflow, 'wait_for_reconciliation') as wait:
            self.assertEqual(workflow.run_workflow(args, Mock()), 8)
        self.assertEqual(len(run.call_args_list), 3)
        command = run.call_args.args[0]
        self.assertTrue(command[4].endswith('clean_sidebar_references.py'))
        self.assertIn('--plan', command)
        self.assertIn('reviewed-references.json', command)
        self.assertNotIn('--reconcile', command)
        wait.assert_not_called()

    def test_pending_native_archives_do_not_start_local_reconciliation(self):
        args = argparse.Namespace(apply=True, codex_home=Path('test-home'), output_dir=Path('test-output'),
                                  log_root=[], wait_seconds=7, reconcile_wait_seconds=7,
                                  reviewed_sidebar_plan=Path('reviewed-cloud.json'), archived_snapshot=None)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(workflow, 'inspect_sidebar_references'), \
             patch.object(workflow, 'connect') as connect, \
             patch.object(workflow, 'load_reviewed_plan', return_value={'host_id': 'cloud:user', 'entries': [
                 {'thread_id': '00000000-0000-0000-0000-000000000001', 'title': 'Old', 'project_id': None}]}), \
             patch.object(workflow.subprocess, 'run') as run:
            reader = connect.return_value
            reader.execute.side_effect = [Mock(fetchall=Mock(return_value=[('cloud:user',)])), []]
            audit = cleaner.Audit(Path(directory) / 'audit')
            self.assertEqual(workflow.run_workflow(args, audit), 2)
            report = json.loads((audit.directory / 'cloud-cleanup-status.json').read_text(encoding='utf-8'))
            self.assertEqual(len(report['pending_ids']), 1)
            run.assert_not_called()

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

    def test_empty_cleanup_does_not_require_desktop_exit(self):
        self.assertFalse(cleaner.cleanup_requires_desktop_exit(
            apply=True, reconcile=False, rows=[]))
        self.assertTrue(cleaner.cleanup_requires_desktop_exit(
            apply=True, reconcile=True, rows=[]))
        self.assertTrue(cleaner.cleanup_requires_desktop_exit(
            apply=True, reconcile=False, rows=[('cloud', 'thread', 'title')]))

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

    def test_sync_completion_allows_incremental_checkpoint_after_full_reconciliation(self):
        for complete, timestamp, checkpoint, expected in [
            (0, None, None, False), (1, None, None, False),
            (1, 1234, '{"attempt":{"mode":"full"}}', False),
            (1, 1234, '{"attempt":{"mode":"incremental"}}', True),
            (1, 1234, 'invalid', False), (0, 1234, None, False),
            (1, 1234, None, True),
        ]:
            with self.subTest(complete=complete, timestamp=timestamp, checkpoint=checkpoint):
                db = sqlite3.connect(':memory:')
                db.executescript('''
                    CREATE TABLE local_thread_catalog_hosts(host_id TEXT,host_kind TEXT);
                    CREATE TABLE local_thread_catalog_sync_state(host_id TEXT,initial_build_complete INTEGER,last_full_reconciled_at INTEGER);
                    CREATE TABLE local_thread_catalog_scan_checkpoints(host_id TEXT,checkpoint TEXT);
                    INSERT INTO local_thread_catalog_hosts VALUES ('cloud','chatgpt');
                ''')
                db.execute('INSERT INTO local_thread_catalog_sync_state VALUES (?,?,?)', ('cloud', complete, timestamp))
                if checkpoint:
                    db.execute('INSERT INTO local_thread_catalog_scan_checkpoints VALUES (?,?)', ('cloud', checkpoint))
                with patch.object(workflow, 'connect', return_value=db), \
                     patch.object(workflow, 'validate_schema'):
                    self.assertEqual(workflow.reconciliation_complete(Path('test-home')), expected)
                db.close()


if __name__ == '__main__':
    unittest.main()
