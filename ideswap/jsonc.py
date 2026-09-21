"""VS Code's JSON-with-comments: strips // and /* */ comments and trailing commas."""
from __future__ import annotations

import json


def _skip_ws_comments(text: str, i: int) -> int:
    n = len(text)
    while i < n:
        if text[i].isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            break
    return i


def loads(text: str):
    """Returns (data, had_comments)."""
    if text.startswith("﻿"):
        text = text[1:]
    out: list[str] = []
    had_comments = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i) or text.startswith("/*", i):
            had_comments = True
            i = _skip_ws_comments(text, i)
            out.append(" ")
        elif c == ",":
            k = _skip_ws_comments(text, i + 1)
            if k < n and text[k] in "}]":
                i += 1
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    body = "".join(out)
    if not body.strip():
        return None, had_comments
    return json.loads(body), had_comments


def dumps(data) -> str:
    return json.dumps(data, indent=4, ensure_ascii=False) + "\n"
