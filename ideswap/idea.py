"""Android Studio / IntelliJ `.idea` folder: read and write the parts that have VS Code equivalents."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import quoteattr

GRADLE = "GradleRunConfiguration"
APPLICATION = "Application"
FLUTTER = "FlutterRunConfigurationType"
ANDROID_APP = "AndroidRunConfigurationType"


@dataclass
class RunConfig:
    name: str
    type: str
    gradle_tasks: list[str] = field(default_factory=list)
    gradle_args: str = ""
    project_path: str = ""
    main_class: str | None = None
    module: str | None = None
    program: str | None = None
    program_args: str = ""
    vm_args: str = ""
    source: str = ""


def project_rel(value: str | None) -> str:
    if not value:
        return ""
    v = value.replace("\\", "/")
    if v.startswith("$PROJECT_DIR$"):
        v = v[len("$PROJECT_DIR$"):]
    return v.strip("/")


def _parse(el: ET.Element, source: str) -> RunConfig:
    rc = RunConfig(name=el.get("name"), type=el.get("type"), source=source)
    if rc.type == GRADLE:
        ess = el.find("ExternalSystemSettings")
        for opt in ess.findall("option") if ess is not None else []:
            n = opt.get("name")
            if n == "externalProjectPath":
                rc.project_path = project_rel(opt.get("value"))
            elif n == "scriptParameters":
                rc.gradle_args = opt.get("value") or ""
            elif n == "taskNames":
                rc.gradle_tasks = [o.get("value") for o in opt.iter("option") if o is not opt and o.get("value")]
    else:
        for opt in el.findall("option"):
            n, v = opt.get("name"), opt.get("value") or ""
            if n == "MAIN_CLASS_NAME":
                rc.main_class = v
            elif n == "filePath":
                rc.program = project_rel(v)
            elif n == "PROGRAM_PARAMETERS":
                rc.program_args = v
            elif n == "VM_PARAMETERS":
                rc.vm_args = v
        mod = el.find("module")
        if mod is not None:
            rc.module = mod.get("name")
    return rc


def read_run_configs(studio_root: Path) -> list[RunConfig]:
    found: dict[str, RunConfig] = {}
    d = studio_root / ".idea" / "runConfigurations"
    for f in sorted(d.glob("*.xml")) if d.is_dir() else []:
        try:
            tree = ET.parse(f)
        except ET.ParseError:
            continue
        for el in tree.getroot().iter("configuration"):
            if el.get("name") and el.get("type"):
                found.setdefault(el.get("name"), _parse(el, f".idea/runConfigurations/{f.name}"))
    ws = studio_root / ".idea" / "workspace.xml"
    if ws.is_file():
        try:
            comps = ET.parse(ws).getroot().findall("component")
        except ET.ParseError:
            comps = []
        for comp in comps:
            if comp.get("name") != "RunManager":
                continue
            for el in comp.iter("configuration"):
                if el.get("default") == "true" or el.get("temporary") == "true":
                    continue
                if el.get("name") and el.get("type"):
                    found.setdefault(el.get("name"), _parse(el, ".idea/workspace.xml"))
    return list(found.values())


def config_filename(studio_root: Path, name: str) -> Path:
    base = re.sub(r"[^\w]", "_", name) or "config"
    d = studio_root / ".idea" / "runConfigurations"
    p, n = d / f"{base}.xml", 1
    while p.exists():
        n += 1
        p = d / f"{base}_{n}.xml"
    return p


def _wrap(inner: str) -> str:
    return f'<component name="ProjectRunConfigurationManager">\n{inner}</component>\n'


def gradle_config_xml(name: str, tasks: list[str], args: str, project_path: str) -> str:
    path = "$PROJECT_DIR$" + (f"/{project_path}" if project_path else "")
    task_opts = "".join(f"          <option value={quoteattr(t)} />\n" for t in tasks)
    return _wrap(
        f'  <configuration default="false" name={quoteattr(name)} type="{GRADLE}" factoryName="Gradle">\n'
        "    <ExternalSystemSettings>\n"
        '      <option name="executionName" />\n'
        f'      <option name="externalProjectPath" value={quoteattr(path)} />\n'
        '      <option name="externalSystemIdString" value="GRADLE" />\n'
        f'      <option name="scriptParameters" value={quoteattr(args)} />\n'
        '      <option name="taskDescriptions">\n        <list />\n      </option>\n'
        '      <option name="taskNames">\n        <list>\n'
        f"{task_opts}"
        "        </list>\n      </option>\n"
        '      <option name="vmOptions" />\n'
        "    </ExternalSystemSettings>\n"
        "    <ExternalSystemDebugServerProcess>true</ExternalSystemDebugServerProcess>\n"
        "    <ExternalSystemReattachDebugProcess>true</ExternalSystemReattachDebugProcess>\n"
        "    <DebugAllEnabled>false</DebugAllEnabled>\n"
        "    <RunAsTest>false</RunAsTest>\n"
        '    <method v="2" />\n'
        "  </configuration>\n")


def application_config_xml(name: str, main_class: str, module: str | None,
                           program_args: str = "", vm_args: str = "") -> str:
    lines = [f'  <configuration default="false" name={quoteattr(name)} type="{APPLICATION}" factoryName="Application">',
             f'    <option name="MAIN_CLASS_NAME" value={quoteattr(main_class)} />']
    if module:
        lines.append(f"    <module name={quoteattr(module)} />")
    if program_args:
        lines.append(f'    <option name="PROGRAM_PARAMETERS" value={quoteattr(program_args)} />')
    if vm_args:
        lines.append(f'    <option name="VM_PARAMETERS" value={quoteattr(vm_args)} />')
    lines += ['    <method v="2">', '      <option name="Make" enabled="true" />', "    </method>", "  </configuration>"]
    return _wrap("\n".join(lines) + "\n")


def flutter_config_xml(name: str, program: str) -> str:
    return _wrap(
        f'  <configuration default="false" name={quoteattr(name)} type="{FLUTTER}" factoryName="Flutter">\n'
        f'    <option name="filePath" value={quoteattr("$PROJECT_DIR$/" + program)} />\n'
        '    <method v="2" />\n'
        "  </configuration>\n")


def read_gradle_jvm(studio_root: Path) -> str | None:
    f = studio_root / ".idea" / "gradle.xml"
    if not f.is_file():
        return None
    try:
        for opt in ET.parse(f).getroot().iter("option"):
            if opt.get("name") == "gradleJvm":
                return opt.get("value")
    except ET.ParseError:
        pass
    return None


def gradle_xml(jvm: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project version="4">\n'
        '  <component name="GradleMigrationSettings" migrationVersion="1" />\n'
        '  <component name="GradleSettings">\n'
        '    <option name="linkedExternalProjectsSettings">\n'
        "      <GradleProjectSettings>\n"
        '        <option name="externalProjectPath" value="$PROJECT_DIR$" />\n'
        f'        <option name="gradleJvm" value={quoteattr(jvm)} />\n'
        "      </GradleProjectSettings>\n"
        "    </option>\n"
        "  </component>\n"
        "</project>\n")


def read_code_style(studio_root: Path) -> dict[str, dict[str, str]]:
    """Per-language indent settings from a project code style, as .editorconfig sections."""
    styles = studio_root / ".idea" / "codeStyles"
    cfg, proj = styles / "codeStyleConfig.xml", styles / "Project.xml"
    if not (cfg.is_file() and proj.is_file()):
        return {}
    try:
        per_project = any(o.get("name") == "USE_PER_PROJECT_SETTINGS" and o.get("value") == "true"
                          for o in ET.parse(cfg).getroot().iter("option"))
        root = ET.parse(proj).getroot()
    except ET.ParseError:
        return {}
    if not per_project:
        return {}
    globs = {"JAVA": "*.java", "kotlin": "*.{kt,kts}", "XML": "*.xml"}
    out: dict[str, dict[str, str]] = {}
    for css in root.iter("codeStyleSettings"):
        glob = globs.get(css.get("language"))
        io = css.find("indentOptions")
        if not glob or io is None:
            continue
        section = {}
        for opt in io.findall("option"):
            if opt.get("name") == "INDENT_SIZE":
                section["indent_size"] = opt.get("value")
            elif opt.get("name") == "USE_TAB_CHARACTER":
                section["indent_style"] = "tab" if opt.get("value") == "true" else "space"
        if section:
            out[glob] = section
    return out


def read_language_level(studio_root: Path) -> str | None:
    f = studio_root / ".idea" / "misc.xml"
    if not f.is_file():
        return None
    try:
        for comp in ET.parse(f).getroot().iter("component"):
            if comp.get("name") == "ProjectRootManager" and comp.get("languageLevel"):
                return language_level_to_version(comp.get("languageLevel"))
    except ET.ParseError:
        pass
    return None


def language_level_to_version(level: str | None) -> str | None:
    m = re.match(r"JDK_(\d+)(?:_(\d+))?", level or "")
    if not m:
        return None
    return f"{m.group(1)}.{m.group(2)}" if m.group(1) == "1" else m.group(1)
