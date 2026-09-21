import json
import unittest

from ideswap import jsonc

from .helpers import TempCase, cli, make_tree, snapshot

MANIFEST = """
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application android:label="Demo">
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

GRADLE_ANDROID = {
    "settings.gradle.kts": 'rootProject.name = "Demo"\ninclude(":app")\n',
    "build.gradle.kts": 'plugins {\n    id("com.android.application") version "9.3.0" apply false\n}\n',
    "app/build.gradle.kts": """
        plugins {
            id("com.android.application")
        }
        android {
            namespace = "com.demo"
            defaultConfig {
                applicationId = "com.demo.app"
            }
        }
    """,
    "app/src/main/AndroidManifest.xml": MANIFEST,
    "app/src/main/java/com/demo/MainActivity.kt": "package com.demo\nclass MainActivity\n",
    "gradlew": "#!/bin/sh\n",
    "local.properties": "sdk.dir=/fake/sdk\n",
}


class VsCodeFirstRoundTrip(TempCase):
    """A project set up in VS Code goes to Android Studio and back without losing anything."""

    def setUp(self):
        super().setUp()
        make_tree(self.tmp, {
            **GRADLE_ANDROID,
            ".vscode/tasks.json": """
                {
                    // my tasks
                    "version": "2.0.0",
                    "tasks": [
                        {"label": "Release build", "type": "shell", "command": "./gradlew",
                         "args": [":app:assembleRelease", "--stacktrace"]},
                        {"label": "Lint JS", "type": "shell", "command": "npm", "args": ["run", "lint"]},
                    ]
                }
            """,
            ".vscode/settings.json": '{ "editor.tabSize": 2, "editor.insertSpaces": true, "kotlin.foo": 1 }\n',
            ".vscode/launch.json": '{"version": "0.2.0", "configurations": ['
                                   '{"type": "node", "request": "launch", "name": "Node thing"}]}\n',
        })
        self.original = snapshot(self.tmp)

    def test_to_studio_then_back(self):
        out = cli("to-studio", self.tmp)
        rc = self.read(".idea/runConfigurations/Release_build.xml")
        self.assertIn('<option value=":app:assembleRelease" />', rc)
        self.assertIn('name="scriptParameters" value="--stacktrace"', rc)
        self.assertIn("indent_size = 2", self.read(".editorconfig"))
        self.assertIn("Lint JS", out)          # reported as kept
        self.assertIn("Node thing", out)

        cli("to-vscode", self.tmp)
        after = snapshot(self.tmp)
        for name in (".vscode/tasks.json", ".vscode/settings.json", ".vscode/launch.json"):
            self.assertEqual(after[name], self.original[name], f"{name} changed on the round trip")

        before_sync = snapshot(self.tmp)
        self.assertIn("none (already in sync)", cli("sync", self.tmp))
        self.assertEqual(snapshot(self.tmp), before_sync)

        cli("undo", self.tmp)
        cli("undo", self.tmp)
        self.assertEqual(snapshot(self.tmp), self.original)
        self.assertFalse((self.tmp / ".ideswap").exists())


class StudioFirstRoundTrip(TempCase):
    """A project set up in Android Studio goes to VS Code and back without losing anything."""

    def setUp(self):
        super().setUp()
        make_tree(self.tmp, {
            **GRADLE_ANDROID,
            ".idea/runConfigurations/Bundle.xml": """
                <component name="ProjectRunConfigurationManager">
                  <configuration default="false" name="Bundle" type="GradleRunConfiguration" factoryName="Gradle">
                    <ExternalSystemSettings>
                      <option name="externalProjectPath" value="$PROJECT_DIR$" />
                      <option name="scriptParameters" value="--offline" />
                      <option name="taskNames"><list><option value=":app:bundleRelease" /></list></option>
                    </ExternalSystemSettings>
                  </configuration>
                </component>
            """,
            ".idea/workspace.xml": """
                <project version="4">
                  <component name="RunManager" selected="Android App.app">
                    <configuration name="app" type="AndroidRunConfigurationType" factoryName="Android App">
                      <module name="Demo.app.main" />
                    </configuration>
                    <configuration name="Profile" type="AndroidTestRunConfigurationType" factoryName="x" />
                    <configuration default="true" type="Application" factoryName="Application" />
                  </component>
                </project>
            """,
            ".idea/gradle.xml": '<project version="4"><component name="GradleSettings"><option '
                                'name="gradleJvm" value="#GRADLE_LOCAL_JAVA_HOME" /></component></project>\n',
            ".idea/codeStyles/codeStyleConfig.xml": '<component name="ProjectCodeStyleConfiguration"><state>'
                                                    '<option name="USE_PER_PROJECT_SETTINGS" value="true" />'
                                                    '</state></component>\n',
            ".idea/codeStyles/Project.xml": '<component name="ProjectCodeStyleConfiguration"><code_scheme '
                                            'name="Project"><codeStyleSettings language="kotlin"><indentOptions>'
                                            '<option name="INDENT_SIZE" value="2" /></indentOptions>'
                                            '</codeStyleSettings></code_scheme></component>\n',
        })
        self.original = snapshot(self.tmp)

    def test_to_vscode_then_back(self):
        out = cli("to-vscode", self.tmp)
        tasks, _ = jsonc.loads(self.read(".vscode/tasks.json"))
        by_label = {t["label"]: t for t in tasks["tasks"]}
        self.assertEqual(by_label["Bundle"]["args"], [":app:bundleRelease", "--offline"])
        self.assertEqual(by_label["Run app on device"]["args"][-1], "com.demo.app/com.demo.MainActivity")
        self.assertEqual(by_label["Build debug APK"]["args"], [":app:assembleDebug"])
        launch, _ = jsonc.loads(self.read(".vscode/launch.json"))
        self.assertEqual(launch["configurations"][0]["apkFile"],
                         "${workspaceFolder}/app/build/outputs/apk/debug/app-debug.apk")
        self.assertIn("indent_size = 2", self.read(".editorconfig"))
        self.assertIn("Profile", out)                     # kept, no VS Code equivalent
        self.assertIn("#GRADLE_LOCAL_JAVA_HOME", out)     # kept, not a path
        self.assertIn("fwcd.kotlin", self.read(".vscode/extensions.json"))

        cli("to-studio", self.tmp)
        after = snapshot(self.tmp)
        idea_files = {k for k in after if k.startswith(".idea/")}
        self.assertEqual(idea_files, {k for k in self.original if k.startswith(".idea/")},
                         "generated default tasks must not flow back into .idea")
        for k in idea_files:
            self.assertEqual(after[k], self.original[k])

        cli("undo", self.tmp)
        cli("undo", self.tmp)
        self.assertEqual(snapshot(self.tmp), self.original)


class DryRunAndDest(TempCase):
    def test_dry_run_writes_nothing(self):
        make_tree(self.tmp / "p", GRADLE_ANDROID)
        before = snapshot(self.tmp)
        out = cli("sync", self.tmp / "p", "--dry-run")
        self.assertIn("DRY RUN", out)
        self.assertIn(".vscode/tasks.json", out)
        self.assertEqual(snapshot(self.tmp), before)

    def test_dest_copies_and_skips_build_outputs(self):
        make_tree(self.tmp / "src", {**GRADLE_ANDROID, "app/build/intermediates/x.bin": b"\0",
                                     ".gradle/cache": "x"})
        cli("to-vscode", self.tmp / "src", "--dest", self.tmp / "dst")
        dst = snapshot(self.tmp / "dst")
        self.assertIn("app/src/main/AndroidManifest.xml", dst)
        self.assertIn(".vscode/tasks.json", dst)
        self.assertNotIn("app/build/intermediates/x.bin", dst)
        self.assertNotIn(".gradle/cache", dst)
        self.assertNotIn(".vscode/tasks.json", snapshot(self.tmp / "src"))


class UndoSafety(TempCase):
    def test_undo_skips_files_edited_afterwards(self):
        make_tree(self.tmp, GRADLE_ANDROID)
        cli("to-vscode", self.tmp)
        tasks = self.tmp / ".vscode" / "tasks.json"
        tasks.write_text("{}", encoding="utf-8")
        out = cli("undo", self.tmp)
        self.assertIn("skipped .vscode/tasks.json", out)
        self.assertEqual(tasks.read_text(encoding="utf-8"), "{}")
        self.assertFalse((self.tmp / ".vscode" / "launch.json").exists())


if __name__ == "__main__":
    unittest.main()
