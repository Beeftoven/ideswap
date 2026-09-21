from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import editorconfig, gradlegen, idea, vscode
from .detect import Project, detect
from .util import escape_property, parse_properties, rel
from .vscode import DEFAULT_MARK, JsonFile


@dataclass
class Options:
    agp: str = "9.3.0"
    gradle: str = "9.5.0"
    kotlin: str = "2.2.20"
    compile_sdk: int = 36
    java_version: int = 17
    convert_build: bool = True
    sdk: str | None = None


def find_sdk(opts: Options) -> Path | None:
    cands = [opts.sdk, os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
    home = Path.home()
    if os.environ.get("LOCALAPPDATA"):
        cands.append(str(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk"))
    cands += [str(home / "Library" / "Android" / "sdk"), str(home / "Android" / "Sdk")]
    for c in cands:
        if c and Path(c).is_dir():
            return Path(c)
    return None


def prepare(root: Path, tx, report, opts: Options) -> Project:
    p = detect(root)
    report.info(f"Detected: {p.describe()} at {p.root}")
    for n in p.notes:
        report.info(n)
    if not p.gradle_ready:
        if p.kind == "unknown":
            report.warn("No Gradle/Maven/Eclipse/IntelliJ build and no Java/Kotlin sources found here.")
            return p
        if not opts.convert_build:
            report.todo("This is not a Gradle project; re-run without --no-build-convert to generate a Gradle build.")
            return p
        if gradlegen.convert(p, tx, report, opts):
            p = detect(root)
    if p.android and p.gradle_root:
        _ensure_local_properties(p, tx, report, opts)
    if p.kind == "flutter" and not p.gradle_root:
        report.todo("Run `flutter create .` to regenerate the missing android/ Gradle project.")
    return p


def _ensure_local_properties(p: Project, tx, report, opts):
    lp = p.gradle_root / "local.properties"
    if "sdk.dir" in parse_properties(lp):
        return
    sdk = find_sdk(opts)
    if sdk is None:
        report.todo("No Android SDK found on this machine. Install Android Studio (it installs the SDK), "
                    "or pass --sdk <path>. VS Code builds need it too.")
        return
    existing = lp.read_text(encoding="utf-8") if lp.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    tx.write(lp, existing + f"sdk.dir={escape_property(str(sdk))}\n")
    report.info(f"Pointed local.properties at the Android SDK in {sdk} (keep this file out of git).")


# ---------------------------------------------------------------- VS Code -> Android Studio

def to_studio(p: Project, tx, report):
    if not p.gradle_ready:
        return
    sroot = p.studio_root
    existing = {rc.name: rc for rc in idea.read_run_configs(sroot)}
    vs = p.root / ".vscode"
    if vs.is_dir():
        settings = JsonFile(vs / "settings.json", {})
        _settings_to_editorconfig(p, settings.data, tx, report)
        home = settings.data.get("java.import.gradle.java.home")
        if isinstance(home, str) and p.gradle_root:
            if not (sroot / ".idea" / "gradle.xml").exists():
                tx.write(sroot / ".idea" / "gradle.xml", idea.gradle_xml(home))
                report.translated(f"Gradle JDK ({home}) -> .idea/gradle.xml")
            elif idea.read_gradle_jvm(sroot) != home:
                report.kept(f"Gradle JDK differs (VS Code: {home}, Android Studio: {idea.read_gradle_jvm(sroot)}); "
                            "both left as they are")
        _tasks_to_studio(p, JsonFile(vs / "tasks.json", {}), existing, tx, report)
        _launch_to_studio(p, JsonFile(vs / "launch.json", {}), existing, tx, report)
        for key in sorted(settings.data):
            if key.startswith(("java.configuration", "java.format", "kotlin.", "dart.", "files.exclude",
                               "search.exclude")):
                report.kept(f"VS Code setting '{key}' (stays in .vscode/settings.json)")
    else:
        report.info("No .vscode folder, so there are no VS Code settings to carry over.")
    if p.kind == "flutter":
        report.todo(f"In Android Studio (with the Flutter plugin): File > Open > {sroot}")
    else:
        report.todo(f"In Android Studio: File > Open > {sroot}  (Android Studio syncs Gradle and "
                    "creates the rest of .idea itself)")


def _settings_to_editorconfig(p: Project, settings: dict, tx, report):
    sections, unmapped = editorconfig.from_vscode_settings(settings)
    for scope in unmapped:
        report.kept(f"VS Code language settings {scope}")
    if not sections:
        return
    target = p.root / ".editorconfig"
    if target.exists():
        report.kept("Editor settings: .editorconfig already exists and wins in both IDEs; not changed")
        return
    tx.write(target, editorconfig.render(sections))
    report.translated("Editor formatting settings (indentation, charset, line endings) -> .editorconfig, "
                      "which Android Studio reads natively")


def _studio_path(p: Project, cwd_rel: str) -> str:
    return rel((p.root / cwd_rel).resolve(), p.studio_root) if cwd_rel else rel(p.root, p.studio_root)


def _tasks_to_studio(p: Project, tasks: JsonFile, existing, tx, report):
    if tasks.invalid:
        report.warn("tasks.json could not be parsed; skipped.")
        return
    for t in tasks.data.get("tasks", []) or []:
        if not isinstance(t, dict):
            continue
        label = t.get("label") or t.get("script") or t.get("command")
        if t.get("detail") == DEFAULT_MARK:
            continue
        g = vscode.gradle_of_task(t)
        if not g:
            report.kept(f"VS Code task '{label}' (not a Gradle task; Android Studio has no equivalent)")
            continue
        names, args, cwd = g
        path = _studio_path(p, cwd)
        path = "" if path == "." else path
        if label in existing:
            rc = existing[label]
            if rc.type == idea.GRADLE and (rc.gradle_tasks != names or rc.gradle_args != args):
                report.kept(f"Task '{label}' differs between the IDEs; both versions kept unchanged")
            continue
        f = idea.config_filename(p.studio_root, label)
        tx.write(f, idea.gradle_config_xml(label, names, args, path))
        existing[label] = idea.RunConfig(label, idea.GRADLE, names, args, path)
        report.translated(f"Task '{label}' -> Gradle run configuration ({' '.join(names)})")


def _module_name(p: Project, gpath: str) -> str:
    return f"{p.name}.main" if gpath == ":" else f"{p.name}{gpath.replace(':', '.')}.main"


def _launch_to_studio(p: Project, launch: JsonFile, existing, tx, report):
    if launch.invalid:
        report.warn("launch.json could not be parsed; skipped.")
        return
    jvm = p.jvm_app_module()
    for c in launch.data.get("configurations", []) or []:
        if not isinstance(c, dict):
            continue
        name, typ = c.get("name", "?"), c.get("type")
        if name in existing:
            continue
        if typ == "java" and c.get("request") == "launch" and c.get("mainClass") and "${" not in c["mainClass"]:
            module = _module_name(p, jvm.gpath if jvm else ":")
            xml = idea.application_config_xml(name, c["mainClass"], module,
                                              vscode.args_list(c.get("args")), vscode.args_list(c.get("vmArgs")))
            tx.write(idea.config_filename(p.studio_root, name), xml)
            report.translated(f"Launch '{name}' -> Application run configuration ({c['mainClass']})")
        elif typ == "dart" and c.get("request", "launch") == "launch":
            program = vscode.strip_workspace(c.get("program")) or "lib/main.dart"
            tx.write(idea.config_filename(p.studio_root, name), idea.flutter_config_xml(name, program))
            report.translated(f"Launch '{name}' -> Flutter run configuration ({program})")
        elif typ == "android":
            report.translated(f"Launch '{name}': Android Studio creates its own 'app' run configuration on sync")
        else:
            report.kept(f"Launch configuration '{name}' (type '{typ}' has no Android Studio equivalent)")
        existing[name] = None


# ---------------------------------------------------------------- Android Studio -> VS Code

def to_vscode(p: Project, tx, report):
    vs = p.root / ".vscode"
    sroot = p.studio_root
    has_idea = (sroot / ".idea").is_dir()
    tasks = JsonFile(vs / "tasks.json", {"version": "2.0.0", "tasks": []})
    launch = JsonFile(vs / "launch.json", {"version": "0.2.0", "configurations": []})
    ext = JsonFile(vs / "extensions.json", {"recommendations": []})
    settings = JsonFile(vs / "settings.json", {})

    if not tasks.exists:
        tasks.data["tasks"] = default_tasks(p)
        tasks.changed = bool(tasks.data["tasks"])
    if not launch.exists:
        launch.data["configurations"] = default_launch(p)
        launch.changed = bool(launch.data["configurations"])
    if not ext.exists:
        ext.data["recommendations"] = default_extensions(p)
        ext.changed = True

    if has_idea:
        configs = idea.read_run_configs(sroot)
        _configs_to_vscode(p, configs, tasks, launch, report)
        jvm_home = idea.read_gradle_jvm(sroot)
        if jvm_home:
            if Path(jvm_home).is_absolute():
                if not settings.invalid and "java.import.gradle.java.home" not in settings.data:
                    settings.data["java.import.gradle.java.home"] = jvm_home
                    settings.changed = True
                    report.translated(f"Gradle JDK ({jvm_home}) -> java.import.gradle.java.home")
            else:
                report.kept(f"Gradle JDK '{jvm_home}' is an Android Studio JDK name, not a path; "
                            "VS Code uses JAVA_HOME or java.import.gradle.java.home instead")
        style = idea.read_code_style(sroot)
        if style:
            if (p.root / ".editorconfig").exists():
                report.kept("Android Studio code style: .editorconfig already exists and wins; not changed")
            else:
                tx.write(p.root / ".editorconfig", editorconfig.render(style))
                report.translated("Android Studio indentation settings -> .editorconfig (VS Code reads it "
                                  "with the EditorConfig extension)")
                if "EditorConfig.EditorConfig" not in ext.data.get("recommendations", []):
                    ext.data.setdefault("recommendations", []).append("EditorConfig.EditorConfig")
                    ext.changed = True
        report.kept("Android Studio-only state (layout editor, profiler, inspections, window layout) stays in "
                    ".idea for when you reopen it there")
    else:
        report.info("No .idea folder, so there are no Android Studio settings to carry over.")

    for f in (tasks, launch, ext, settings):
        if f.invalid:
            report.warn(f".vscode/{f.path.name} could not be parsed; left untouched.")
        f.save(tx, report)
    if p.android:
        report.todo("In VS Code: open " + str(p.root) + " and install the recommended extensions when prompted. "
                    "Build/install/run are under Terminal > Run Task.")
    if not shutil.which("java") and not os.environ.get("JAVA_HOME"):
        report.todo("No JDK found on PATH. VS Code's Java/Gradle tools need JDK 17+ (Android Studio bundles "
                    "its own, VS Code does not).")


def _configs_to_vscode(p: Project, configs, tasks: JsonFile, launch: JsonFile, report):
    labels = {t.get("label"): t for t in tasks.data.get("tasks", []) if isinstance(t, dict)}
    launch_names = {c.get("name") for c in launch.data.get("configurations", []) if isinstance(c, dict)}
    has_android_launch = any(isinstance(c, dict) and c.get("type") == "android"
                             for c in launch.data.get("configurations", []))
    for rc in configs:
        if rc.type == idea.GRADLE:
            if not rc.gradle_tasks:
                continue
            cwd = rel((p.studio_root / rc.project_path).resolve(), p.root)
            cwd = "" if cwd == "." else cwd
            if rc.name in labels:
                g = vscode.gradle_of_task(labels[rc.name])
                if g and (g[0] != rc.gradle_tasks or g[1] != rc.gradle_args):
                    report.kept(f"Task '{rc.name}' differs between the IDEs; both versions kept unchanged")
                continue
            if tasks.invalid:
                continue
            tasks.data.setdefault("tasks", []).append(vscode.gradle_task(rc.name, rc.gradle_tasks, rc.gradle_args, cwd))
            tasks.changed = True
            labels[rc.name] = tasks.data["tasks"][-1]
            report.translated(f"Gradle run configuration '{rc.name}' -> VS Code task")
        elif rc.name in launch_names or launch.invalid:
            continue
        elif rc.type == idea.APPLICATION and rc.main_class:
            cfg = {"type": "java", "name": rc.name, "request": "launch", "mainClass": rc.main_class}
            if rc.program_args:
                cfg["args"] = rc.program_args
            if rc.vm_args:
                cfg["vmArgs"] = rc.vm_args
            launch.data.setdefault("configurations", []).append(cfg)
            launch.changed = True
            report.translated(f"Application run configuration '{rc.name}' -> launch.json")
        elif rc.type == idea.FLUTTER and rc.program:
            launch.data.setdefault("configurations", []).append(
                {"name": rc.name, "type": "dart", "request": "launch", "program": rc.program})
            launch.changed = True
            report.translated(f"Flutter run configuration '{rc.name}' -> launch.json")
        elif rc.type == idea.ANDROID_APP:
            if not has_android_launch:
                launch.data.setdefault("configurations", []).extend(default_launch(p, android_only=True))
                launch.changed = has_android_launch = True
                report.translated(f"Android run configuration '{rc.name}' -> launch.json (Android extension)")
        else:
            report.kept(f"Run configuration '{rc.name}' ({rc.type}) has no VS Code equivalent")
        launch_names.add(rc.name)


# ---------------------------------------------------------------- defaults for a fresh .vscode

def _gradle_cwd(p: Project) -> str:
    if not p.gradle_root:
        return ""
    r = rel(p.gradle_root, p.root)
    return "" if r == "." else r


def default_tasks(p: Project) -> list[dict]:
    if p.kind == "flutter":
        return [vscode.shell_task("Flutter: build APK", "flutter", ["build", "apk"]),
                vscode.shell_task("Flutter: run", "flutter", ["run"])]
    out: list[dict] = []
    cwd = _gradle_cwd(p)
    if p.kind == "react-native":
        out.append(vscode.shell_task("React Native: run on Android", "npx", ["react-native", "run-android"]))
    if not p.gradle_root:
        return out
    app = p.app_module()
    if app:
        out += [
            vscode.gradle_task("Build debug APK", [app.task("assembleDebug")], cwd=cwd, group="build",
                               default=True, generated=True),
            vscode.gradle_task("Install debug on device", [app.task("installDebug")], cwd=cwd, generated=True),
        ]
        if app.application_id and app.launcher:
            out.append(vscode.shell_task("Run app on device", "adb",
                                         ["shell", "am", "start", "-n", f"{app.application_id}/{app.launcher}"],
                                         depends="Install debug on device"))
        out += [vscode.gradle_task("Unit tests", [app.task("testDebugUnitTest")], cwd=cwd, group="test",
                                   generated=True),
                vscode.gradle_task("Clean", ["clean"], cwd=cwd, generated=True)]
    else:
        out += [vscode.gradle_task("Build", ["build"], cwd=cwd, group="build", default=True, generated=True),
                vscode.gradle_task("Test", ["test"], cwd=cwd, group="test", generated=True)]
        jvm = p.jvm_app_module()
        if jvm:
            out.append(vscode.gradle_task("Run", [jvm.task("run")], cwd=cwd, generated=True))
        out.append(vscode.gradle_task("Clean", ["clean"], cwd=cwd, generated=True))
    return out


def default_launch(p: Project, android_only=False) -> list[dict]:
    if p.kind == "flutter" and not android_only:
        return [{"name": "Flutter", "type": "dart", "request": "launch", "program": "lib/main.dart"}]
    app = p.app_module()
    if app and app.manifest:
        apk_name = (p.name if app.gpath == ":" else app.dir.name)
        ws = "${workspaceFolder}/"
        return [{
            "type": "android", "request": "launch", "name": "Launch app on device",
            "appSrcRoot": ws + rel(app.manifest.parent, p.root),
            "apkFile": ws + rel(app.dir / "build" / "outputs" / "apk" / "debug" / f"{apk_name}-debug.apk", p.root),
            "adbPort": 5037,
            "preLaunchTask": "Build debug APK",
        }]
    jvm = p.jvm_app_module()
    if jvm and jvm.main_class and not android_only:
        return [{"type": "java", "name": "Run main", "request": "launch", "mainClass": jvm.main_class}]
    return []


def default_extensions(p: Project) -> list[str]:
    if p.kind == "flutter":
        return ["Dart-Code.dart-code", "Dart-Code.flutter"]
    recs = ["vscjava.vscode-java-pack", "vscjava.vscode-gradle"]
    if p.kotlin:
        recs.append("fwcd.kotlin")
    if p.android:
        recs += ["adelphes.android-dev-ext", "redhat.vscode-xml"]
    if p.kind == "react-native":
        recs.insert(0, "msjsdiag.vscode-react-native")
    if (p.root / ".editorconfig").exists():
        recs.append("EditorConfig.EditorConfig")
    return recs


def run(root: Path, direction: str, tx, report, opts: Options) -> Project:
    p = prepare(root, tx, report, opts)
    if p.gradle_ready or p.kind == "flutter":
        if direction in ("studio", "sync"):
            to_studio(p, tx, report)
        if direction in ("vscode", "sync"):
            to_vscode(p, tx, report)
    return p
