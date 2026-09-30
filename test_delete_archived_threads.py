from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from clean_codex_catalog import Audit
import delete_archived_threads as archive


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
