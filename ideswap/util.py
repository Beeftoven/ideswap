from __future__ import annotations

import os
import re
from pathlib import Path

SKIP_DIRS = {
    "build", "out", "bin", "gen", "target", "node_modules", "Pods",
    "intermediates", "generated",
}

PKG_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def rel(path: Path, base: Path) -> str:
    r = os.path.relpath(Path(path), Path(base)).replace("\\", "/")
    return "." if r == "" else r


def relkey(root: Path, path: Path) -> str:
    return rel(path, root)


def kstr(s: str) -> str:
    """Kotlin string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def gradle_name(name: str) -> str:
    cleaned = re.sub(r'[/\\:<>"?*|\s]+', "-", name).strip(".-")
    return cleaned or "project"


def walk_files(root: Path, exts: tuple[str, ...], limit: int = 50000):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(filenames):
            if f.endswith(exts):
                yield Path(dirpath) / f
                count += 1
                if count >= limit:
                    return


def find_named(root: Path, name: str, limit: int = 50):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        if name in filenames:
            out.append(Path(dirpath) / name)
            if len(out) >= limit:
                break
    return out


def has_files(root: Path, exts: tuple[str, ...]) -> bool:
    return next(walk_files(root, exts), None) is not None


def parse_properties(path: Path) -> dict[str, str]:
    props: dict[str, str] = {}
    if not Path(path).is_file():
        return props
    logical = ""
    for raw in read_text(path).splitlines():
        line = raw.strip() if not logical else raw.lstrip()
        if not logical and (not line or line[0] in "#!"):
            continue
        if line.endswith("\\") and not line.endswith("\\\\"):
            logical += line[:-1]
            continue
        logical += line
        m = re.match(r"((?:\\.|[^=:\s])+)\s*[=:\s]\s*(.*)", logical)
        if m:
            key = re.sub(r"\\(.)", r"\1", m.group(1))
            props[key] = re.sub(r"\\(.)", r"\1", m.group(2))
        logical = ""
    return props


def escape_property(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("=", "\\=")


def source_root_of(file: Path) -> Path:
    head = read_text(file)[:20000]
    m = PKG_RE.search(head)
    parent = file.parent
    if not m:
        return parent
    p = parent
    for part in reversed(m.group(1).split(".")):
        if p.name != part:
            return parent
        p = p.parent
    return p


def minimal_roots(roots) -> list[Path]:
    chosen: list[Path] = []
    for r in sorted(set(roots), key=lambda p: (len(p.parts), str(p))):
        if not any(r == c or c in r.parents for c in chosen):
            chosen.append(r)
    return sorted(chosen)


def classify_root(root: Path, project_root: Path) -> str:
    parts = [p.lower() for p in Path(rel(root, project_root)).parts]
    if "androidtest" in parts:
        return "androidTest"
    if any(p in ("test", "tests", "testfixtures") for p in parts):
        return "test"
    return "main"
