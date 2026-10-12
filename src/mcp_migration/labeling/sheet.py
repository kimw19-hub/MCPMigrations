"""Reading, validating and writing one coder's labeling sheet.

The sheet stays the record. The app edits the same CSV that `labels` reads, so
nothing downstream changes and a coder can still fall back to a spreadsheet.
Every save re-reads the file and rewrites it atomically: an edit made outside
the app to another row survives, and an interrupted write cannot truncate the
sheet.
"""

from __future__ import annotations

import csv
import os
import threading
from collections.abc import Mapping
from pathlib import Path

from mcp_migration.report.sampling import (
    AUTHOR_KINDS_VERIFIED,
    CODEBOOK_COLUMNS,
    DEPRECATED_FEATURES,
    DETECTOR_VERDICTS,
    DISPOSITIONS,
    END_STATES,
    TRIGGERS,
)

ALLOWED: dict[str, tuple[str, ...]] = {
    "detector_correct": DETECTOR_VERDICTS,
    "end_state": END_STATES,
    "trigger": TRIGGERS,
    "author_kind_verified": AUTHOR_KINDS_VERIFIED,
}
# Filled only when the detector was right; see "Columns you fill" in the codebook.
REQUIRED_IF_CORRECT = ("end_state", "trigger", "author_kind_verified")

TODO, INCOMPLETE, DONE = "todo", "incomplete", "done"


class InvalidLabel(ValueError):
    """A value the codebook does not allow, or an inconsistent combination."""


def normalize_deprecated(value: str) -> str:
    """Validate ``feature:disposition`` pairs and put them in a canonical order."""
    pairs: dict[str, str] = {}
    for part in value.split(";"):
        part = part.strip().lower()
        if not part:
            continue
        feature, sep, disposition = (s.strip() for s in part.partition(":"))
        if not sep or feature not in DEPRECATED_FEATURES or disposition not in DISPOSITIONS:
            raise InvalidLabel(f"deprecated: {part!r} is not a feature:disposition pair")
        if feature in pairs:
            raise InvalidLabel(f"deprecated: {feature} appears twice")
        pairs[feature] = disposition
    return ";".join(f"{f}:{pairs[f]}" for f in DEPRECATED_FEATURES if f in pairs)


def validate(labels: Mapping[str, str]) -> dict[str, str]:
    """Return the labels cleaned and checked, or raise ``InvalidLabel``."""
    unknown = set(labels) - set(CODEBOOK_COLUMNS)
    if unknown:
        raise InvalidLabel(f"not a coded column: {', '.join(sorted(unknown))}")
    clean = {c: str(labels.get(c, "")).strip() for c in CODEBOOK_COLUMNS}
    for column, allowed in ALLOWED.items():
        clean[column] = clean[column].lower()
        if clean[column] and clean[column] not in allowed:
            raise InvalidLabel(f"{column}: {clean[column]!r} is not one of {', '.join(allowed)}")
    clean["deprecated"] = normalize_deprecated(clean["deprecated"])
    if clean["detector_correct"] != "yes":
        filled = [c for c in (*REQUIRED_IF_CORRECT, "deprecated") if clean[c]]
        if filled:
            raise InvalidLabel(f"{', '.join(filled)} must be empty unless detector_correct is yes")
    return clean


def status(row: Mapping[str, str]) -> str:
    """How far a row has been coded.

    A blank is legitimate only with a reason in ``notes``, and a ``no`` always
    needs one, since it is the record of why the detector was wrong.
    """
    verdict = str(row.get("detector_correct", "")).strip().lower()
    has_notes = bool(str(row.get("notes", "")).strip())
    if not verdict:
        return TODO
    if verdict == "no":
        return DONE if has_notes else INCOMPLETE
    blanks = [c for c in REQUIRED_IF_CORRECT if not str(row.get(c, "")).strip()]
    return DONE if not blanks or has_notes else INCOMPLETE


class Sheet:
    """One coder's CSV. Thread-safe for the handful of requests one coder makes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        fields, rows = self._read()
        missing = [c for c in ("episode_id", *CODEBOOK_COLUMNS) if c not in fields]
        if missing:
            raise ValueError(f"{path} lacks columns: {', '.join(missing)}")
        if len({r["episode_id"] for r in rows}) != len(rows):
            raise ValueError(f"{path} lists an episode_id more than once")

    def _read(self) -> tuple[list[str], list[dict[str, str]]]:
        with self.path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = [{k: v or "" for k, v in row.items() if k is not None} for row in reader]
            return list(reader.fieldnames or []), rows

    def rows(self) -> list[dict[str, str]]:
        with self._lock:
            return self._read()[1]

    def update(self, episode_id: str, labels: Mapping[str, str]) -> dict[str, str]:
        """Write one row's coded columns and return the row as saved."""
        clean = validate(labels)
        with self._lock:
            fields, rows = self._read()
            row = next((r for r in rows if r["episode_id"] == episode_id), None)
            if row is None:
                raise KeyError(episode_id)
            row.update(clean)
            tmp = self.path.with_name(self.path.name + ".partial")
            try:
                with tmp.open("w", encoding="utf-8", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                os.replace(tmp, self.path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            return row
