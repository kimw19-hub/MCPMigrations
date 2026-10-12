"""JSON / JSONL helpers. All writers create parent directories."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    """Write records (streamed) to ``path``; return the number written.

    The write is atomic: records go to a temporary file that is renamed into
    place only on success. Stages resume by checking whether an output file
    exists, so a half-written file left by a crash or an interrupt would be
    mistaken for completed work and silently truncate the dataset.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    count = 0
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return count


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def write_lines(path: Path, lines: Iterable[str]) -> int:
    items = list(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{item}\n" for item in items), encoding="utf-8")
    return len(items)


def read_lines(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def first_line(text: str | None, limit: int = 200) -> str:
    """First line of a commit message or title, truncated."""
    if not text:
        return ""
    return text.splitlines()[0][:limit]
