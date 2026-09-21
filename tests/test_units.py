import unittest

from ideswap import android, editorconfig, jsonc, vscode


class JsoncTest(unittest.TestCase):
    def test_comments_and_trailing_commas(self):
        data, had = jsonc.loads('{\n // c\n "a": "http://x//y", /* b */ "b": [1, 2,],\n}')
        self.assertEqual(data, {"a": "http://x//y", "b": [1, 2]})
        self.assertTrue(had)

    def test_escaped_quotes_and_bom(self):
        data, had = jsonc.loads('﻿{"a": "say \\"hi\\" // not a comment"}')
        self.assertEqual(data["a"], 'say "hi" // not a comment')
        self.assertFalse(had)


class ManifestTest(unittest.TestCase):
    SRC = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
           '    package="com.ex.app"\n    android:versionCode="3">\n'
           '    <uses-sdk android:minSdkVersion="15" android:targetSdkVersion="19" android:maxSdkVersion="30"/>\n'
           '    <!-- keep me -->\n'
           '</manifest>\n')

    def test_strip_keeps_everything_else(self):
        out, removed = android.strip_for_agp(self.SRC)
        self.assertEqual(removed, ["package", "minSdkVersion", "targetSdkVersion"])
        self.assertNotIn("package=", out)
        for kept in ('android:versionCode="3"', 'android:maxSdkVersion="30"', "<!-- keep me -->"):
            self.assertIn(kept, out)

    def test_resolve_class(self):
        self.assertEqual(android.resolve_class(".Main", "a.b"), "a.b.Main")
        self.assertEqual(android.resolve_class("Main", "a.b"), "a.b.Main")
        self.assertEqual(android.resolve_class("x.y.Main", "a.b"), "x.y.Main")


class GradleTaskParseTest(unittest.TestCase):
    def test_command_with_args(self):
        t = {"label": "b", "command": ".\\gradlew.bat assembleDebug", "args": ["-x", "lint", "--info"],
             "options": {"cwd": "${workspaceFolder}/android"}}
        self.assertEqual(vscode.gradle_of_task(t), (["assembleDebug"], "-x lint --info", "android"))

    def test_vscode_gradle_extension_task(self):
        t = {"type": "gradle", "script": "app:build", "args": "--stacktrace"}
        self.assertEqual(vscode.gradle_of_task(t), (["app:build"], "--stacktrace", ""))

    def test_non_gradle(self):
        self.assertIsNone(vscode.gradle_of_task({"label": "x", "command": "npm", "args": ["test"]}))


class EditorConfigTest(unittest.TestCase):
    def test_settings_mapping(self):
        sections, unmapped = editorconfig.from_vscode_settings(
            {"editor.tabSize": 2, "editor.insertSpaces": True, "files.eol": "\n",
             "[kotlin]": {"editor.tabSize": 4}, "[cobol]": {"editor.tabSize": 8}})
        self.assertEqual(sections["*"], {"indent_style": "space", "indent_size": "2", "end_of_line": "lf"})
        self.assertEqual(sections["*.{kt,kts}"], {"indent_size": "4"})
        self.assertEqual(unmapped, ["[cobol]"])


if __name__ == "__main__":
    unittest.main()
