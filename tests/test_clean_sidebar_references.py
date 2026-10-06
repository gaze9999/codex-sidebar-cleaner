import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import clean_sidebar_references as cleaner


OLD = "g-p-" + "1" * 32
LIVE = "g-p-" + "2" * 32


def state():
    return {"unrelated": {"active_threads": ["keep"]}, "electron-persisted-atom-state": {
        "chatgpt-sidebar-state-v1": {"account": {"projects": [{"id": LIVE}], "pinnedProjects": []}},
        cleaner.KEY: {
            "account": {"sections": [
                {"id": "a", "name": "Section", "itemKeys": ["chatgpt:project:" + OLD,
                    "codex:project:local", "chatgpt:project:" + LIVE, "chatgpt:conversation:keep"]},
                {"id": "b", "name": "Other", "itemKeys": ["chatgpt:project:" + OLD]},
            ], "collapsedSectionIds": ["a"], "sectionOrder": ["pinned", "custom:a", "custom:b"]},
            "another-account": {"sections": [{"id": "a", "itemKeys": ["chatgpt:project:" + OLD]}]},
        },
    }}


class SidebarReferencesTests(unittest.TestCase):
    def test_preview_and_exact_removal_preserve_accounts_sections_and_conversations(self):
        before = state()
        snapshot = copy.deepcopy(before)
        rows = cleaner.candidates(before, "account")
        self.assertEqual([row["project_id"] for row in rows], [OLD, OLD])
        plan = cleaner.make_plan(before, "account", [OLD])
        after = cleaner.transform(before, plan)
        self.assertEqual(before, snapshot)
        expected = copy.deepcopy(before)
        sections = cleaner.account_sections(expected, "account")
        for section in sections:
            section["itemKeys"].remove("chatgpt:project:" + OLD)
        self.assertEqual(after, expected)
        self.assertEqual(len(plan["references"]), 2)

    def test_changed_scope_and_unconfirmed_plan_are_rejected(self):
        before = state()
        plan = cleaner.make_plan(before, "account", [OLD])
        changed = copy.deepcopy(before)
        cleaner.account_sections(changed, "account")[1]["itemKeys"] = []
        with self.assertRaisesRegex(RuntimeError, "changed"):
            cleaner.transform(changed, plan)
        plan["confirmed_unwanted_sidebar_references"] = False
        with self.assertRaises(ValueError):
            cleaner.transform(before, plan)
        with self.assertRaises(ValueError):
            cleaner.make_plan(before, "account", [OLD, OLD])
        cleaner.account_sections(before, "account")[0]["itemKeys"].append(42)
        with self.assertRaises(ValueError):
            cleaner.candidates(before, "account")

    def test_apply_backups_original_bytes_and_verifies_scoped_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state.json"
            raw = json.dumps(state(), indent=2).encode()
            path.write_bytes(raw)
            plan = cleaner.make_plan(state(), "account", [OLD])
            backup = root / "backup" / "original.json"
            audit = Mock()
            with patch.object(cleaner, "app_running", return_value=False):
                cleaner.apply_plan(path, plan, backup, audit)
            self.assertEqual(backup.read_bytes(), raw)
            self.assertEqual(json.loads(path.read_bytes()), cleaner.transform(state(), plan))
            self.assertFalse(list(root.glob("*.tmp")))
            self.assertFalse(audit.record.call_args.kwargs["cloud_modified"])
            self.assertFalse(audit.record.call_args.kwargs["catalog_modified"])

    def test_restarted_app_or_concurrent_state_write_cannot_be_overwritten(self):
        for restart in (True, False):
            with self.subTest(restart=restart), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / "state.json"
                original = json.dumps(state()).encode()
                path.write_bytes(original)
                changed = state()
                changed["unrelated"]["active_threads"].append("new")
                current = json.dumps(changed).encode()
                calls = 0

                def running():
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        if not restart:
                            path.write_bytes(current)
                        return restart
                    return False

                with patch.object(cleaner, "app_running", side_effect=running):
                    with self.assertRaisesRegex(RuntimeError, "changed"):
                        cleaner.apply_plan(path, cleaner.make_plan(state(), "account", [OLD]),
                                           root / "backup.json", Mock())
                self.assertEqual(path.read_bytes(), original if restart else current)
                self.assertEqual((root / "backup.json").read_bytes(), original)
                self.assertFalse(list(root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
