import contextlib
import io
import tempfile
import textwrap
import unittest
from pathlib import Path

from ideswap.cli import main
from ideswap.fsops import STATE_DIR


def make_tree(root: Path, files: dict):
    for path, content in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8", newline="")


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and STATE_DIR not in p.parts}


def cli(*args) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main([str(a) for a in args])
    assert code == 0, buf.getvalue()
    return buf.getvalue()


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def read(self, path) -> str:
        return (self.tmp / path).read_text(encoding="utf-8")
