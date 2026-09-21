from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = "{%s}" % ANDROID_NS
MAIN = "android.intent.action.MAIN"
LAUNCHER = "android.intent.category.LAUNCHER"


@dataclass
class ManifestInfo:
    package: str | None = None
    min_sdk: int | None = None
    target_sdk: int | None = None
    launcher: str | None = None


def _int(v):
    return int(v) if v and v.strip().isdigit() else None


def parse_manifest(path: Path) -> ManifestInfo:
    info = ManifestInfo()
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return info
    info.package = root.get("package")
    sdk = root.find("uses-sdk")
    if sdk is not None:
        info.min_sdk = _int(sdk.get(A + "minSdkVersion"))
        info.target_sdk = _int(sdk.get(A + "targetSdkVersion"))
    for el in root.iter():
        if el.tag not in ("activity", "activity-alias"):
            continue
        for f in el.findall("intent-filter"):
            actions = {a.get(A + "name") for a in f.findall("action")}
            cats = {c.get(A + "name") for c in f.findall("category")}
            if MAIN in actions and LAUNCHER in cats and el.get(A + "name"):
                info.launcher = el.get(A + "name")
                return info
    return info


def resolve_class(name: str, namespace: str | None) -> str:
    if name.startswith(".") and namespace:
        return namespace + name
    if "." not in name and namespace:
        return f"{namespace}.{name}"
    return name


def strip_for_agp(text: str) -> tuple[str, list[str]]:
    """AGP 8+ rejects `package=` and SDK versions in the source manifest; they move to Gradle."""
    removed: list[str] = []
    m = re.search(r"<manifest\b[^>]*>", text, re.S)
    if m:
        tag = m.group(0)
        new_tag, n = re.subn(r"\s+package\s*=\s*(\"[^\"]*\"|'[^']*')", "", tag)
        if n:
            removed.append("package")
            text = text[:m.start()] + new_tag + text[m.end():]
    prefix_m = re.search(r"xmlns:(\w+)\s*=\s*[\"']" + re.escape(ANDROID_NS), text)
    prefix = prefix_m.group(1) if prefix_m else "android"
    m = re.search(r"<uses-sdk\b[^>]*>", text, re.S)
    if m:
        tag = m.group(0)
        for attr in ("minSdkVersion", "targetSdkVersion"):
            tag, n = re.subn(r"\s+%s:%s\s*=\s*(\"[^\"]*\"|'[^']*')" % (prefix, attr), "", tag)
            if n:
                removed.append(attr)
        text = text[:m.start()] + tag + text[m.end():]
    return text, removed
