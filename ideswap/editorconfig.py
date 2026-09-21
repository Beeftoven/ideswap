""".editorconfig is read natively by Android Studio and by VS Code (EditorConfig extension),
so it is the neutral place for formatting settings."""
from __future__ import annotations

CHARSETS = {"utf8": "utf-8", "utf8bom": "utf-8-bom", "utf16le": "utf-16le",
            "utf16be": "utf-16be", "iso88591": "latin1"}
LANG_GLOBS = {"java": "*.java", "kotlin": "*.{kt,kts}", "xml": "*.xml", "groovy": "*.gradle",
              "json": "*.json", "jsonc": "*.json", "markdown": "*.md", "dart": "*.dart",
              "javascript": "*.js", "typescript": "*.ts", "javascriptreact": "*.jsx",
              "typescriptreact": "*.tsx", "properties": "*.properties", "yaml": "*.{yml,yaml}"}


def _section_from_settings(settings: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(settings.get("editor.insertSpaces"), bool):
        out["indent_style"] = "space" if settings["editor.insertSpaces"] else "tab"
    if isinstance(settings.get("editor.tabSize"), int):
        out["indent_size"] = str(settings["editor.tabSize"])
    if settings.get("files.encoding") in CHARSETS:
        out["charset"] = CHARSETS[settings["files.encoding"]]
    eol = {"\n": "lf", "\r\n": "crlf"}.get(settings.get("files.eol"))
    if eol:
        out["end_of_line"] = eol
    for key, ec in (("files.insertFinalNewline", "insert_final_newline"),
                    ("files.trimTrailingWhitespace", "trim_trailing_whitespace")):
        if isinstance(settings.get(key), bool):
            out[ec] = "true" if settings[key] else "false"
    return out


def from_vscode_settings(settings: dict) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Returns (sections, language scopes that could not be mapped)."""
    sections: dict[str, dict[str, str]] = {}
    top = _section_from_settings(settings)
    if top:
        sections["*"] = top
    unmapped = []
    for key, value in settings.items():
        if key.startswith("[") and key.endswith("]") and isinstance(value, dict):
            sec = _section_from_settings(value)
            if not sec:
                continue
            glob = LANG_GLOBS.get(key[1:-1])
            if glob:
                sections[glob] = sec
            else:
                unmapped.append(key)
    return sections, unmapped


def render(sections: dict[str, dict[str, str]]) -> str:
    lines = ["root = true"]
    order = sorted(sections, key=lambda s: (s != "*", s))
    for sec in order:
        lines += ["", f"[{sec}]"] + [f"{k} = {v}" for k, v in sections[sec].items()]
    return "\n".join(lines) + "\n"
