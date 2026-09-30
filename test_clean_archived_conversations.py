from argparse import Namespace
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from clean_codex_catalog import Audit
import clean_archived_conversations as recovery


class ArchiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home/"sqlite").mkdir()
        with closing(sqlite3.connect(self.home/"sqlite/codex-dev.db")) as db:
            db.execute("CREATE TABLE local_thread_catalog_hosts(host_id TEXT,host_kind TEXT)")
            db.execute("INSERT INTO local_thread_catalog_hosts VALUES ('chatgpt:account','chatgpt')")
            db.execute("CREATE TABLE local_thread_catalog(host_id TEXT,thread_id TEXT,display_title TEXT,project_id TEXT,source_kind TEXT)")
            self.id = "00000000-0000-0000-0000-000000000001"
            db.execute("INSERT INTO local_thread_catalog VALUES (?,?,?,?,?)",("chatgpt:account",self.id,"Old",None,"chatgpt"))
            db.commit()
        self.args = Namespace(codex_home=self.home, output_dir=self.home, lang="zh-TW", codex=None, wait_seconds=12)
        self.audit = Audit(self.home/"audit")

    def test_source_selection_and_cancel(self):
        for value, result in (("1","local"),("2","cloud"),("3",None),("",None)):
            with patch("builtins.input",return_value=value):
                self.assertEqual(recovery.choose_source(),result)

    def test_web_handoff_never_claims_cloud_deletion_or_starts_cache_cleanup(self):
        with patch("builtins.input",return_value="1"), patch.object(recovery.webbrowser,"open",return_value=True) as browser, patch.object(recovery,"run_child") as child:
            self.assertEqual(recovery.cloud_workflow(self.args,self.audit),2)
        browser.assert_called_once_with(recovery.CLOUD_SETTINGS_URL)
        child.assert_not_called()
        event = json.loads(self.audit.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertFalse(event["cloud_deleted"])
        self.assertEqual(event["status"],"manual_cloud_deletion_pending")

    def test_unconfirmed_cloud_cache_selection_does_not_write_a_deletion_plan(self):
        with patch("builtins.input",side_effect=["2",self.id,"CANCEL"]),patch.object(recovery,"run_child") as child:
            self.assertEqual(recovery.cloud_workflow(self.args,self.audit),0)
        child.assert_not_called()
        self.assertFalse((self.audit.directory/"confirmed-cloud-deleted-plan.json").exists())

    def test_explicit_cloud_confirmation_previews_then_cleans_only_selected_cache_ids(self):
        with patch("builtins.input",side_effect=["2",self.id,"CLOUD DELETED"]),patch.object(recovery,"run_child",return_value=0) as child:
            self.assertEqual(recovery.cloud_workflow(self.args,self.audit),0)
        plan=json.loads((self.audit.directory/"confirmed-cloud-deleted-plan.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["id"] for entry in plan["entries"]],[self.id])
        self.assertTrue(plan["confirmed_deleted_in_cloud"])
        self.assertNotIn("--apply",child.call_args_list[0].args[2])
        self.assertIn("--apply",child.call_args_list[1].args[2])

    def test_failed_cache_preview_does_not_apply(self):
        with patch("builtins.input",side_effect=["2",self.id,"CLOUD DELETED"]),patch.object(recovery,"run_child",return_value=1) as child:
            self.assertEqual(recovery.cloud_workflow(self.args,self.audit),1)
        self.assertEqual(child.call_count,1)

    def test_invalid_absent_local_or_other_account_ids_cannot_be_confirmed(self):
        for ids in ([],[{}],[self.id,self.id],["00000000-0000-0000-0000-000000000002"]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                recovery.cloud_cache_selection(self.home,ids)
        with closing(sqlite3.connect(self.home/"sqlite/codex-dev.db")) as db:
            db.execute("UPDATE local_thread_catalog SET source_kind='local'"); db.commit()
        with self.assertRaises(ValueError):
            recovery.cloud_cache_selection(self.home,[self.id])
        with closing(sqlite3.connect(self.home/"sqlite/codex-dev.db")) as db:
            db.execute("UPDATE local_thread_catalog SET source_kind='chatgpt',host_id='another'"); db.commit()
        with self.assertRaises(ValueError):
            recovery.cloud_cache_selection(self.home,[self.id])

    def test_valid_cache_selection_does_not_assert_cloud_deletion(self):
        self.assertFalse(recovery.cloud_cache_selection(self.home,[self.id])["confirmed_deleted_in_cloud"])

    def test_local_stage_forwards_custom_paths_and_missing_rollout_review_without_shell(self):
        self.args.codex = "C:/Codex & tools/codex.exe"
        with patch.object(recovery.subprocess,"run",return_value=Mock(returncode=0)) as run:
            self.assertEqual(recovery.run_child("delete_archived_threads.py",self.args,["--interactive","--allow-missing-rollouts"],self.audit),0)
        command=run.call_args.args[0]
        self.assertIsInstance(command,list)
        self.assertIn(self.args.codex,command)
        self.assertIn("--allow-missing-rollouts",command)
        self.assertNotIn("shell",run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
