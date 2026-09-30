import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plan_sidebar_cleanup import archive_status, build_plan, load_reviewed_plan, normalize
import plan_sidebar_cleanup as planner


A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"


def rows():
    return {A: {"title": "Node.js old", "source_kind": "chatgpt", "project_id": "old"},
            B: {"title": "Keep", "source_kind": "chatgpt", "project_id": None},
            C: {"title": "Ｎｏｄｅ.js old", "source_kind": "chatgpt", "project_id": "other"}}


class ScreenshotPlanTests(unittest.TestCase):
    def test_non_windows_interactive_review_uses_verified_titles_without_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            titles = root / "titles.json"
            titles.write_text(json.dumps(["Keep", "Node.js old"]), encoding="utf-8")
            with patch.object(planner.sys, "argv", ["plan_sidebar_cleanup.py", "--interactive", "--output-dir", str(root)]), \
                 patch.object(planner.sys, "platform", "darwin"), \
                 patch.object(planner, "inventory", return_value=(rows(), {B}, "cloud:account")), \
                 patch("builtins.input", side_effect=[str(titles), "Keep", "1"]), \
                 patch.object(planner.subprocess, "run") as ocr:
                self.assertEqual(planner.main(), 0)
            ocr.assert_not_called()
            plan = json.loads(next((root / "logs").glob("screenshot-plan-*/review-plan.json")).read_text(encoding="utf-8"))
            self.assertEqual(plan["selected_ids"], [A])
            self.assertEqual(plan["keep_through_id"], B)

    def test_ambiguous_keep_boundary_cannot_choose_an_arbitrary_id(self):
        current = rows()
        current[C]["title"] = "Keep"
        with self.assertRaises(ValueError):
            build_plan([{"name": "image", "lines": ["Keep", "Node.js old"]}], current,
                       {B, C}, keep_through="Keep")

    def test_native_listing_verifies_only_returned_ids_and_rejects_acknowledgements(self):
        plan = {"host_id": "cloud:account", "entries": [
            {"thread_id": A, "title": "Old", "project_id": "project"},
            {"thread_id": B, "title": "Older", "project_id": None}]}
        status = archive_status(plan, None)
        self.assertEqual(status["pending_ids"], [A, B])
        snapshot = {"schema_version": 1, "source": "codex_app.list_archived_threads",
                    "expected_chatgpt_host": "cloud:account", "captured_at": datetime.now(timezone.utc).isoformat(),
                    "conversations": [{"id": A, "title": "Old", "project_id": "project", "archived": True}]}
        status = archive_status(plan, snapshot)
        self.assertEqual(status["verified_ids"], [A])
        self.assertEqual(status["pending_ids"], [B])
        self.assertFalse(status["archive_listing_verified"])
        for change in ({"expected_chatgpt_host": "other"}, {"source": "archive_acknowledged"},
                       {"captured_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()},
                       {"conversations": [{"id": A, "archived": False}]},
                       {"conversations": snapshot["conversations"] * 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                archive_status(plan, dict(snapshot, **change))

    def test_reviewed_plan_rejects_other_account_protected_or_unmatched_ids(self):
        plan = build_plan([{"name": "image", "lines": ["Node.js old"]}], rows(), {B},
                          reviewed_ids=[A], confirmed=True)
        plan["expected_chatgpt_host"] = "cloud:account"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.json"
            path.write_text(json.dumps(plan), encoding="utf-8")
            self.assertEqual(load_reviewed_plan(path, "cloud:account")["entries"][0]["thread_id"], A)
            with self.assertRaises(ValueError):
                load_reviewed_plan(path, "other")
            for change in ({"selected_ids": [B]}, {"selected_ids": [A, A]},
                           {"confirmed_unwanted_sidebar_entries": False}, {"matches": []}):
                path.write_text(json.dumps(dict(plan, **change)), encoding="utf-8")
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_reviewed_plan(path, "cloud:account")

    def test_duplicates_boundary_and_current_catalog_are_preserved(self):
        images = [{"name": "first", "sha256": "same", "lines": ["Keep", "Boundary", "Node.js old"]},
                  {"name": "duplicate", "sha256": "same", "lines": ["Keep", "Boundary", "Node.js old"]},
                  {"name": "other", "lines": ["N o d e . j s old", "Keep"]}]
        before = copy.deepcopy(images)
        plan = build_plan(images, rows(), {B}, keep_through="Boundary")
        self.assertEqual(plan["unique_images"], 2)
        self.assertEqual({item["thread_id"] for item in plan["matches"]}, {A, C})
        self.assertEqual(len(plan["same_title_candidates"]), 1)
        self.assertEqual(plan["archive_requests"], [])
        self.assertEqual(plan["automatic_removals"], [])
        self.assertEqual(images, before)
        self.assertEqual(normalize("Ｎｏｄｅ .js OLD"), normalize("Node.js old"))

    def test_fuzzy_suggestions_cannot_enter_reviewed_selection(self):
        images = [{"name": "image", "lines": ["Node.js ol"]}]
        plan = build_plan(images, rows(), set())
        self.assertEqual(plan["matches"], [])
        self.assertTrue(plan["unmatched"][0]["suggestions"])
        with self.assertRaises(ValueError):
            build_plan(images, rows(), set(), reviewed_ids=[A], confirmed=True)
        plan = build_plan(images, rows(), set(), corrections={"image:1": "Node.js old"},
                          reviewed_ids=[A], confirmed=True)
        self.assertEqual(plan["archive_requests"][0]["arguments"], {"source": "chatgpt", "threadId": A, "archived": True})
        self.assertFalse(plan["restore_requests"][0]["arguments"]["archived"])

    def test_protected_ids_confirmation_and_boundary_are_required(self):
        images = [{"name": "image", "lines": ["Node.js old", "Keep"]}]
        for kwargs in ({"reviewed_ids": [B], "confirmed": True},
                       {"reviewed_ids": [A], "confirmed": False},
                       {"reviewed_ids": [A, A], "confirmed": True},
                       {"keep_through": "Missing"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_plan(images, rows(), {B}, **kwargs)
        with self.assertRaises(ValueError):
            build_plan([{"name": "bad", "lines": [123]}], rows(), set())


if __name__ == "__main__":
    unittest.main()
