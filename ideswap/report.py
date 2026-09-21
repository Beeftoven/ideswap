from __future__ import annotations

SECTIONS = [
    ("info", "Project"),
    ("translated", "Translated between IDEs"),
    ("kept", "Kept as-is (no equivalent in the other IDE, left untouched)"),
    ("warning", "Warnings"),
    ("todo", "Next steps"),
]


class Report:
    def __init__(self):
        self.items: list[tuple[str, str]] = []

    def add(self, kind: str, msg: str):
        if (kind, msg) not in self.items:
            self.items.append((kind, msg))

    def info(self, msg): self.add("info", msg)
    def translated(self, msg): self.add("translated", msg)
    def kept(self, msg): self.add("kept", msg)
    def warn(self, msg): self.add("warning", msg)
    def todo(self, msg): self.add("todo", msg)

    def of(self, kind):
        return [m for k, m in self.items if k == kind]

    def render(self, changes: list[dict] | None = None) -> str:
        lines: list[str] = []
        for kind, title in SECTIONS[:1]:
            for m in self.of(kind):
                lines.append(m)
        if changes is not None:
            lines.append("")
            lines.append("Files written" if changes else "Files written: none (already in sync)")
            for c in changes:
                mark = "+" if c["action"] == "created" else "~"
                note = "" if c["action"] == "created" else "  (original backed up)"
                lines.append(f"  {mark} {c['path']}{note}")
        for kind, title in SECTIONS[1:]:
            msgs = self.of(kind)
            if msgs:
                lines.append("")
                lines.append(title)
                lines.extend(f"  - {m}" for m in msgs)
        return "\n".join(lines)
