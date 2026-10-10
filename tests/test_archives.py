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
import delete_archived_threads as archive


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
        browser.assert_called_once_with("https://chatgpt.com/settings/archived-chats")
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


class ArchiveDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.db = self.home / "state_5.sqlite"
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY,rollout_path TEXT,archived INTEGER,"
                       "source TEXT,thread_source TEXT,cwd TEXT,title TEXT)")
            db.execute("CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)")
            db.commit()
        self.audit = Audit(self.home / "logs")
        self.backup = self.home / "backup"
        self.server = Mock()
        self.server.call.side_effect = self.rpc_delete

    def add(self, key, parent=None, archived=1, source="vscode", spawn=False):
        path = self.home / "archived_sessions" / (key + ".jsonl")
        path.parent.mkdir(exist_ok=True)
        meta = {"id": key}
        if parent:
            meta["forked_from_id"] = parent
        path.write_text(json.dumps({"type": "session_meta", "payload": meta}) + "\n", encoding="utf-8")
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?)",
                       (key,str(path),archived,source,"user",str(self.home),key))
            if spawn:
                db.execute("INSERT INTO thread_spawn_edges VALUES (?,?)",(parent,key))
            db.commit()

    def rpc_delete(self, method, params):
        self.assertEqual(method,"thread/delete")
        with closing(sqlite3.connect(self.db)) as db:
            key=params["threadId"]
            path=db.execute("SELECT rollout_path FROM threads WHERE id=?",(key,)).fetchone()[0]
            db.execute("DELETE FROM threads WHERE id=?",(key,))
            db.execute("DELETE FROM thread_spawn_edges WHERE child_thread_id=?",(key,))
            db.commit()
        Path(path).unlink(missing_ok=True)
        return {}

    def run_delete(self, apply=True, dependencies=True):
        return archive.delete_archived(self.server,self.home,["parent"],dependencies,apply,
                                       self.audit,self.backup)

    def test_fork_first_backup_and_unrelated_preserved(self):
        self.add("parent"); self.add("fork","parent"); self.add("unrelated")
        plan=self.run_delete()
        self.assertEqual(plan["delete_order"],["fork","parent"])
        self.assertEqual([c.args[1]["threadId"] for c in self.server.call.call_args_list],["fork","parent"])
        with closing(sqlite3.connect(self.backup/"state_5.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM threads").fetchone()[0],3)
        self.assertTrue((self.backup/"archived_sessions/parent.jsonl").exists())
        self.assertEqual(set(archive.catalog(self.home)[0]),{"unrelated"})

    def test_active_fork_and_spawned_child_block_before_backup(self):
        for spawn in (False,True):
            with self.subTest(spawn=spawn):
                self.add("parent"+str(spawn)); self.add("active"+str(spawn),"parent"+str(spawn),0,spawn=spawn)
                with self.assertRaisesRegex(ValueError,"Active"):
                    archive.delete_archived(self.server,self.home,["parent"+str(spawn)],True,True,self.audit,self.backup)
        self.server.call.assert_not_called(); self.assertFalse(self.backup.exists())

    def test_fork_expansion_needs_explicit_option(self):
        self.add("parent"); self.add("fork","parent")
        with self.assertRaisesRegex(ValueError,"include-archived-dependencies"):
            self.run_delete(dependencies=False)
        self.server.call.assert_not_called()

    def test_preview_does_not_start_server_backup_or_delete(self):
        self.add("parent"); self.add("fork","parent")
        self.run_delete(apply=False)
        self.server.call.assert_not_called(); self.assertFalse(self.backup.exists())
        self.assertEqual(len(archive.catalog(self.home)[0]),2)

    def test_cloud_and_unknown_ids_never_delete(self):
        self.add("cloud",source="chatgpt")
        for ids in (["cloud"],["absent"],[],[{}],["cloud","cloud"]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                archive.delete_archived(self.server,self.home,ids,True,True,self.audit,self.backup)
        self.server.call.assert_not_called()

    def test_backup_failure_prevents_any_rpc(self):
        self.add("parent")
        with patch.object(archive,"backup",side_effect=OSError("disk full")),self.assertRaises(OSError):
            self.run_delete()
        self.server.call.assert_not_called()

    def test_new_fork_during_backup_blocks_scope_expansion(self):
        self.add("parent")
        original=archive.backup
        def change(*args):
            original(*args); self.add("new","parent")
        with patch.object(archive,"backup",side_effect=change),self.assertRaisesRegex(RuntimeError,"changed"):
            self.run_delete()
        self.server.call.assert_not_called()

    def test_rpc_failure_stops_and_retains_success_audit(self):
        self.add("parent"); self.add("fork","parent")
        def fail_parent(method,params):
            if params["threadId"]=="parent":
                raise RuntimeError("server rejected")
            return self.rpc_delete(method,params)
        self.server.call.side_effect=fail_parent
        with self.assertRaisesRegex(RuntimeError,"rejected"):
            self.run_delete()
        self.assertEqual(set(archive.catalog(self.home)[0]),{"parent"})
        events=[json.loads(x) for x in self.audit.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([x["thread_id"] for x in events if x["event"]=="archive_delete_verified"],["fork"])

    def test_self_history_reference_is_ignored_and_cycle_rejected(self):
        self.add("parent","parent")
        self.assertEqual(self.run_delete(apply=False)["delete_order"],["parent"])
        self.add("fork","parent")
        path=self.home/"archived_sessions/parent.jsonl"
        path.write_text(json.dumps({"type":"session_meta","payload":{"id":"parent","forked_from_id":"fork"}}),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"Cycle"):
            self.run_delete()
        self.server.call.assert_not_called()

    def test_reviewed_scope_cannot_expand(self):
        self.add("parent")
        reviewed=self.run_delete(apply=False)
        self.add("fork","parent")
        with self.assertRaisesRegex(RuntimeError,"confirmation"):
            archive.delete_archived(self.server,self.home,["parent"],True,True,self.audit,self.backup,reviewed)
        self.server.call.assert_not_called()

    def test_backup_includes_versioned_history_and_desktop_state(self):
        self.add("parent")
        history=self.home/"thread_history_1.sqlite"
        with closing(sqlite3.connect(history)) as db:
            db.execute("CREATE TABLE items (body TEXT)")
            db.execute("INSERT INTO items VALUES ('history')")
            db.commit()
        state=self.home/".codex-global-state.json"
        state.write_text('{"thread-project-assignments":{}}',encoding="utf-8")
        self.run_delete()
        with closing(sqlite3.connect(self.backup/history.name)) as db:
            self.assertEqual(db.execute("SELECT body FROM items").fetchone()[0],"history")
        self.assertEqual((self.backup/state.name).read_text(encoding="utf-8"),state.read_text(encoding="utf-8"))

    def test_new_descendant_after_first_deletion_is_preserved(self):
        self.add("parent"); self.add("fork","parent")
        def change(method,params):
            result=self.rpc_delete(method,params)
            if params["threadId"]=="fork":
                self.add("new","parent",spawn=True)
            return result
        self.server.call.side_effect=change
        with self.assertRaisesRegex(RuntimeError,"New dependency"):
            self.run_delete()
        self.assertEqual(set(archive.catalog(self.home)[0]),{"parent","new"})
        self.assertEqual(self.server.call.call_count,1)

    def test_missing_rollout_requires_review_and_backs_up_metadata(self):
        self.add("parent"); self.add("unrelated")
        (self.home/"archived_sessions/parent.jsonl").unlink()
        with self.assertRaisesRegex(ValueError,"allow-missing-rollouts"):
            self.run_delete()
        self.server.call.assert_not_called()
        plan = archive.delete_archived(self.server,self.home,["parent"],True,True,self.audit,
                                       self.backup,allow_missing_rollouts=True)
        self.assertEqual(plan["missing_rollout_ids"],["parent"])
        self.assertEqual(set(archive.catalog(self.home)[0]),{"unrelated"})
        saved = json.loads((self.backup/"manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["missing_rollout_ids"],["parent"])
        with closing(sqlite3.connect(self.backup/"state_5.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM threads").fetchone()[0],2)

    def test_missing_rollout_reappearing_after_backup_stops_deletion(self):
        self.add("parent")
        path=self.home/"archived_sessions/parent.jsonl"
        content=path.read_text(encoding="utf-8"); path.unlink()
        original=archive.backup
        def change(*args):
            original(*args); path.write_text(content,encoding="utf-8")
        with patch.object(archive,"backup",side_effect=change),self.assertRaisesRegex(RuntimeError,"changed"):
            archive.delete_archived(self.server,self.home,["parent"],True,True,self.audit,
                                    self.backup,allow_missing_rollouts=True)
        self.server.call.assert_not_called()

    def test_missing_rollout_option_never_allows_active_or_cloud_rows(self):
        self.add("active",archived=0); self.add("cloud",source="chatgpt")
        for key in ("active","cloud"):
            (self.home/"archived_sessions"/(key+".jsonl")).unlink()
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,"Active or unsupported"):
                archive.delete_archived(self.server,self.home,[key],True,True,self.audit,
                                        self.backup,allow_missing_rollouts=True)
        self.server.call.assert_not_called()

    def test_missing_rollout_outside_home_never_reaches_rpc(self):
        self.add("parent")
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE threads SET rollout_path=? WHERE id='parent'",
                       (str(self.home.parent/"outside-cleaner-home.jsonl"),)); db.commit()
        with self.assertRaisesRegex(ValueError,"outside Codex home"):
            archive.delete_archived(self.server,self.home,["parent"],True,True,self.audit,
                                    self.backup,allow_missing_rollouts=True)
        self.server.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
