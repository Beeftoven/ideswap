import unittest

from ideswap import jsonc
from ideswap.detect import detect

from .helpers import TempCase, cli, make_tree, snapshot


class EclipseAdt(TempCase):
    def setUp(self):
        super().setUp()
        make_tree(self.tmp, {
            "App/AndroidManifest.xml": """
                <?xml version="1.0" encoding="utf-8"?>
                <manifest xmlns:android="http://schemas.android.com/apk/res/android"
                    package="com.old.app" android:versionCode="7">
                    <uses-sdk android:minSdkVersion="15" android:targetSdkVersion="19" />
                    <application><activity android:name=".Home"><intent-filter>
                        <action android:name="android.intent.action.MAIN" />
                        <category android:name="android.intent.category.LAUNCHER" />
                    </intent-filter></activity></application>
                </manifest>
            """,
            "App/project.properties": "target=android-19\nandroid.library.reference.1=../Lib\n"
                                      "proguard.config=${sdk.dir}/tools/proguard/proguard-android.txt:proguard-project.txt\n",
            "App/proguard-project.txt": "-keep class x\n",
            "App/.classpath": '<classpath><classpathentry kind="src" path="src"/>'
                              '<classpathentry kind="src" path="gen"/></classpath>\n',
            "App/src/com/old/app/Home.java": "package com.old.app;\npublic class Home {}\n",
            "App/src/com/old/app/IRemote.aidl": "package com.old.app;\ninterface IRemote {}\n",
            "App/res/values/strings.xml": "<resources/>\n",
            "App/assets/data.txt": "x\n",
            "App/libs/helper.jar": b"PK",
            "App/libs/armeabi/libfoo.so": b"\x7fELF",
            "Lib/AndroidManifest.xml": '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                                       'package="com.old.lib"/>\n',
            "Lib/project.properties": "android.library=true\n",
            "Lib/src/com/old/lib/L.java": "package com.old.lib;\nclass L {}\n",
        })
        self.original = snapshot(self.tmp)

    def test_convert_and_undo(self):
        self.assertEqual(detect(self.tmp / "App").kind, "eclipse-adt")
        cli("to-studio", self.tmp / "App")
        b = self.read("App/build.gradle.kts")
        for expected in ('id("com.android.application")', 'namespace = "com.old.app"',
                         'applicationId = "com.old.app"', "minSdk = 15", "targetSdk = 19",
                         'manifest.srcFile("AndroidManifest.xml")', 'java.srcDirs("src")', 'res.srcDirs("res")',
                         'assets.srcDirs("assets")', 'jniLibs.srcDirs("libs")', 'aidl.srcDirs("src")',
                         "aidl = true", '"dir" to "libs"', 'implementation(project(":Lib"))',
                         "isMinifyEnabled = true", '"proguard-project.txt"'):
            self.assertIn(expected, b)
        self.assertNotIn("kotlin", b)
        self.assertIn('id("com.android.library")', self.read("Lib/build.gradle.kts"))
        settings = self.read("App/settings.gradle.kts")
        self.assertIn('include(":Lib")', settings)
        self.assertIn('project(":Lib").projectDir = file("../Lib")', settings)
        self.assertIn('id("com.android.application") version "9.3.0"', settings)
        self.assertIn("gradle-9.5.0-bin.zip", self.read("App/gradle/wrapper/gradle-wrapper.properties"))
        manifest = self.read("App/AndroidManifest.xml")
        self.assertNotIn("package=", manifest)
        self.assertNotIn("minSdkVersion", manifest)
        self.assertIn('android:versionCode="7"', manifest)

        p = detect(self.tmp / "App")
        self.assertEqual(p.kind, "gradle")
        self.assertEqual(p.app_module().launcher, "com.old.app.Home")

        cli("undo", self.tmp / "App")
        self.assertEqual(snapshot(self.tmp), self.original)


class Maven(TempCase):
    def test_multi_module(self):
        make_tree(self.tmp, {
            "pom.xml": """
                <project xmlns="http://maven.apache.org/POM/4.0.0">
                  <groupId>com.acme</groupId><artifactId>parent</artifactId><version>1.2</version>
                  <packaging>pom</packaging>
                  <modules><module>core</module><module>cli</module></modules>
                  <properties><guava.version>33.0-jre</guava.version>
                    <maven.compiler.release>21</maven.compiler.release></properties>
                  <dependencyManagement><dependencies>
                    <dependency><groupId>com.google.guava</groupId><artifactId>guava</artifactId>
                      <version>${guava.version}</version></dependency>
                  </dependencies></dependencyManagement>
                  <repositories><repository><url>https://repo.acme.com/maven</url></repository></repositories>
                </project>
            """,
            "core/pom.xml": """
                <project xmlns="http://maven.apache.org/POM/4.0.0">
                  <parent><groupId>com.acme</groupId><artifactId>parent</artifactId><version>1.2</version></parent>
                  <artifactId>core</artifactId>
                  <dependencies>
                    <dependency><groupId>com.google.guava</groupId><artifactId>guava</artifactId>
                      <exclusions><exclusion><groupId>com.google.code.findbugs</groupId>
                        <artifactId>jsr305</artifactId></exclusion></exclusions></dependency>
                    <dependency><groupId>javax.servlet</groupId><artifactId>servlet-api</artifactId>
                      <version>2.5</version><scope>provided</scope></dependency>
                    <dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>
                      <version>5.11.4</version><scope>test</scope></dependency>
                  </dependencies>
                </project>
            """,
            "core/src/main/java/com/acme/Core.java": "package com.acme;\npublic class Core {}\n",
            "cli/pom.xml": """
                <project xmlns="http://maven.apache.org/POM/4.0.0">
                  <parent><groupId>com.acme</groupId><artifactId>parent</artifactId><version>1.2</version></parent>
                  <artifactId>cli</artifactId>
                  <dependencies><dependency><groupId>com.acme</groupId><artifactId>core</artifactId>
                    <version>${project.version}</version></dependency></dependencies>
                  <build><plugins>
                    <plugin><artifactId>exec-maven-plugin</artifactId>
                      <configuration><mainClass>com.acme.Cli</mainClass></configuration></plugin>
                    <plugin><artifactId>maven-shade-plugin</artifactId></plugin>
                  </plugins></build>
                </project>
            """,
            "cli/src/main/java/com/acme/Cli.java": "package com.acme;\npublic class Cli {}\n",
        })
        out = cli("to-studio", self.tmp)
        settings = self.read("settings.gradle.kts")
        self.assertIn('include(":cli")', settings)
        self.assertIn('include(":core")', settings)
        self.assertIn('maven("https://repo.acme.com/maven")', settings)
        core = self.read("core/build.gradle.kts")
        self.assertIn('implementation("com.google.guava:guava:33.0-jre") {', core)
        self.assertIn('exclude(group = "com.google.code.findbugs", module = "jsr305")', core)
        self.assertIn('compileOnly("javax.servlet:servlet-api:2.5")', core)
        self.assertIn('testImplementation("org.junit.jupiter:junit-jupiter:5.11.4")', core)
        self.assertIn("useJUnitPlatform()", core)
        self.assertIn('JavaVersion.toVersion("21")', core)
        self.assertIn('version = "1.2"', core)
        cli_build = self.read("cli/build.gradle.kts")
        self.assertIn('implementation(project(":core"))', cli_build)
        self.assertIn('mainClass.set("com.acme.Cli")', cli_build)
        self.assertIn("maven-shade-plugin", out)
        self.assertFalse((self.tmp / "build.gradle.kts").exists())


class PlainFolders(TempCase):
    def test_plain_java_with_tests(self):
        make_tree(self.tmp, {
            "code/org/x/App.java": "package org.x;\npublic class App { public static void main(String[] a) {} }\n",
            "code/org/x/Util.java": "package org.x;\nclass Util {}\n",
            "tests/org/x/AppTest.java": "package org.x;\nimport org.junit.jupiter.api.Test;\nclass AppTest {}\n",
            "lib/gson.jar": b"PK",
        })
        self.assertEqual(detect(self.tmp).kind, "plain")
        cli("to-vscode", self.tmp)
        b = self.read("build.gradle.kts")
        self.assertIn('java.setSrcDirs(listOf("code"))', b)
        self.assertIn('java.setSrcDirs(listOf("tests"))', b)
        self.assertIn('mainClass.set("org.x.App")', b)
        self.assertIn('"dir" to "lib"', b)
        self.assertIn("junit-jupiter", b)
        tasks, _ = jsonc.loads(self.read(".vscode/tasks.json"))
        self.assertIn("Run", [t["label"] for t in tasks["tasks"]])

    def test_plain_kotlin_main(self):
        make_tree(self.tmp, {"src/tool/main.kt": "package tool\n\nfun main() {}\n"})
        cli("to-studio", self.tmp)
        b = self.read("build.gradle.kts")
        self.assertIn('id("org.jetbrains.kotlin.jvm")', b)
        self.assertIn('mainClass.set("tool.MainKt")', b)
        self.assertIn('sourceSets["main"].kotlin.setSrcDirs(listOf("src"))', b)
        self.assertIn('id("org.jetbrains.kotlin.jvm") version', self.read("settings.gradle.kts"))

    def test_vscode_java_settings(self):
        make_tree(self.tmp, {
            ".vscode/settings.json": '{"java.project.sourcePaths": ["app/src"], '
                                     '"java.project.referencedLibraries": ["vendor/**/*.jar"]}',
            "app/src/Hello.java": "public class Hello { public static void main(String[] a) {} }\n",
        })
        self.assertEqual(detect(self.tmp).kind, "vscode-java")
        cli("to-studio", self.tmp)
        b = self.read("build.gradle.kts")
        self.assertIn('java.setSrcDirs(listOf("app/src"))', b)
        self.assertIn('"dir" to "vendor", "include" to listOf("**/*.jar")', b)
        self.assertIn('mainClass.set("Hello")', b)

    def test_eclipse_java(self):
        make_tree(self.tmp, {
            ".classpath": '<classpath><classpathentry kind="src" path="source"/>'
                          '<classpathentry kind="lib" path="jars/a.jar"/>'
                          '<classpathentry kind="con" path="org.eclipse.jdt.junit.JUNIT_CONTAINER/4"/></classpath>',
            "source/p/A.java": "package p;\nclass A {}\n",
            "source/p/config.properties": "x=1\n",
        })
        cli("to-studio", self.tmp)
        b = self.read("build.gradle.kts")
        self.assertIn('java.setSrcDirs(listOf("source"))', b)
        self.assertIn('resources.exclude("**/*.java", "**/*.kt")', b)
        self.assertIn('implementation(files("jars/a.jar"))', b)
        self.assertIn('testImplementation("junit:junit:4.13.2")', b)

    def test_unknown_folder(self):
        make_tree(self.tmp, {"notes.txt": "hi"})
        out = cli("to-studio", self.tmp)
        self.assertIn("Unrecognised", out)
        self.assertEqual(snapshot(self.tmp), {"notes.txt": b"hi"})


class IntelliJ(TempCase):
    def test_iml_modules_and_android_facet(self):
        make_tree(self.tmp, {
            ".idea/modules.xml": '<project><component name="ProjectModuleManager"><modules>'
                                 '<module filepath="$PROJECT_DIR$/core/core.iml"/>'
                                 '<module filepath="$PROJECT_DIR$/phone/phone.iml"/></modules></component></project>',
            ".idea/misc.xml": '<project><component name="ProjectRootManager" languageLevel="JDK_1_8"/></project>',
            ".idea/libraries/gson.xml": '<component name="libraryTable"><library name="gson" type="repository">'
                                        '<properties maven-id="com.google.code.gson:gson:2.11.0"/></library></component>',
            "core/core.iml": """
                <module type="JAVA_MODULE"><component name="NewModuleRootManager">
                  <content url="file://$MODULE_DIR$">
                    <sourceFolder url="file://$MODULE_DIR$/src" isTestSource="false"/>
                    <sourceFolder url="file://$MODULE_DIR$/test" isTestSource="true"/>
                  </content>
                  <orderEntry type="library" name="gson" level="project"/>
                </component></module>
            """,
            "core/src/c/C.java": "package c;\nclass C {}\n",
            "phone/phone.iml": """
                <module type="JAVA_MODULE">
                  <component name="FacetManager"><facet type="android" name="Android"><configuration>
                    <option name="MANIFEST_FILE_RELATIVE_PATH" value="/AndroidManifest.xml"/>
                  </configuration></facet></component>
                  <component name="NewModuleRootManager"><content url="file://$MODULE_DIR$">
                    <sourceFolder url="file://$MODULE_DIR$/src" isTestSource="false"/></content>
                    <orderEntry type="module" module-name="core"/>
                  </component>
                </module>
            """,
            "phone/AndroidManifest.xml": '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                                         'package="com.phone"><uses-sdk android:minSdkVersion="23"/></manifest>',
            "phone/res/values/s.xml": "<resources/>",
            "phone/src/com/phone/P.java": "package com.phone;\nclass P {}\n",
        })
        self.assertEqual(detect(self.tmp).kind, "intellij")
        cli("to-studio", self.tmp)
        core = self.read("core/build.gradle.kts")
        self.assertIn('implementation("com.google.code.gson:gson:2.11.0")', core)
        self.assertIn('JavaVersion.toVersion("1.8")', core)
        phone = self.read("phone/build.gradle.kts")
        self.assertIn('namespace = "com.phone"', phone)
        self.assertIn("minSdk = 23", phone)
        self.assertIn('implementation(project(":core"))', phone)
        self.assertIn("VERSION_1_8", phone)


class Flutter(TempCase):
    def test_flutter_roundtrip(self):
        make_tree(self.tmp, {
            "pubspec.yaml": "name: demo\n",
            "lib/main.dart": "void main() {}\n",
            "android/settings.gradle.kts": 'include(":app")\n',
            "android/app/build.gradle.kts": 'plugins { id("com.android.application") }\n',
        })
        p = detect(self.tmp)
        self.assertEqual((p.kind, p.studio_root), ("flutter", self.tmp))
        cli("to-vscode", self.tmp)
        launch, _ = jsonc.loads(self.read(".vscode/launch.json"))
        self.assertEqual(launch["configurations"][0]["type"], "dart")
        self.assertIn("Dart-Code.flutter", self.read(".vscode/extensions.json"))
        cli("to-studio", self.tmp)
        self.assertIn('value="$PROJECT_DIR$/lib/main.dart"', self.read(".idea/runConfigurations/Flutter.xml"))


if __name__ == "__main__":
    unittest.main()
