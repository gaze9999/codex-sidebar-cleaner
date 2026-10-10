import argparse
import builtins
import importlib.util
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import cleaner_language as ui
import cli as launcher
import runtime_paths


class EncodedOutput(io.StringIO):
    def __init__(self, encoding):
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self):
        return self._encoding


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ)
        self.environment.start()
        os.environ.pop("SIDEBAR_CLEANER_LANG", None)
        self.output = patch.object(sys, "stdout", EncodedOutput("utf-8"))
        self.error = patch.object(sys, "stderr", EncodedOutput("utf-8"))
        self.output.start()
        self.error.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.output.stop)
        self.addCleanup(self.error.stop)

    def test_default_and_explicit_language_format_the_same_fields(self):
        self.assertEqual(ui.language(), "zh-TW")
        self.assertEqual(ui.text("共 {count} 筆", "{count} entries", count=3), "共 3 筆")
        os.environ["SIDEBAR_CLEANER_LANG"] = "en"
        self.assertEqual(ui.text("共 {count} 筆", "{count} entries", count=3), "3 entries")
        os.environ["SIDEBAR_CLEANER_LANG"] = "unknown"
        self.assertEqual(ui.language(), "zh-TW")

    def test_console_input_is_preserved_with_an_old_launcher_environment(self):
        original = builtins.input
        with patch.dict(os.environ, SIDEBAR_CLEANER_GUI="1"):
            ui.configure(argparse.Namespace(lang="zh-TW"))
            self.assertIs(builtins.input, original)
            self.assertEqual(ui.language(), "zh-TW")

    def test_either_output_stream_can_require_english_fallback(self):
        for stream in ("stdout", "stderr"):
            for encoding in ("ascii", "cp437", "unknown-codec"):
                with self.subTest(stream=stream, encoding=encoding), \
                     patch.object(sys, stream, EncodedOutput(encoding)):
                    args = argparse.Namespace(lang="zh-TW")
                    ui.configure(args)
                    self.assertEqual(args.lang, "en")
                    self.assertEqual(os.environ["SIDEBAR_CLEANER_LANG"], "en")
                    self.assertEqual(ui.event_message("started"), "Inspection started")

    def test_utf8_cp950_and_unicode_string_streams_allow_chinese(self):
        for encoding in ("utf-8", "cp950", None):
            with self.subTest(encoding=encoding), patch.object(sys, "stdout", EncodedOutput(encoding)):
                ui.configure(argparse.Namespace(lang="zh-TW"))
                self.assertEqual(ui.event_message("started"), "開始檢查")

    def test_explicit_cli_language_also_selects_help_language(self):
        for flags, expected in ((["--lang", "en"], "English description"),
                                (["--lang=en"], "English description"),
                                (["--lang", "zh-TW"], "中文說明")):
            with self.subTest(flags=flags), patch.object(sys, "argv", ["tool.py", *flags]):
                os.environ["SIDEBAR_CLEANER_LANG"] = "en" if "zh-TW" in flags else "zh-TW"
                parser = ui.parser("中文說明", "English description")
                self.assertIn(expected, parser.format_help())
                args = parser.parse_args()
                ui.configure(args)
                self.assertEqual(args.lang, "zh-TW" if "zh-TW" in flags else "en")

    def test_ascii_output_escapes_user_titles_without_crashing(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", write_through=True)
        with patch.object(sys, "stdout", stream):
            ui.configure(argparse.Namespace(lang="zh-TW"))
            print(ui.text("對話: {title}", "Chat: {title}", title="中文對話"))
        self.assertTrue(raw.getvalue().startswith(b"Chat: "))
        self.assertIn(b"\\u4e2d", raw.getvalue())
        raw.getvalue().decode("ascii")


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

    def test_source_and_packaged_output_roots_do_not_follow_the_current_directory(self):
        self.assertEqual(runtime_paths.output_root(), Path(runtime_paths.__file__).resolve().parents[1])
        root = Path.cwd() / "fixture"
        for platform, name in (("win32", "CodexSidebarCleaner.exe"), ("darwin", "CodexSidebarCleaner")):
            with self.subTest(platform=platform), patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "platform", platform), \
                 patch.object(sys, "executable", str(root / "runtime/CodexSidebarCleaner" / name)):
                self.assertEqual(runtime_paths.output_root(), root)

    def test_packaged_routes_use_the_cli_binary_and_preserve_arguments(self):
        root = Path("package")
        for platform, name in (("win32", "CodexSidebarCleaner.exe"), ("darwin", "CodexSidebarCleaner")):
            with self.subTest(platform=platform), patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "platform", platform), patch.object(runtime_paths, "ROOT", root):
                command = runtime_paths.tool_command(Path("app/maintain_sidebar.py"), "--lang", "en", "--help")
                self.assertEqual(command, [str(root / "runtime/CodexSidebarCleaner" / name),
                                           "--tool", "maintain_sidebar.py", "--lang", "en", "--help"])


entry_spec = importlib.util.spec_from_file_location(
    "entry", Path(__file__).resolve().parents[1] / "launch-cli.py")
entry = importlib.util.module_from_spec(entry_spec)
entry_spec.loader.exec_module(entry)


class EntryTests(unittest.TestCase):
    def launch(self, arguments):
        return (patch.object(sys, "argv", ["CodexSidebarCleaner", *arguments]),
                patch.object(sys, "stdin", None), patch.object(sys, "stdout", None),
                patch.object(sys, "stderr", None), patch.object(sys, "path", list(sys.path)))

    def test_default_and_explicit_cli_preserve_language_and_exit_status(self):
        for arguments in (["en"], ["--cli", "en"]):
            with self.subTest(arguments=arguments):
                argv, stdin, stdout, stderr, path = self.launch(arguments)
                with argv, stdin, stdout, stderr, path, patch("cli.main", return_value=7) as launch:
                    self.assertEqual(entry.main(), 7)
                    self.assertEqual(sys.argv[1:], ["en"])
                    launch.assert_called_once_with()

    def test_allowlisted_tools_keep_their_arguments_and_main_entry(self):
        tools = ("cli.py", "clean_archived_conversations.py", "clean_codex_catalog.py",
                 "clean_sidebar_references.py", "compare_cloud_catalog.py", "delete_archived_threads.py",
                 "maintain_sidebar.py", "organize_local_threads.py", "plan_sidebar_cleanup.py")
        for tool in tools:
            with self.subTest(tool=tool):
                argv, stdin, stdout, stderr, path = self.launch(["--tool", tool, "--lang", "en", "--help"])
                with argv, stdin, stdout, stderr, path, patch.object(entry.runpy, "run_path") as run:
                    self.assertEqual(entry.main(), 0)
                    script = str(Path(entry.__file__).resolve().parent / "app" / tool)
                    self.assertEqual(sys.argv, [script, "--lang", "en", "--help"])
                    run.assert_called_once_with(script, run_name="__main__")

    def test_packaged_tool_uses_the_bundled_source_directory(self):
        source = Path("fixture bundle")
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform):
                argv, stdin, stdout, stderr, path = self.launch(["--tool", "maintain_sidebar.py", "--help"])
                with argv, stdin, stdout, stderr, path, \
                     patch.object(sys, "frozen", True, create=True), \
                     patch.object(sys, "_MEIPASS", str(source), create=True), \
                     patch.object(sys, "platform", platform), \
                     patch.object(entry.runpy, "run_path") as run:
                    self.assertEqual(entry.main(), 0)
                    script = str(source / "app" / "maintain_sidebar.py")
                    self.assertEqual(sys.argv, [script, "--help"])
                    run.assert_called_once_with(script, run_name="__main__")

    def test_unknown_or_missing_tool_is_rejected_before_execution(self):
        for arguments in (["--tool"], ["--tool", "../cli.py"], ["--tool", "unknown.py"]):
            with self.subTest(arguments=arguments):
                argv, stdin, stdout, stderr, path = self.launch(arguments)
                with argv, stdin, stdout, stderr, path, patch.object(entry.runpy, "run_path") as run:
                    with self.assertRaisesRegex(SystemExit, "Unknown bundled tool"):
                        entry.main()
                    run.assert_not_called()

    def test_tool_exit_status_is_preserved(self):
        argv, stdin, stdout, stderr, path = self.launch(["--tool", "maintain_sidebar.py", "--help"])
        with argv, stdin, stdout, stderr, path, patch.object(entry.runpy, "run_path", side_effect=SystemExit(2)):
            with self.assertRaises(SystemExit) as result:
                entry.main()
            self.assertEqual(result.exception.code, 2)

    def test_self_test_uses_only_the_cli_runtime_check(self):
        argv, stdin, stdout, stderr, path = self.launch(["--self-test"])
        with argv, stdin, stdout, stderr, path, \
             patch.object(entry.sqlite3, "connect", wraps=entry.sqlite3.connect) as connect, \
             patch.object(launcher, "main") as menu:
            self.assertEqual(entry.main(), 0)
            connect.assert_called_once_with(":memory:")
            menu.assert_not_called()

    def test_removed_options_are_rejected_before_reading_input(self):
        for flag in ("--web", "--gui", "--window", "--web-smoke", "--window-smoke"):
            with self.subTest(flag=flag):
                argv, stdin, stdout, stderr, path = self.launch([flag])
                with argv, stdin, stdout, stderr, path, patch("builtins.input") as read:
                    with self.assertRaises(SystemExit) as result:
                        entry.main()
                    self.assertEqual(result.exception.code, 2)
                    read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
