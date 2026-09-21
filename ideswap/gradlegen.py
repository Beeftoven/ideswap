"""Turn a non-Gradle project into a Gradle build, in place, without moving any source files.

Sources stay where they are; the generated build points at them with sourceSets.
Nothing is deleted, so the original build description (pom.xml, .classpath, .iml, ...)
keeps working alongside the new Gradle files.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from . import android, idea, jsonc
from .detect import BUILD_FILES, Project, first_existing
from .util import (classify_root, find_named, gradle_name, kstr, minimal_roots, parse_properties,
                   read_text, rel, source_root_of, walk_files)

JUNIT4 = 'testImplementation("junit:junit:4.13.2")'
JUNIT5 = ['testImplementation(platform("org.junit:junit-bom:5.11.4"))',
          'testImplementation("org.junit.jupiter:junit-jupiter")',
          'testRuntimeOnly("org.junit.platform:junit-platform-launcher")']


@dataclass
class ModuleSpec:
    dir: Path
    gpath: str = ":"
    android: bool = False
    library: bool = False
    kotlin: bool = False
    namespace: str | None = None
    app_id: str | None = None
    min_sdk: int | None = None
    target_sdk: int | None = None
    compile_sdk: int | None = None
    manifest: Path | None = None
    java_dirs: list[Path] = field(default_factory=list)
    res_dirs: list[Path] = field(default_factory=list)
    assets_dirs: list[Path] = field(default_factory=list)
    jni_dirs: list[Path] = field(default_factory=list)
    aidl_dirs: list[Path] = field(default_factory=list)
    resource_dirs: list[Path] = field(default_factory=list)
    test_dirs: list[Path] = field(default_factory=list)
    test_resource_dirs: list[Path] = field(default_factory=list)
    android_test_dirs: list[Path] = field(default_factory=list)
    exclude_sources_from_resources: bool = False
    ndk_build: Path | None = None
    minify_release: bool = False
    proguard_files: list[Path] = field(default_factory=list)
    main_class: str | None = None
    java_version: str | None = None
    group: str | None = None
    version: str | None = None
    deps: list[str] = field(default_factory=list)
    junit5: bool = False

    def add_dep(self, line: str):
        if line not in self.deps:
            self.deps.append(line)


def _mod(gpath: str) -> str:
    return "Main module" if gpath == ":" else f"Module '{gpath}'"


# ---------------------------------------------------------------- rendering

def _paths(dirs, base) -> str:
    return ", ".join(kstr(rel(d, base)) for d in dirs)


def _java_const(v: str) -> str:
    return "VERSION_" + v.replace(".", "_")


def _norm_java(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip()
    m = re.fullmatch(r"1\.(\d+)", v)
    major = int(m.group(1)) if m else int(v) if v.isdigit() else None
    if major is None:
        return None
    if major < 8:
        return "1.8"
    return "1.8" if major == 8 else str(major)


def render_android(s: ModuleSpec, opts) -> str:
    b = s.dir
    plugin = "com.android.library" if s.library else "com.android.application"
    L = ["plugins {", f'    id("{plugin}")', "}", "", "android {",
         f"    namespace = {kstr(s.namespace)}", f"    compileSdk = {s.compile_sdk or opts.compile_sdk}",
         "", "    defaultConfig {"]
    if not s.library and s.app_id:
        L.append(f"        applicationId = {kstr(s.app_id)}")
    if s.min_sdk:
        L.append(f"        minSdk = {s.min_sdk}")
    if s.target_sdk and not s.library:
        L.append(f"        targetSdk = {s.target_sdk}")
    L.append("    }")
    if s.minify_release or s.proguard_files:
        files = ['getDefaultProguardFile("proguard-android-optimize.txt")'] + [kstr(rel(p, b)) for p in s.proguard_files]
        L += ["", "    buildTypes {", "        release {",
              f"            isMinifyEnabled = {'true' if s.minify_release else 'false'}",
              f"            proguardFiles({', '.join(files)})", "        }", "    }"]
    jv = _norm_java(s.java_version) or str(opts.java_version)
    L += ["", "    compileOptions {", f"        sourceCompatibility = JavaVersion.{_java_const(jv)}",
          f"        targetCompatibility = JavaVersion.{_java_const(jv)}", "    }"]
    if s.aidl_dirs:
        L += ["", "    buildFeatures {", "        aidl = true", "    }"]
    if s.ndk_build:
        L += ["", "    externalNativeBuild {", "        ndkBuild {",
              f"            path = file({kstr(rel(s.ndk_build, b))})", "        }", "    }"]
    sets: list[tuple[str, list[str]]] = []
    main: list[str] = []
    if s.manifest:
        main.append(f"manifest.srcFile({kstr(rel(s.manifest, b))})")
    if s.java_dirs:
        main.append(f"java.srcDirs({_paths(s.java_dirs, b)})")
        if s.kotlin:
            main.append(f"kotlin.srcDirs({_paths(s.java_dirs, b)})")
    for attr, dirs in (("res", s.res_dirs), ("assets", s.assets_dirs), ("jniLibs", s.jni_dirs), ("aidl", s.aidl_dirs)):
        if dirs:
            main.append(f"{attr}.srcDirs({_paths(dirs, b)})")
    if main:
        sets.append(("main", main))
    for name, dirs in (("test", s.test_dirs), ("androidTest", s.android_test_dirs)):
        if dirs:
            entry = [f"java.srcDirs({_paths(dirs, b)})"]
            if s.kotlin:
                entry.append(f"kotlin.srcDirs({_paths(dirs, b)})")
            sets.append((name, entry))
    if sets:
        L += ["", "    sourceSets {"]
        for name, entries in sets:
            L.append(f'        getByName("{name}") {{')
            L += [f"            {e}" for e in entries]
            L.append("        }")
        L.append("    }")
    L.append("}")
    return _finish(L, s)


def render_jvm(s: ModuleSpec, opts) -> str:
    b = s.dir
    L = ["plugins {", '    id("org.jetbrains.kotlin.jvm")' if s.kotlin else "    java"]
    if s.main_class:
        L.append("    application")
    L += ["}", ""]
    if s.group:
        L.append(f"group = {kstr(s.group)}")
    if s.version:
        L.append(f"version = {kstr(s.version)}")
    if s.group or s.version:
        L.append("")
    jv = _norm_java(s.java_version)
    if jv and not s.kotlin:
        L += ["java {", f"    sourceCompatibility = JavaVersion.toVersion({kstr(jv)})",
              f"    targetCompatibility = JavaVersion.toVersion({kstr(jv)})", "}", ""]
    L += ["sourceSets {", "    main {", f"        java.setSrcDirs(listOf({_paths(s.java_dirs, b)}))",
          f"        resources.setSrcDirs(listOf({_paths(s.resource_dirs, b)}))"]
    if s.exclude_sources_from_resources:
        L.append('        resources.exclude("**/*.java", "**/*.kt")')
    L.append("    }")
    if s.test_dirs or s.test_resource_dirs:
        L += ["    test {", f"        java.setSrcDirs(listOf({_paths(s.test_dirs, b)}))",
              f"        resources.setSrcDirs(listOf({_paths(s.test_resource_dirs, b)}))"]
        if s.exclude_sources_from_resources:
            L.append('        resources.exclude("**/*.java", "**/*.kt")')
        L.append("    }")
    L.append("}")
    if s.kotlin:
        L += ["", "kotlin {", f'    sourceSets["main"].kotlin.setSrcDirs(listOf({_paths(s.java_dirs, b)}))']
        if s.test_dirs:
            L.append(f'    sourceSets["test"].kotlin.setSrcDirs(listOf({_paths(s.test_dirs, b)}))')
        L.append("}")
    if s.main_class:
        L += ["", "application {", f"    mainClass.set({kstr(s.main_class)})", "}"]
    text = _finish(L, s)
    if s.junit5:
        text += "\ntasks.test {\n    useJUnitPlatform()\n}\n"
    return text


def _finish(L: list[str], s: ModuleSpec) -> str:
    if s.deps:
        L += ["", "dependencies {"]
        for d in s.deps:
            L += ["    " + line for line in d.split("\n")]
        L.append("}")
    return "\n".join(L) + "\n"


def render_settings(name: str, specs: list[ModuleSpec], root: Path, repos: list[str], opts) -> str:
    plugins = []
    if any(s.android and not s.library for s in specs):
        plugins.append(f'        id("com.android.application") version "{opts.agp}"')
    if any(s.android and s.library for s in specs):
        plugins.append(f'        id("com.android.library") version "{opts.agp}"')
    if any(s.kotlin and not s.android for s in specs):
        plugins.append(f'        id("org.jetbrains.kotlin.jvm") version "{opts.kotlin}"')
    L = ["pluginManagement {", "    repositories {", "        google()", "        mavenCentral()",
         "        gradlePluginPortal()", "    }"]
    if plugins:
        L += ["    plugins {"] + plugins + ["    }"]
    L += ["}", "", "dependencyResolutionManagement {", "    repositories {", "        google()",
          "        mavenCentral()"]
    L += [f"        maven({kstr(u)})" for u in repos]
    L += ["    }", "}", "", f"rootProject.name = {kstr(gradle_name(name))}"]
    subs = sorted((s for s in specs if s.gpath != ":"), key=lambda s: s.gpath)
    if subs:
        L.append("")
    for s in subs:
        L.append(f'include("{s.gpath}")')
        default = root / s.gpath.strip(":").replace(":", "/")
        if s.dir.resolve() != default.resolve():
            L.append(f'project("{s.gpath}").projectDir = file({kstr(rel(s.dir, root))})')
    return "\n".join(L) + "\n"


def wrapper_properties(version: str) -> str:
    return ("distributionBase=GRADLE_USER_HOME\n"
            "distributionPath=wrapper/dists\n"
            f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{version}-bin.zip\n"
            "networkTimeout=10000\n"
            "validateDistributionUrl=true\n"
            "zipStoreBase=GRADLE_USER_HOME\n"
            "zipStorePath=wrapper/dists\n")


# ---------------------------------------------------------------- shared discovery

def _sources(root: Path):
    return list(walk_files(root, (".java", ".kt")))


def _roots_by_kind(files, project_root: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {"main": [], "test": [], "androidTest": []}
    for r in minimal_roots(source_root_of(f) for f in files):
        out[classify_root(r, project_root)].append(r)
    return out


def _files_under(dirs):
    for d in dirs:
        if d.is_dir():
            yield from walk_files(d, (".java", ".kt"))


def find_main_classes(dirs) -> list[str]:
    found = []
    for f in _files_under(dirs):
        text = read_text(f)
        pkg_m = re.search(r"^\s*package\s+([\w.]+)", text, re.M)
        pkg = pkg_m.group(1) if pkg_m else ""
        cls = None
        if f.suffix == ".java" and re.search(r"\bstatic\s+(?:final\s+)?(?:public\s+)?void\s+main\s*\(", text):
            cls = f.stem
        elif f.suffix == ".kt" and re.search(r"^fun\s+main\s*\(", text, re.M):
            jvm_name = re.search(r'@file:JvmName\(\s*"(\w+)"\s*\)', text)
            stem = re.sub(r"\W", "_", f.stem)
            cls = jvm_name.group(1) if jvm_name else stem[:1].upper() + stem[1:] + "Kt"
        if cls:
            found.append(f"{pkg}.{cls}" if pkg else cls)
    return found


def _pick_main(spec: ModuleSpec, report):
    mains = find_main_classes(spec.java_dirs)
    if not mains:
        return
    preferred = [m for m in mains if m.rsplit(".", 1)[-1] in ("Main", "MainKt", "App", "AppKt", "Application")]
    spec.main_class = (preferred or mains)[0]
    if len(mains) > 1:
        others = ", ".join(m for m in mains if m != spec.main_class)
        report.warn(f"Several entry points found; using {spec.main_class}. Others: {others}")


def _test_frameworks(spec: ModuleSpec):
    text = "\n".join(read_text(f)[:5000] for f in _files_under(spec.test_dirs))
    if "org.junit.jupiter" in text:
        for d in JUNIT5:
            spec.add_dep(d)
        spec.junit5 = True
    elif re.search(r"import\s+org\.junit\.", text):
        spec.add_dep(JUNIT4)
    if "import kotlin.test" in text:
        spec.add_dep('testImplementation(kotlin("test"))')


def _jar_tree(spec: ModuleSpec, d: Path, conf="implementation"):
    if d.is_dir() and any(d.glob("*.jar")):
        spec.add_dep(f'{conf}(fileTree(mapOf("dir" to {kstr(rel(d, spec.dir))}, "include" to listOf("*.jar"))))')


def _android_from_manifest(spec: ModuleSpec, manifest: Path, report):
    info = android.parse_manifest(manifest)
    spec.android = True
    spec.manifest = manifest
    spec.namespace = spec.namespace or info.package
    if not spec.namespace:
        spec.namespace = "com.example." + re.sub(r"\W", "", spec.dir.name.lower()) or "app"
        report.warn(f"No package name found in {manifest.name}; using namespace '{spec.namespace}' - check it.")
    spec.app_id = spec.app_id or info.package or spec.namespace
    spec.min_sdk = spec.min_sdk or info.min_sdk
    spec.target_sdk = spec.target_sdk or info.target_sdk
    if not spec.min_sdk:
        spec.min_sdk = 21
        report.warn(f"No minSdkVersion found for {_mod(spec.gpath)}; defaulted to 21.")
    elif spec.min_sdk < 21:
        report.warn(f"{_mod(spec.gpath)} has minSdk {spec.min_sdk}; current AndroidX libraries need 21+. "
                    "Raise it if the build complains.")


def _android_dirs(spec: ModuleSpec, base: Path):
    for attr, name in (("res_dirs", "res"), ("assets_dirs", "assets")):
        d = base / name
        if d.is_dir():
            getattr(spec, attr).append(d)


# ---------------------------------------------------------------- converters

def from_plain(project: Project, report, source_roots=None, jar_trees=(), jar_files=(),
               junit_container=None, exclude_sources_from_resources=False) -> tuple[list[ModuleSpec], list[str]]:
    root = project.root
    spec = ModuleSpec(dir=root, kotlin=project.kotlin)
    if source_roots is None:
        by_kind = _roots_by_kind(_sources(root), root)
    else:
        by_kind = {"main": [], "test": [], "androidTest": []}
        for r in source_roots:
            by_kind[classify_root(r, root)].append(r)
    spec.java_dirs, spec.test_dirs = by_kind["main"], by_kind["test"]
    spec.exclude_sources_from_resources = exclude_sources_from_resources
    manifests = sorted(find_named(root, "AndroidManifest.xml"), key=lambda p: (len(p.parts), str(p)))
    if manifests:
        spec.android_test_dirs = by_kind["androidTest"]
        _android_from_manifest(spec, manifests[0], report)
        _android_dirs(spec, manifests[0].parent)
        if len(manifests) > 1:
            report.warn("Several AndroidManifest.xml files found; used "
                        f"{rel(manifests[0], root)} as the app manifest.")
        spec.aidl_dirs = [d for d in spec.java_dirs if any(d.rglob("*.aidl"))]
    else:
        spec.test_dirs += by_kind["androidTest"]
        spec.resource_dirs = [d for d in sorted(root.rglob("resources")) if d.is_dir()
                              and classify_root(d, root) == "main" and "build" not in d.parts]
        spec.test_resource_dirs = [d for d in sorted(root.rglob("resources")) if d.is_dir()
                                   and classify_root(d, root) == "test" and "build" not in d.parts]
        if exclude_sources_from_resources:
            spec.resource_dirs = spec.resource_dirs or list(spec.java_dirs)
            spec.test_resource_dirs = spec.test_resource_dirs or list(spec.test_dirs)
        _pick_main(spec, report)
    if not spec.java_dirs and not spec.android:
        return [], []
    for d, pattern in jar_trees:
        if pattern == "*.jar":
            _jar_tree(spec, d)
        else:
            spec.add_dep(f'implementation(fileTree(mapOf("dir" to {kstr(rel(d, root))}, '
                         f'"include" to listOf({kstr(pattern)}))))')
    for j in jar_files:
        spec.add_dep(f"implementation(files({kstr(rel(j, root))}))")
    if not jar_trees and not jar_files:
        for name in ("libs", "lib"):
            _jar_tree(spec, root / name)
    if junit_container == 5:
        for d in JUNIT5:
            spec.add_dep(d)
        spec.junit5 = True
    elif junit_container == 4:
        spec.add_dep(JUNIT4)
    _test_frameworks(spec)
    return [spec], []


def from_vscode_java(project: Project, report):
    root = project.root
    data, _ = jsonc.loads(read_text(root / ".vscode" / "settings.json"))
    source_roots = [(root / p).resolve() for p in data.get("java.project.sourcePaths", [])]
    libs = data.get("java.project.referencedLibraries", [])
    if isinstance(libs, dict):
        libs = libs.get("include", [])
    trees, files = [], []
    for g in libs:
        g = g.replace("\\", "/")
        parts = g.split("/")
        idx = next((i for i, p in enumerate(parts) if any(ch in p for ch in "*?[")), None)
        if idx is None:
            files.append((root / g).resolve())
        else:
            trees.append(((root / "/".join(parts[:idx])).resolve(), "/".join(parts[idx:])))
    report.info("Using java.project.sourcePaths / referencedLibraries from .vscode/settings.json")
    return from_plain(project, report, source_roots=source_roots, jar_trees=trees, jar_files=files)


def _eclipse_classpath(d: Path):
    srcs, libs, junit = [], [], None
    cp = d / ".classpath"
    if cp.is_file():
        try:
            for e in ET.parse(cp).getroot().iter("classpathentry"):
                kind, path = e.get("kind"), e.get("path") or ""
                if kind == "src" and not path.startswith("/") and path not in ("gen",):
                    srcs.append((d / path).resolve())
                elif kind == "lib" and path.endswith(".jar") and not path.startswith("/"):
                    libs.append((d / path).resolve())
                elif kind == "con" and "JUNIT_CONTAINER" in path:
                    junit = 5 if path.rstrip("/").endswith("5") else 4
        except ET.ParseError:
            pass
    return srcs, libs, junit


def from_eclipse_java(project: Project, report):
    srcs, libs, junit = _eclipse_classpath(project.root)
    report.info("Using source folders and jars from Eclipse's .classpath")
    return from_plain(project, report, source_roots=srcs or None, jar_files=libs,
                      junit_container=junit, exclude_sources_from_resources=True)


def _adt_module(d: Path, gpath: str, report, seen: dict) -> list[ModuleSpec]:
    d = d.resolve()
    if d in seen:
        return []
    props = parse_properties(d / "project.properties")
    spec = ModuleSpec(dir=d, gpath=gpath, library=props.get("android.library", "").strip() == "true")
    seen[d] = spec
    spec.kotlin = any(walk_files(d, (".kt",), limit=1))
    target = re.search(r"(\d+)\s*$", props.get("target", ""))
    if target:
        report.info(f"{_mod(gpath)} targeted android-{target.group(1)} in Eclipse; building with the current compileSdk.")
    srcs, libs, _ = _eclipse_classpath(d)
    spec.java_dirs = srcs or ([d / "src"] if (d / "src").is_dir() else [])
    _android_from_manifest(spec, d / "AndroidManifest.xml", report)
    _android_dirs(spec, d)
    spec.aidl_dirs = [s for s in spec.java_dirs if any(s.rglob("*.aidl"))]
    libs_dir = d / "libs"
    _jar_tree(spec, libs_dir)
    for j in libs:
        if j.parent != libs_dir:
            spec.add_dep(f"implementation(files({kstr(rel(j, d))}))")
    if libs_dir.is_dir() and any(libs_dir.rglob("*.so")):
        spec.jni_dirs.append(libs_dir)
    if (d / "jni" / "Android.mk").is_file():
        spec.ndk_build = d / "jni" / "Android.mk"
    if libs_dir.is_dir() and any(p.name.startswith("android-support") for p in libs_dir.glob("*.jar")):
        report.warn(f"{_mod(gpath)} bundles old android-support jars. Modern builds use AndroidX; "
                    "replace them with AndroidX dependencies if the build reports duplicate classes.")
    pg = props.get("proguard.config")
    if pg:
        spec.minify_release = True
        for part in re.split(r"[:;]", pg):
            if part and "${" not in part and (d / part).is_file():
                spec.proguard_files.append(d / part)
        report.warn(f"{_mod(gpath)}: ProGuard was enabled for release exports, so release builds now use R8 "
                    "(stricter). Test a release build.")
    specs = [spec]
    refs = sorted((k, v) for k, v in props.items() if k.startswith("android.library.reference."))
    for _, ref in refs:
        lib_dir = (d / ref).resolve()
        if not (lib_dir / "AndroidManifest.xml").is_file():
            report.warn(f"Library reference '{ref}' in {_mod(gpath)} was not found.")
            continue
        lib_gpath = ":" + gradle_name(lib_dir.name)
        existing = seen.get(lib_dir)
        spec.add_dep(f'implementation(project("{existing.gpath if existing else lib_gpath}"))')
        if not existing:
            specs += _adt_module(lib_dir, lib_gpath, report, seen)
    return specs


def from_eclipse_adt(project: Project, report):
    return _adt_module(project.root, ":", report, {}), []


# ---- Maven

def _strip_ns(el):
    for e in el.iter():
        if isinstance(e.tag, str) and "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]
    return el


class Pom:
    def __init__(self, path: Path):
        self.path = path
        self.dir = path.parent
        self.el = _strip_ns(ET.parse(path).getroot())
        self.parent: Pom | None = None

    def t(self, path, default=None):
        e = self.el.find(path)
        return e.text.strip() if e is not None and e.text and e.text.strip() else default

    @property
    def group(self):
        return self.t("groupId") or self.t("parent/groupId")

    @property
    def artifact(self):
        return self.t("artifactId")

    @property
    def version(self):
        return self.t("version") or self.t("parent/version")

    def chain(self):
        c, p = [], self
        while p:
            c.append(p)
            p = p.parent
        return list(reversed(c))

    def props(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in self.chain():
            pe = p.el.find("properties")
            for e in list(pe) if pe is not None else []:
                if isinstance(e.tag, str):
                    out[e.tag] = (e.text or "").strip()
        for prefix in ("project.", "pom.", ""):
            out[prefix + "groupId"] = self.group or ""
            out[prefix + "artifactId"] = self.artifact or ""
            out[prefix + "version"] = self.version or ""
        out["project.basedir"] = out["basedir"] = str(self.dir)
        out["project.parent.version"] = self.t("parent/version") or ""
        return out

    def sub(self, s: str | None) -> str | None:
        if s is None:
            return None
        props = self.props()
        for _ in range(5):
            new = re.sub(r"\$\{([^}]+)\}", lambda m: props.get(m.group(1), m.group(0)), s)
            if new == s:
                break
            s = new
        return s


SCOPES = {None: ["implementation"], "compile": ["implementation"], "provided": ["compileOnly", "testImplementation"],
          "runtime": ["runtimeOnly"], "test": ["testImplementation"]}
QUIET_PLUGINS = {"maven-compiler-plugin", "maven-surefire-plugin", "maven-jar-plugin", "maven-resources-plugin",
                 "maven-install-plugin", "maven-deploy-plugin", "maven-clean-plugin", "exec-maven-plugin",
                 "kotlin-maven-plugin", "maven-site-plugin", "android-maven-plugin"}


def _load_reactor(root_pom: Path, report) -> list[Pom]:
    poms: dict[Path, Pom] = {}

    def load(path: Path):
        path = path.resolve()
        if path in poms or not path.is_file():
            return poms.get(path)
        try:
            pom = Pom(path)
        except ET.ParseError as e:
            report.warn(f"Could not parse {path}: {e}")
            return None
        poms[path] = pom
        parent_rel = pom.t("parent/relativePath", "../pom.xml")
        if pom.el.find("parent") is not None and parent_rel:
            ppath = (pom.dir / parent_rel)
            ppath = ppath / "pom.xml" if ppath.is_dir() else ppath
            parent = load(ppath) if ppath.is_file() else None
            if parent and parent.artifact == pom.t("parent/artifactId"):
                pom.parent = parent
        for m in pom.el.findall("modules/module"):
            if m.text:
                mp = pom.dir / m.text.strip()
                load(mp / "pom.xml" if mp.is_dir() else mp)
        return pom

    load(root_pom)
    return list(poms.values())


def from_maven(project: Project, report):
    root = project.root
    poms = _load_reactor(root / "pom.xml", report)
    by_coord = {}
    for p in poms:
        if p.dir.resolve() == root:
            gpath = ":"
        else:
            gpath = ":" + ":".join(gradle_name(x) for x in Path(rel(p.dir, root)).parts)
        by_coord[(p.group, p.artifact)] = gpath
    repos: list[str] = []
    specs: list[ModuleSpec] = []
    for pom in poms:
        for r in pom.el.findall("repositories/repository"):
            url = pom.sub(r.findtext("url"))
            if url and "repo.maven.apache.org" not in url and "repo1.maven.org" not in url and url not in repos:
                repos.append(url.strip())
        packaging = pom.t("packaging", "jar")
        if packaging == "pom":
            continue
        spec = _maven_module(pom, root, by_coord, report)
        if spec:
            specs.append(spec)
    return specs, repos


def _maven_module(pom: Pom, root: Path, by_coord, report) -> ModuleSpec | None:
    d = pom.dir.resolve()
    spec = ModuleSpec(dir=d, gpath=by_coord[(pom.group, pom.artifact)], group=pom.sub(pom.group),
                      version=pom.sub(pom.version))
    packaging = pom.t("packaging", "jar")
    plugins = [p.findtext("artifactId") for p in pom.el.findall("build/plugins/plugin")]
    is_android = packaging in ("apk", "aar", "apklib") or "android-maven-plugin" in plugins
    spec.kotlin = "kotlin-maven-plugin" in plugins or (d / "src" / "main" / "kotlin").is_dir()

    def dirs(tag, *defaults):
        custom = pom.sub(pom.t(f"build/{tag}"))
        cands = [Path(custom) if Path(custom).is_absolute() else d / custom] if custom else [d / x for x in defaults]
        return [c for c in cands if c.is_dir()] or ([cands[0]] if custom else [])

    spec.java_dirs = dirs("sourceDirectory", "src/main/java", "src/main/kotlin")
    spec.test_dirs = dirs("testSourceDirectory", "src/test/java", "src/test/kotlin")
    res = [pom.sub(r.findtext("directory")) for r in pom.el.findall("build/resources/resource")]
    spec.resource_dirs = [d / r for r in res if r] if res else [x for x in [d / "src/main/resources"] if x.is_dir()]
    tres = [pom.sub(r.findtext("directory")) for r in pom.el.findall("build/testResources/testResource")]
    spec.test_resource_dirs = [d / r for r in tres if r] if tres else [x for x in [d / "src/test/resources"] if x.is_dir()]

    props = pom.props()
    jv = props.get("maven.compiler.release") or props.get("maven.compiler.source")
    for p in pom.el.findall("build/plugins/plugin") + pom.el.findall("build/pluginManagement/plugins/plugin"):
        if p.findtext("artifactId") == "maven-compiler-plugin":
            jv = p.findtext("configuration/release") or p.findtext("configuration/source") or jv
    spec.java_version = pom.sub(jv) if jv else None
    for p in pom.el.findall("build/plugins/plugin"):
        mc = p.find(".//mainClass")
        if mc is not None and mc.text and not spec.main_class:
            spec.main_class = pom.sub(mc.text.strip())
    unknown = [p for p in plugins if p and p not in QUIET_PLUGINS]
    if unknown:
        report.kept(f"{_mod(spec.gpath)}: Maven plugins with no automatic Gradle equivalent: {', '.join(unknown)} "
                    "(still in pom.xml; add Gradle equivalents by hand if you need them)")

    managed: dict[tuple, str] = {}
    platforms: list[str] = []
    for p in pom.chain():
        for dep in p.el.findall("dependencyManagement/dependencies/dependency"):
            g, a, v = p.sub(dep.findtext("groupId")), p.sub(dep.findtext("artifactId")), p.sub(dep.findtext("version"))
            if dep.findtext("scope") == "import" and dep.findtext("type") == "pom":
                platforms.append(f'implementation(platform("{g}:{a}:{v}"))')
            elif v:
                managed[(g, a)] = v
    external_parent = pom.el.find("parent") is not None and pom.parent is None
    deps: dict[tuple, tuple] = {}
    for p in pom.chain():
        for dep in p.el.findall("dependencies/dependency"):
            g, a = p.sub(dep.findtext("groupId")), p.sub(dep.findtext("artifactId"))
            deps[(g, a)] = (p, dep)
    needs_parent_platform = False
    for (g, a), (p, dep) in deps.items():
        if is_android and g == "com.google.android" and a in ("android", "support-v4"):
            continue
        scope = (dep.findtext("scope") or "").strip() or None
        dtype = dep.findtext("type")
        if dtype in ("test-jar",):
            report.warn(f"{_mod(spec.gpath)}: test-jar dependency {g}:{a} needs manual setup (java-test-fixtures).")
            continue
        if (g, a) in by_coord:
            for conf in SCOPES.get(scope, ["implementation"]):
                spec.add_dep(f'{conf}(project("{by_coord[(g, a)]}"))')
            continue
        if scope == "system":
            sp = p.sub(dep.findtext("systemPath"))
            if sp:
                spec.add_dep(f"implementation(files({kstr(rel(Path(sp), d)) if Path(sp).is_absolute() else kstr(sp)}))")
            continue
        v = p.sub(dep.findtext("version")) or managed.get((g, a))
        if v and "${" in v:
            report.warn(f"{_mod(spec.gpath)}: could not resolve version '{v}' of {g}:{a}.")
        if not v:
            if external_parent:
                needs_parent_platform = True
            elif not platforms:
                report.warn(f"{_mod(spec.gpath)}: no version found for {g}:{a}; add one in build.gradle.kts.")
        notation = ":".join(x for x in (g, a, v, p.sub(dep.findtext("classifier"))) if x)
        excl = [(x.findtext("groupId"), x.findtext("artifactId")) for x in dep.findall("exclusions/exclusion")]
        for conf in SCOPES.get(scope, ["implementation"]):
            line = f'{conf}("{notation}")'
            if excl:
                body = "".join(
                    f"\n    exclude(group = {kstr(eg)})" if ea in (None, "*") else
                    f"\n    exclude(group = {kstr(eg)}, module = {kstr(ea)})" for eg, ea in excl)
                line += " {" + body + "\n}"
            spec.add_dep(line)
        if g == "org.junit.jupiter" or (g == "org.junit" and a == "junit-bom"):
            spec.junit5 = True
    for pl in platforms:
        spec.deps.insert(0, pl)
    if needs_parent_platform:
        pg, pa, pv = pom.t("parent/groupId"), pom.t("parent/artifactId"), pom.t("parent/version")
        spec.deps.insert(0, f'implementation(platform("{pg}:{pa}:{pv}"))')
        report.info(f"{_mod(spec.gpath)}: versions inherited from parent POM {pg}:{pa} are imported as a Gradle platform.")
    if spec.junit5:
        spec.add_dep('testRuntimeOnly("org.junit.platform:junit-platform-launcher")')

    if is_android:
        manifest = next((m for m in (d / "AndroidManifest.xml", d / "src/main/AndroidManifest.xml") if m.is_file()), None)
        if not manifest:
            report.warn(f"{_mod(spec.gpath)}: Android packaging but no AndroidManifest.xml found; built as plain Java.")
        else:
            spec.library = packaging in ("aar", "apklib")
            _android_from_manifest(spec, manifest, report)
            _android_dirs(spec, manifest.parent)
            spec.resource_dirs, spec.test_resource_dirs = [], []
    return spec


# ---- IntelliJ .iml

def _iml_url(url: str, mdir: Path, root: Path) -> Path:
    u = url.replace("$MODULE_DIR$", str(mdir)).replace("$PROJECT_DIR$", str(root))
    u = re.sub(r"^(file|jar)://", "", u)
    return Path(u.split("!/")[0]).resolve()


def _project_libraries(root: Path) -> dict[str, ET.Element]:
    out = {}
    d = root / ".idea" / "libraries"
    for f in d.glob("*.xml") if d.is_dir() else []:
        try:
            for lib in ET.parse(f).getroot().iter("library"):
                if lib.get("name"):
                    out[lib.get("name")] = lib
        except ET.ParseError:
            pass
    return out


def _library_deps(lib: ET.Element, spec: ModuleSpec, conf: str, mdir: Path, root: Path):
    props = lib.find("properties")
    if lib.get("type") == "repository" and props is not None and props.get("maven-id"):
        spec.add_dep(f'{conf}("{props.get("maven-id")}")')
        return
    for r in lib.findall("CLASSES/root"):
        p = _iml_url(r.get("url", ""), mdir, root)
        spec.add_dep(f"{conf}(files({kstr(rel(p, spec.dir))}))")
    for jd in lib.findall("jarDirectory"):
        p = _iml_url(jd.get("url", ""), mdir, root)
        pattern = "**/*.jar" if jd.get("recursive") == "true" else "*.jar"
        spec.add_dep(f'{conf}(fileTree(mapOf("dir" to {kstr(rel(p, spec.dir))}, "include" to listOf("{pattern}"))))')


def from_intellij(project: Project, report):
    root = project.root
    imls: list[Path] = []
    modules_xml = root / ".idea" / "modules.xml"
    if modules_xml.is_file():
        try:
            for m in ET.parse(modules_xml).getroot().iter("module"):
                fp = m.get("filepath") or m.get("fileurl", "").replace("file://", "")
                if fp:
                    imls.append(Path(fp.replace("$PROJECT_DIR$", str(root))).resolve())
        except ET.ParseError:
            pass
    imls = [p for p in imls if p.is_file()] or sorted(root.glob("*.iml"))
    project_libs = _project_libraries(root)
    names = {p.stem: p.parent.resolve() for p in imls}
    run_configs = idea.read_run_configs(root)
    level = idea.read_language_level(root)
    specs = []
    for iml in imls:
        mdir = iml.parent.resolve()
        gpath = ":" if mdir == root else ":" + gradle_name(iml.stem)
        spec = ModuleSpec(dir=mdir, gpath=gpath, java_version=level)
        try:
            el = ET.parse(iml).getroot()
        except ET.ParseError as e:
            report.warn(f"Could not parse {iml.name}: {e}")
            continue
        comp = next((c for c in el.findall("component") if c.get("name") == "NewModuleRootManager"), None)
        if comp is None:
            continue
        if comp.get("LANGUAGE_LEVEL"):
            spec.java_version = idea.language_level_to_version(comp.get("LANGUAGE_LEVEL")) or spec.java_version
        for sf in comp.iter("sourceFolder"):
            p = _iml_url(sf.get("url", ""), mdir, root)
            kind = sf.get("type")
            if kind == "java-resource":
                spec.resource_dirs.append(p)
            elif kind == "java-test-resource":
                spec.test_resource_dirs.append(p)
            elif sf.get("isTestSource") == "true":
                spec.test_dirs.append(p)
            else:
                spec.java_dirs.append(p)
        scopes = {"TEST": "testImplementation", "PROVIDED": "compileOnly", "RUNTIME": "runtimeOnly"}
        for oe in comp.findall("orderEntry"):
            conf = scopes.get(oe.get("scope"), "implementation")
            if oe.get("type") == "module-library":
                lib = oe.find("library")
                if lib is not None:
                    _library_deps(lib, spec, conf, mdir, root)
            elif oe.get("type") == "library" and oe.get("level") == "project":
                lib = project_libs.get(oe.get("name"))
                if lib is not None:
                    _library_deps(lib, spec, conf, root, root)
                else:
                    report.warn(f"{_mod(gpath)}: library '{oe.get('name')}' not found in .idea/libraries.")
            elif oe.get("type") == "module" and oe.get("module-name") in names:
                dep_dir = names[oe.get("module-name")]
                dep_gpath = ":" if dep_dir == root else ":" + gradle_name(oe.get("module-name"))
                spec.add_dep(f'{conf}(project("{dep_gpath}"))')
        spec.kotlin = any(walk_files(mdir, (".kt",), limit=1))
        facet = next((f for f in el.iter("facet") if f.get("type") == "android"), None)
        if facet is not None:
            opts = {o.get("name"): o.get("value") or "" for o in facet.iter("option")}
            manifest = mdir / opts.get("MANIFEST_FILE_RELATIVE_PATH", "/AndroidManifest.xml").lstrip("/")
            spec.library = opts.get("LIBRARY_PROJECT") == "true"
            if manifest.is_file():
                _android_from_manifest(spec, manifest, report)
                for attr, key, default in (("res_dirs", "RES_FOLDER_RELATIVE_PATH", "/res"),
                                           ("assets_dirs", "ASSETS_FOLDER_RELATIVE_PATH", "/assets")):
                    p = mdir / opts.get(key, default).lstrip("/")
                    if p.is_dir():
                        getattr(spec, attr).append(p)
                _jar_tree(spec, mdir / opts.get("LIBS_FOLDER_RELATIVE_PATH", "/libs").lstrip("/"))
                spec.resource_dirs, spec.test_resource_dirs = [], []
            else:
                report.warn(f"{_mod(gpath)}: Android facet points at a missing manifest ({manifest}).")
        else:
            rc = next((c for c in run_configs if c.type == idea.APPLICATION and c.main_class
                       and (c.module == iml.stem or len(imls) == 1)), None)
            if rc:
                spec.main_class = rc.main_class
            else:
                _pick_main(spec, report)
        _test_frameworks(spec)
        specs.append(spec)
    return specs, []


CONVERTERS = {"maven": from_maven, "intellij": from_intellij, "eclipse-adt": from_eclipse_adt,
              "eclipse-java": from_eclipse_java, "vscode-java": from_vscode_java, "plain": from_plain}


def convert(project: Project, tx, report, opts) -> bool:
    specs, repos = CONVERTERS[project.kind](project, report)
    if not specs:
        report.warn("No Java/Kotlin sources or Android manifest found, so there is nothing to build with Gradle.")
        return False
    root = project.root
    written = []
    for s in specs:
        existing = first_existing(s.dir, BUILD_FILES)
        if existing:
            report.warn(f"{rel(existing, root)} already exists; left it alone.")
            continue
        text = render_android(s, opts) if s.android else render_jvm(s, opts)
        tx.write(s.dir / "build.gradle.kts", text)
        written.append(s)
        if s.android and s.manifest:
            original = s.manifest.read_bytes().decode("utf-8", errors="replace")
            new, removed = android.strip_for_agp(original)
            if removed:
                tx.write(s.manifest, new)
                report.translated(f"{rel(s.manifest, root)}: moved {', '.join(removed)} into build.gradle.kts "
                                  "(current Android Gradle Plugin rejects them in the manifest)")
    if not written:
        return False
    tx.write(root / "settings.gradle.kts", render_settings(project.name or root.name, specs, root, repos, opts))
    wrapper = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not wrapper.exists():
        tx.write(wrapper, wrapper_properties(opts.gradle))
    if not (root / "gradle.properties").exists():
        tx.write(root / "gradle.properties", "org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8\n")
    report.translated(f"{project.describe()} converted to Gradle ({len(written)} module"
                      f"{'s' if len(written) != 1 else ''}); sources were not moved")
    if not (root / "gradlew").exists():
        report.todo("Create the Gradle wrapper scripts once: open the project in Android Studio (it syncs "
                    "using gradle/wrapper/gradle-wrapper.properties) and run the 'wrapper' task from the "
                    "Gradle panel, or run `gradle wrapper` if Gradle is installed.")
    return True
