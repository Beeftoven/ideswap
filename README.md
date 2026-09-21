# ideswap

Move Android, Java and Kotlin projects between **Android Studio** and **VS Code**, in either
direction, without losing anything.

## Why this works

A Gradle build (`settings.gradle`, `build.gradle`, `src/`) is what both IDEs actually build from.
Each IDE only adds its own config folder, `.idea/` or `.vscode/`, and the two don't conflict.
So ideswap never rewrites your code. It:

1. **Converts the build to Gradle if it isn't already.** Supported: Maven, Eclipse ADT/Ant Android,
   Eclipse Java, IntelliJ `.iml` projects, VS Code Java projects with no build tool, and plain source
   folders. Sources stay where they are, and the generated build points at them. The old build files
   (`pom.xml`, `.classpath`, ...) are left in place.
2. **Translates settings that have an equivalent** in the other IDE.
3. **Never deletes the other IDE's config.** Anything that can't be translated stays where it was,
   so you can go back and forth.
4. **Records every write** so `ideswap undo` restores the exact original bytes.

| Android Studio (`.idea`) | VS Code (`.vscode`) |
|---|---|
| Gradle run configurations | `tasks.json` Gradle tasks (both ways) |
| Application run configurations | `launch.json` Java launches (both ways) |
| Flutter run configurations | `launch.json` Dart launches (both ways) |
| Android app run configuration | `launch.json` Android launch (Android extension) |
| Project code style (indentation) | `.editorconfig` (read by both IDEs) |
| Gradle JDK path (`gradle.xml`) | `java.import.gradle.java.home` |
| Layout editor, profiler, inspections | kept in `.idea`, untouched |
| Non-Gradle tasks (npm, scripts) | kept in `.vscode`, untouched |

When the same task or configuration name exists on both sides with different contents, neither side
is changed and the report tells you.

## Install

Requires Python 3.9+. There are no dependencies.

```bash
pip install -e .
```

Or run it without installing: `python -m ideswap ...` from this folder.

## Use

```bash
ideswap detect  path/to/project      # what is it, what would happen
ideswap to-studio path/to/project    # get it ready for Android Studio
ideswap to-vscode path/to/project    # get it ready for VS Code
ideswap sync path/to/project         # both
ideswap undo path/to/project         # revert the last run exactly
```

Useful options for the conversion commands:

- `--dry-run` shows the report and the file list without writing anything.
- `--dest DIR` copies the project to `DIR` first (skipping build outputs and caches) and converts the copy.
- `--no-build-convert` leaves non-Gradle projects alone.
- `--sdk PATH` sets the Android SDK location if it isn't auto-detected.
- `--agp`, `--gradle-version`, `--kotlin-version`, `--compile-sdk`, `--java-version` set the versions
  used in generated builds. The defaults are AGP 9.3.0, Gradle 9.5.0, Kotlin 2.2.20, compileSdk 36 and Java 17.

## Things to know

- **Gradle wrapper.** ideswap writes `gradle/wrapper/gradle-wrapper.properties` but not the
  `gradlew` scripts or jar. Android Studio syncs fine without them; run the `wrapper` task once to create them.
- **Manifest edits.** For converted Android projects, `package=` and `uses-sdk` min/target versions
  move from `AndroidManifest.xml` into `build.gradle.kts`, because current Android Gradle Plugin versions
  reject them in the manifest. This is the only change ideswap makes to an existing source file, and
  `undo` reverts it.
- **Comments in `.vscode/*.json`.** They are kept unless ideswap has to add an entry to that file.
  If it does, the original is backed up and the report says so.
- **Undo safety.** `undo` skips any file you've edited since ideswap wrote it, so it never throws away your changes.
- **State.** Undo data lives in `.ideswap/`, which ignores itself in git.

## Tests

```bash
python -m unittest discover -s tests -t .
```
