"""Every file ideswap writes goes through a Transaction so it can be undone exactly."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from .util import relkey

STATE_DIR = ".ideswap"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Transaction:
    def __init__(self, root: Path, command: str = ""):
        self.root = Path(root).resolve()
        self.command = command
        hist = self.root / STATE_DIR / "history"
        base = time.strftime("%Y%m%d-%H%M%S")
        self.op_id, n = base, 1
        while (hist / f"{self.op_id}.json").exists():
            n += 1
            self.op_id = f"{base}-{n}"
        self.changes: list[dict] = []
        self._by_path: dict[str, dict] = {}

    def write(self, path: Path, content: str | bytes) -> bool:
        path = Path(path).resolve()
        data = content.encode("utf-8") if isinstance(content, str) else content
        old = path.read_bytes() if path.is_file() else None
        if old == data:
            return False
        key = relkey(self.root, path)
        rec = self._by_path.get(key)
        if rec is None:
            rec = {"path": key, "action": "modified" if old is not None else "created",
                   "backup": None, "dirs": []}
            if old is not None:
                backup = self.root / STATE_DIR / "backups" / self.op_id / key.replace("../", "__up__/")
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(old)
                rec["backup"] = relkey(self.root, backup)
            else:
                d = path.parent
                while not d.exists():
                    rec["dirs"].append(relkey(self.root, d))
                    d = d.parent
            self._by_path[key] = rec
            self.changes.append(rec)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        rec["sha"] = sha(data)
        self._save_history()
        return True

    def _save_history(self):
        state = self.root / STATE_DIR
        (state / "history").mkdir(parents=True, exist_ok=True)
        ignore = state / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
        record = {"op": self.op_id, "command": self.command, "changes": self.changes}
        (state / "history" / f"{self.op_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


def undo(root: Path) -> list[str]:
    root = Path(root).resolve()
    state = root / STATE_DIR
    history = sorted((state / "history").glob("*.json"), key=lambda p: p.stem) if state.is_dir() else []
    if not history:
        return ["Nothing to undo."]
    latest = history[-1]
    rec = json.loads(latest.read_text(encoding="utf-8"))
    msgs = [f"Undoing '{rec.get('command') or rec['op']}' ({rec['op']})"]
    skipped = False
    for c in reversed(rec["changes"]):
        p = (root / c["path"]).resolve()
        cur = p.read_bytes() if p.is_file() else None
        if cur is None or sha(cur) != c.get("sha"):
            msgs.append(f"  skipped {c['path']}: changed or removed since ideswap wrote it")
            skipped = True
            continue
        if c["action"] == "created":
            p.unlink()
            for d in c["dirs"]:
                try:
                    (root / d).rmdir()
                except OSError:
                    pass
            msgs.append(f"  removed  {c['path']}")
        else:
            p.write_bytes((root / c["backup"]).read_bytes())
            msgs.append(f"  restored {c['path']}")
    backups = state / "backups" / rec["op"]
    if skipped and backups.is_dir():
        msgs.append(f"Original copies of skipped files are kept in {backups}")
    else:
        shutil.rmtree(backups, ignore_errors=True)
    latest.unlink()
    if not any((state / "history").glob("*.json")) and not skipped:
        shutil.rmtree(state, ignore_errors=True)
    return msgs
