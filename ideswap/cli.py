from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__
from .convert import Options, run
from .detect import detect
from .fsops import STATE_DIR, Transaction, undo
from .report import Report
from .util import rel

REGENERABLE = {".gradle", STATE_DIR, ".cxx", ".externalNativeBuild", ".dart_tool", "node_modules"}


def _ignore(directory, names):
    d = Path(directory)
    skip = {n for n in names if n in REGENERABLE}
    if "build" in names and (any((d / b).is_file() for b in ("build.gradle", "build.gradle.kts"))
                             or (d / "build" / "intermediates").is_dir() or (d / "build" / "tmp").is_dir()):
        skip.add("build")
    return skip


def copy_project(src: Path, dest: Path):
    shutil.copytree(src, dest, ignore=_ignore, dirs_exist_ok=True)


DIRECTIONS = {"to-studio": "studio", "to-vscode": "vscode", "sync": "sync"}


def _convert(args) -> int:
    src = Path(args.path).resolve()
    if not src.is_dir():
        print(f"error: {src} is not a folder", file=sys.stderr)
        return 2
    opts = Options(agp=args.agp, gradle=args.gradle_version, kotlin=args.kotlin_version,
                   compile_sdk=args.compile_sdk, java_version=args.java_version,
                   convert_build=not args.no_build_convert, sdk=args.sdk)
    direction = DIRECTIONS[args.cmd]
    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="ideswap-") as tmp:
            work = Path(tmp) / src.name
            copy_project(src, work)
            tx, report = Transaction(work, args.cmd), Report()
            run(work, direction, tx, report, opts)
            print("DRY RUN - nothing was written.\n")
            print(report.render(tx.changes).replace(str(work), str(src)))
        return 0
    root = src
    if args.dest:
        dest = Path(args.dest).resolve()
        if dest.exists() and any(dest.iterdir()):
            print(f"error: destination {dest} is not empty", file=sys.stderr)
            return 2
        copy_project(src, dest)
        root = dest
    tx, report = Transaction(root, args.cmd), Report()
    if args.dest:
        report.info(f"Copied {src} -> {root} (build outputs and caches skipped; they regenerate)")
    run(root, direction, tx, report, opts)
    print(report.render(tx.changes))
    if tx.changes:
        print(f"\nUndo everything this run wrote: ideswap undo \"{root}\"")
    return 0


def _detect(args) -> int:
    p = detect(Path(args.path))
    print(p.describe())
    print(f"  folder:          {p.root}")
    if p.gradle_root:
        print(f"  Gradle root:     {p.gradle_root}")
    print(f"  Android Studio:  {'has .idea' if (p.studio_root / '.idea').is_dir() else 'no .idea yet'}"
          f"  (opens {p.studio_root})")
    print(f"  VS Code:         {'has .vscode' if (p.root / '.vscode').is_dir() else 'no .vscode yet'}")
    for m in p.modules:
        kind = "android app" if m.app else "android library" if m.library else "jvm app" if m.jvm_app else "module"
        extra = f"  id={m.application_id}" if m.application_id else ""
        extra += f"  launcher={m.launcher}" if m.launcher else ""
        print(f"  module {m.gpath:<16} {kind:<16} {rel(m.dir, p.root)}{extra}")
    for n in p.notes:
        print(f"  note: {n}")
    if not p.gradle_ready and p.kind != "unknown":
        print("\n  Not Gradle yet: to-studio / to-vscode will generate a Gradle build (sources are not moved).")
    return 0


def _undo(args) -> int:
    for line in undo(Path(args.path)):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ideswap",
        description="Move Android/Java/Kotlin projects between Android Studio and VS Code without losing settings.")
    ap.add_argument("--version", action="version", version=f"ideswap {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    helps = {
        "detect": "show what kind of project a folder is and what the tool would do",
        "to-studio": "make the project open cleanly in Android Studio (carries over VS Code settings)",
        "to-vscode": "make the project work in VS Code (carries over Android Studio settings)",
        "sync": "both directions: make the project ready for either IDE",
        "undo": "revert the most recent ideswap run in this project",
    }
    for name, text in helps.items():
        sp = sub.add_parser(name, help=text, description=text)
        sp.add_argument("path", nargs="?", default=".", help="project folder (default: current folder)")
        if name in DIRECTIONS:
            sp.add_argument("--dest", help="copy the project here first and convert the copy")
            sp.add_argument("--dry-run", action="store_true", help="show what would change without writing")
            sp.add_argument("--no-build-convert", action="store_true",
                            help="do not generate a Gradle build for non-Gradle projects")
            sp.add_argument("--sdk", help="Android SDK folder (default: auto-detect)")
            sp.add_argument("--agp", default=Options.agp, help=f"Android Gradle Plugin version for generated builds "
                                                              f"(default {Options.agp})")
            sp.add_argument("--gradle-version", default=Options.gradle, help=f"default {Options.gradle}")
            sp.add_argument("--kotlin-version", default=Options.kotlin,
                            help=f"Kotlin JVM plugin version for non-Android builds (default {Options.kotlin})")
            sp.add_argument("--compile-sdk", type=int, default=Options.compile_sdk,
                            help=f"default {Options.compile_sdk}")
            sp.add_argument("--java-version", type=int, default=Options.java_version,
                            help=f"Java target for generated Android builds (default {Options.java_version})")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "detect":
        return _detect(args)
    if args.cmd == "undo":
        return _undo(args)
    return _convert(args)


if __name__ == "__main__":
    sys.exit(main())
