from __future__ import annotations

from pathlib import Path

import pytest

from mcp_migration.labeling.sheet import (
    DONE,
    INCOMPLETE,
    TODO,
    InvalidLabel,
    Sheet,
    normalize_deprecated,
    status,
    validate,
)
from mcp_migration.report.labels import read_sheet
from mcp_migration.report.sampling import CODEBOOK_COLUMNS, PREFILLED_COLUMNS

BLANK = dict.fromkeys(CODEBOOK_COLUMNS, "")


def write_sheet(path: Path, ids: list[str]) -> Path:
    header = ",".join((*PREFILLED_COLUMNS, *CODEBOOK_COLUMNS))
    lines = [header]
    for eid in ids:
        repo = eid.split("#")[0]
        lines.append(f"{eid},{repo},migration,s,e,1,sdk_v1,url" + "," * len(CODEBOOK_COLUMNS))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_validate_accepts_a_complete_yes_and_lowercases_it() -> None:
    clean = validate(
        {
            **BLANK,
            "detector_correct": "Yes",
            "end_state": "dual_era",
            "trigger": "proactive",
            "author_kind_verified": "human",
        }
    )
    assert clean["detector_correct"] == "yes"


def test_validate_rejects_values_the_codebook_does_not_allow() -> None:
    with pytest.raises(InvalidLabel, match="end_state"):
        validate({**BLANK, "detector_correct": "yes", "end_state": "modern"})


def test_validate_rejects_unknown_columns() -> None:
    with pytest.raises(InvalidLabel, match="kind"):
        validate({**BLANK, "kind": "migration"})


def test_a_no_must_leave_the_other_judgements_empty() -> None:
    with pytest.raises(InvalidLabel, match="trigger"):
        validate({**BLANK, "detector_correct": "no", "trigger": "unknown"})


def test_deprecated_pairs_are_put_in_a_canonical_order() -> None:
    assert normalize_deprecated(" roots:kept ; sampling:Replaced ") == (
        "sampling:replaced;roots:kept"
    )


@pytest.mark.parametrize(
    "value", ["sampling", "sampling:gone", "tools:removed", "roots:kept;roots:removed"]
)
def test_malformed_deprecated_cells_are_rejected(value: str) -> None:
    with pytest.raises(InvalidLabel):
        normalize_deprecated(value)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({}, TODO),
        ({"detector_correct": "no"}, INCOMPLETE),
        ({"detector_correct": "no", "notes": "langchain's create_message"}, DONE),
        ({"detector_correct": "yes", "end_state": "dual_era"}, INCOMPLETE),
        ({"detector_correct": "yes", "end_state": "dual_era", "notes": "no PR found"}, DONE),
        (
            {
                "detector_correct": "yes",
                "end_state": "dual_era",
                "trigger": "unknown",
                "author_kind_verified": "bot",
            },
            DONE,
        ),
    ],
)
def test_status_follows_the_codebook(row: dict[str, str], expected: str) -> None:
    assert status(row) == expected


def test_update_writes_one_row_and_keeps_the_rest(tmp_path: Path) -> None:
    path = write_sheet(tmp_path / "sample.csv", ["a/b#0", "c/d#1"])
    sheet = Sheet(path)
    sheet.update("c/d#1", {**BLANK, "detector_correct": "no", "notes": 'a, with "quotes"'})

    rows = read_sheet(path)
    assert [r["episode_id"] for r in rows] == ["a/b#0", "c/d#1"]
    assert rows[0]["detector_correct"] == ""
    assert rows[1]["notes"] == 'a, with "quotes"'
    assert not path.with_name("sample.csv.partial").exists()


def test_update_keeps_edits_made_outside_the_app(tmp_path: Path) -> None:
    path = write_sheet(tmp_path / "sample.csv", ["a/b#0", "c/d#1"])
    sheet = Sheet(path)
    text = path.read_text(encoding="utf-8").replace("c/d#1,c/d,", "c/d#1,c/d-renamed,")
    path.write_text(text, encoding="utf-8")

    sheet.update("a/b#0", {**BLANK, "detector_correct": "no", "notes": "x"})
    assert read_sheet(path)[1]["repo"] == "c/d-renamed"


def test_update_of_an_unknown_episode_raises(tmp_path: Path) -> None:
    sheet = Sheet(write_sheet(tmp_path / "sample.csv", ["a/b#0"]))
    with pytest.raises(KeyError):
        sheet.update("x/y#0", BLANK)


def test_a_sheet_without_the_coded_columns_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    path.write_text("episode_id,repo\na/b#0,a/b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="detector_correct"):
        Sheet(path)
