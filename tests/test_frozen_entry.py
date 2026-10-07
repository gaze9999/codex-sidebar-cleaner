from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import frozen_entry


class FrozenEntryTests(unittest.TestCase):
    def launch(self, arguments):
        return (patch.object(sys, "argv", ["CodexSidebarCleaner", *arguments]),
                patch.object(sys, "stdin", None), patch.object(sys, "stdout", None),
                patch.object(sys, "stderr", None), patch.object(sys, "path", list(sys.path)))

    def test_default_and_explicit_cli_preserve_language_and_exit_status(self):
        for arguments in (["en"], ["--cli", "en"]):
            with self.subTest(arguments=arguments):
                argv, stdin, stdout, stderr, path = self.launch(arguments)
                with argv, stdin, stdout, stderr, path, patch("cli.main", return_value=7) as launch:
                    self.assertEqual(frozen_entry.main(), 7)
                    self.assertEqual(sys.argv[1:], ["en"])
                    launch.assert_called_once_with()

    def test_allowlisted_tools_keep_their_arguments_and_main_entry(self):
        tools = ("cli.py", "clean_archived_conversations.py", "clean_codex_catalog.py",
                 "clean_sidebar_references.py", "compare_cloud_catalog.py", "delete_archived_threads.py",
                 "maintain_sidebar.py", "organize_local_threads.py", "plan_sidebar_cleanup.py")
        for tool in tools:
            with self.subTest(tool=tool):
                argv, stdin, stdout, stderr, path = self.launch(["--tool", tool, "--lang", "en", "--help"])
                with argv, stdin, stdout, stderr, path, patch.object(frozen_entry.runpy, "run_path") as run:
                    self.assertEqual(frozen_entry.main(), 0)
                    script = str(Path(frozen_entry.__file__).resolve().parent / tool)
                    self.assertEqual(sys.argv, [script, "--lang", "en", "--help"])
                    run.assert_called_once_with(script, run_name="__main__")

    def test_unknown_or_missing_tool_is_rejected_before_execution(self):
        for arguments in (["--tool"], ["--tool", "../cli.py"], ["--tool", "unknown.py"]):
            with self.subTest(arguments=arguments):
                argv, stdin, stdout, stderr, path = self.launch(arguments)
                with argv, stdin, stdout, stderr, path, patch.object(frozen_entry.runpy, "run_path") as run:
                    with self.assertRaisesRegex(SystemExit, "Unknown bundled tool"):
                        frozen_entry.main()
                    run.assert_not_called()

    def test_tool_exit_status_is_preserved(self):
        argv, stdin, stdout, stderr, path = self.launch(["--tool", "maintain_sidebar.py", "--help"])
        with argv, stdin, stdout, stderr, path, patch.object(frozen_entry.runpy, "run_path", side_effect=SystemExit(2)):
            with self.assertRaises(SystemExit) as result:
                frozen_entry.main()
            self.assertEqual(result.exception.code, 2)

    def test_self_test_uses_only_the_cli_runtime_check(self):
        argv, stdin, stdout, stderr, path = self.launch(["--self-test"])
        with argv, stdin, stdout, stderr, path, patch("packaged_smoke.main", return_value=0) as smoke:
            self.assertEqual(frozen_entry.main(), 0)
            smoke.assert_called_once_with()

    def test_removed_options_are_rejected_before_reading_input(self):
        for flag in ("--web", "--gui", "--window", "--web-smoke", "--window-smoke"):
            with self.subTest(flag=flag):
                argv, stdin, stdout, stderr, path = self.launch([flag])
                with argv, stdin, stdout, stderr, path, patch("builtins.input") as read:
                    with self.assertRaises(SystemExit) as result:
                        frozen_entry.main()
                    self.assertEqual(result.exception.code, 2)
                    read.assert_not_called()
