import argparse
import builtins
import io
import os
import sys
import unittest
from unittest.mock import patch

import cleaner_language as ui


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


if __name__ == "__main__":
    unittest.main()
