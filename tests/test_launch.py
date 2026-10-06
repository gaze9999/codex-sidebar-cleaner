import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import cli as launcher
from test_cleaner_language import EncodedOutput


class LauncherTests(unittest.TestCase):
    def launch(self, inputs, status=0, arguments=(), encoding="utf-8"):
        output = EncodedOutput(encoding)
        with patch.dict(os.environ), patch.object(sys, "argv", ["launch-cli.py", *arguments]), \
             patch.object(sys, "stdout", output), patch.object(sys, "stderr", io.StringIO()), \
             patch.object(launcher, "ROOT", Path("fixture folder")), \
             patch("builtins.input", side_effect=inputs), \
             patch.object(launcher.subprocess, "run", return_value=Mock(returncode=status)) as run:
            os.environ.pop("SIDEBAR_CLEANER_LANG", None)
            result = launcher.main()
            return result, run, output.getvalue()

    def test_every_menu_route_preserves_script_flags_and_language(self):
        cases = (
            (["1", ""], "maintain_sidebar.py", ["--apply", "--review-sidebar-references"]),
            (["2", ""], "clean_archived_conversations.py", ["--interactive"]),
            (["3", "1", ""], "maintain_sidebar.py", ["--apply", "--interactive-sidebar-plan"]),
            (["3", "2", ""], "clean_sidebar_references.py", ["--interactive"]),
            (["3", "3", ""], "plan_sidebar_cleanup.py", ["--interactive"]),
            (["3", "4", ""], "organize_local_threads.py", ["--apply"]),
            (["3", "5", ""], "clean_codex_catalog.py", ["--apply"]),
        )
        for inputs, script, flags in cases:
            with self.subTest(script=script, flags=flags):
                result, run, output = self.launch(inputs)
                self.assertEqual(result, 0)
                run.assert_called_once_with([sys.executable, "-X", "utf8", "-u",
                                             str(Path("fixture folder") / script), "--lang", "zh-TW", *flags],
                                            check=False)
                self.assertIn("修復側邊欄", output)
                self.assertIn("操作已結束", output)

    def test_language_switch_in_both_menus_is_forwarded_to_the_child(self):
        for inputs, language in ((["l", "2", ""], "en"),
                                 (["3", "L", "4", ""], "en"),
                                 (["L", "3", "L", "5", ""], "zh-TW")):
            with self.subTest(inputs=inputs):
                _, run, output = self.launch(inputs)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0][5:7], ["--lang", language])
                self.assertIn("L. Chinese", output)

    def test_startup_arguments_can_select_english(self):
        for arguments in (("en",), ("--lang", "en"), ("--lang=en",)):
            with self.subTest(arguments=arguments):
                _, run, output = self.launch(["1", ""], arguments=arguments)
                self.assertIn("Fix sidebar", output)
                self.assertNotIn("修復側邊欄", output)
                self.assertEqual(run.call_args.args[0][5:7], ["--lang", "en"])

    def test_fallback_remains_english_when_chinese_is_selected_again(self):
        _, run, output = self.launch(["L", "2", ""], encoding="ascii")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][5:7], ["--lang", "en"])
        self.assertIn("Chinese output is unavailable", output)
        output.encode("ascii")

    def test_exit_back_eof_and_invalid_choices_do_not_start_an_operation(self):
        for inputs in (["0"], ["3", "0", "0"], [EOFError()], ["invalid", "0"]):
            with self.subTest(inputs=inputs):
                result, run, _ = self.launch(inputs)
                self.assertEqual(result, 0)
                run.assert_not_called()

    def test_child_status_survives_pause_eof_without_rerunning(self):
        for status, message in ((0, "操作已結束"), (2, "仍有項目待處理"), (7, "結束代碼: 7")):
            with self.subTest(status=status):
                result, run, output = self.launch(["1", EOFError()], status=status)
                self.assertEqual(result, status)
                self.assertEqual(run.call_count, 1)
                self.assertIn(message, output)
                self.assertEqual("記錄檔:" in output, status != 0)


if __name__ == "__main__":
    unittest.main()
