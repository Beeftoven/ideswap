from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import android, jsonc
from .util import find_named, has_files, read_text

SETTINGS_FILES = ("settings.gradle.kts", "settings.gradle")
BUILD_FILES = ("build.gradle.kts", "build.gradle")
GRADLE_KINDS = ("gradle", "flutter", "react-native")

KIND_NAMES = {
    "gradle": "Gradle project",
    "flutter": "Flutter project",
    "react-native": "React Native project",
    "maven": "Maven project (not Gradle)",
    "intellij": "IntelliJ-format project (.iml modules, not Gradle)",
    "eclipse-adt": "Eclipse ADT / Ant Android project (not Gradle)",
    "eclipse-java": "Eclipse Java project (not Gradle)",
    "vscode-java": "VS Code Java project with no build tool",
    "plain": "Source folder with no build tool",
    "unknown": "Unrecognised folder",
}


def first_existing(d: Path, names) -> Path | None:
    for n in names:
        p = d / n
        if p.is_file():
            return p
    return None


def is_gradle_dir(d: Path) -> bool:
    return bool(first_existing(d, SETTINGS_FILES) or first_existing(d, BUILD_FILES))


@dataclass
class GradleModule:
    gpath: str
    dir: Path
    build_file: Path | None = None
    app: bool = False
    library: bool = False
    application_id: str | None = None
    namespace: str | None = None
    manifest: Path | None = None
    launcher: str | None = None
    jvm_app: bool = False
    main_class: str | None = None

    @property
    def android(self):
        return self.app or self.library

    def task(self, name: str) -> str:
        return name if self.gpath == ":" else f"{self.gpath}:{name}"


@dataclass
class Project:
    root: Path
    kind: str
    gradle_root: Path | None = None
    android: bool = False
    kotlin: bool = False
    modules: list[GradleModule] = field(default_factory=list)
    name: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def gradle_ready(self):
        return self.kind in GRADLE_KINDS

    @property
    def studio_root(self) -> Path:
        """The folder Android Studio should open (where its .idea lives)."""
        if self.kind == "flutter" or self.gradle_root is None:
            return self.root
        return self.gradle_root

    def app_module(self) -> GradleModule | None:
        return next((m for m in self.modules if m.app), None)

    def jvm_app_module(self) -> GradleModule | None:
        return next((m for m in self.modules if m.jvm_app), None)

    def describe(self) -> str:
        s = KIND_NAMES[self.kind]
        tags = [t for t, on in (("Android", self.android), ("Kotlin", self.kotlin)) if on]
        if tags:
            s += f" [{', '.join(tags)}]"
        return s


INCLUDE_PAREN = re.compile(r"\binclude\s*\(([^)]*)\)", re.S)
INCLUDE_GROOVY = re.compile(r"\binclude\s+((?:[\"'][^\"']+[\"']\s*,?\s*)+)")
PROJECT_DIR = re.compile(
    r"project\(\s*[\"']([^\"']+)[\"']\s*\)\.projectDir\s*=\s*(?:file\(|new\s+File\([^,]+,)\s*[\"']([^\"']+)[\"']")


def _q(pattern: str, text: str):
    m = re.search(pattern, text)
    return m.group(1) if m else None


def scan_gradle(gradle_root: Path) -> tuple[list[GradleModule], str]:
    settings = first_existing(gradle_root, SETTINGS_FILES)
    text = read_text(settings) if settings else ""
    name = _q(r"rootProject\.name\s*=\s*[\"']([^\"']+)[\"']", text) or gradle_root.name
    paths: list[str] = []
    for m in list(INCLUDE_PAREN.finditer(text)) + list(INCLUDE_GROOVY.finditer(text)):
        for q in re.findall(r"[\"']([^\"']+)[\"']", m.group(1)):
            gp = q if q.startswith(":") else ":" + q
            if gp not in paths:
                paths.append(gp)
    overrides = {(a if a.startswith(":") else ":" + a): b for a, b in PROJECT_DIR.findall(text)}
    modules = []
    if first_existing(gradle_root, BUILD_FILES) or not paths:
        modules.append(GradleModule(":", gradle_root))
    for gp in paths:
        d = gradle_root / overrides[gp] if gp in overrides else gradle_root / gp.strip(":").replace(":", "/")
        modules.append(GradleModule(gp, d.resolve()))
    for mod in modules:
        _fill_module(mod)
    return modules, name


def _fill_module(mod: GradleModule):
    mod.build_file = first_existing(mod.dir, BUILD_FILES)
    text = read_text(mod.build_file) if mod.build_file else ""
    applied = re.sub(r"(?m)^.*\bapply\s*\(?\s*false.*$", "", text)
    mod.app = bool(re.search(r"com\.android\.application|android[.\-]application|androidApplication", applied))
    mod.library = not mod.app and bool(
        re.search(r"com\.android\.library|android[.\-]library|androidLibrary", applied))
    mod.application_id = _q(r"applicationId\s*=?\s*[\"']([^\"']+)[\"']", text)
    mod.namespace = _q(r"namespace\s*=?\s*[\"']([^\"']+)[\"']", text)
    if not mod.android:
        mod.jvm_app = bool(re.search(r"(?m)^\s*(?:application|id\s*\(?\s*[\"']application[\"'])", text))
        mod.main_class = _q(r"mainClass(?:Name)?(?:\.set\()?\s*=?\s*\(?\s*[\"']([^\"']+)[\"']", text)
        return
    custom = _q(r"manifest\.srcFile\s*\(?\s*(?:file\()?\s*[\"']([^\"']+)[\"']", text)
    manifest = mod.dir / custom if custom else mod.dir / "src" / "main" / "AndroidManifest.xml"
    if manifest.is_file():
        mod.manifest = manifest
        info = android.parse_manifest(manifest)
        mod.namespace = mod.namespace or info.package
        if info.launcher:
            mod.launcher = android.resolve_class(info.launcher, mod.namespace)
    mod.application_id = mod.application_id or mod.namespace


def _json_file(path: Path):
    try:
        data, _ = jsonc.loads(read_text(path))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def detect(root: Path) -> Project:
    root = Path(root).resolve()
    p = _detect_kind(root)
    if p.gradle_root:
        p.modules, p.name = scan_gradle(p.gradle_root)
        p.android = p.android or any(m.android for m in p.modules)
    p.name = p.name or root.name
    if p.kind != "unknown":
        p.kotlin = has_files(root, (".kt",))
        if not p.android and p.kind not in GRADLE_KINDS:
            p.android = bool(find_named(root, "AndroidManifest.xml", limit=1))
    return p


def _detect_kind(root: Path) -> Project:
    android_dir = root / "android"
    sub_gradle = android_dir if is_gradle_dir(android_dir) else None
    if (root / "pubspec.yaml").is_file() and android_dir.is_dir():
        return Project(root, "flutter", sub_gradle, android=True)
    pkg = root / "package.json"
    if pkg.is_file() and android_dir.is_dir():
        deps = {**_json_file(pkg).get("dependencies", {}), **_json_file(pkg).get("devDependencies", {})}
        if "react-native" in deps:
            return Project(root, "react-native", sub_gradle, android=True)
    if is_gradle_dir(root):
        return Project(root, "gradle", root)
    subs = [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith(".")
            and first_existing(d, SETTINGS_FILES)]
    if len(subs) == 1:
        p = Project(root, "gradle", subs[0])
        p.notes.append(f"The Gradle build lives in the '{subs[0].name}' subfolder.")
        return p
    if (root / "pom.xml").is_file():
        return Project(root, "maven")
    if (root / ".idea" / "modules.xml").is_file() or any(root.glob("*.iml")):
        return Project(root, "intellij")
    if (root / "AndroidManifest.xml").is_file():
        return Project(root, "eclipse-adt", android=True)
    if (root / ".classpath").is_file():
        return Project(root, "eclipse-java")
    settings = root / ".vscode" / "settings.json"
    if settings.is_file() and "java.project.sourcePaths" in _json_file(settings):
        return Project(root, "vscode-java")
    if has_files(root, (".java", ".kt")):
        return Project(root, "plain")
    return Project(root, "unknown")
