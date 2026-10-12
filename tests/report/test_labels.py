from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from mcp_migration.report.labels import (
    agreement,
    cohens_kappa,
    precision,
    precision_by_signal,
    read_sheet,
)


def test_kappa_is_one_for_perfect_agreement_with_two_categories() -> None:
    assert cohens_kappa(["yes", "no", "yes"], ["yes", "no", "yes"]) == pytest.approx(1.0)


def test_kappa_is_one_when_both_coders_use_a_single_category() -> None:
    """Chance agreement is also 1.0 here, which would otherwise divide by zero."""
    assert cohens_kappa(["yes"] * 5, ["yes"] * 5) == 1.0


def test_kappa_is_zero_for_chance_level_agreement() -> None:
    a = ["yes", "yes", "no", "no"]
    b = ["yes", "no", "yes", "no"]
    assert cohens_kappa(a, b) == pytest.approx(0.0)


def test_kappa_is_negative_for_systematic_disagreement() -> None:
    assert cohens_kappa(["yes", "no"], ["no", "yes"]) < 0


def test_kappa_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        cohens_kappa(["yes"], ["yes", "no"])


def test_precision_ignores_unlabeled_rows() -> None:
    rows = [
        {"detector_correct": "yes"},
        {"detector_correct": "no"},
        {"detector_correct": ""},  # not yet coded
        {"detector_correct": "YES"},  # case-insensitive
    ]
    assert precision(rows) == pytest.approx(2 / 3)


def test_precision_of_an_uncoded_sheet_is_zero_not_an_error() -> None:
    assert precision([{"detector_correct": ""}]) == 0.0


def test_precision_by_kind_uses_the_episode_record() -> None:
    rows = [
        {"episode_id": "a/b#0", "detector_correct": "yes"},
        {"episode_id": "a/b#1", "detector_correct": "no"},
        {"episode_id": "c/d#0", "detector_correct": "yes"},
    ]
    episodes = {
        "a/b#0": {"kind": "migration"},
        "a/b#1": {"kind": "migration"},
        "c/d#0": {"kind": "pin_below_2"},
    }
    result = precision_by_signal(rows, episodes)
    assert result["migration"]["n"] == 2
    assert result["migration"]["precision"] == 0.5
    assert result["pin_below_2"]["n"] == 1
    assert result["pin_below_2"]["precision"] == 1.0
    # A kind measured on one episode must not read as precisely as one measured
    # on hundreds, so every entry carries an interval.
    assert result["pin_below_2"]["ci_low"] < 1.0


def test_agreement_matches_rows_by_episode_id_not_order() -> None:
    a = [
        {"episode_id": "x#0", "detector_correct": "yes", "trigger": "proactive"},
        {"episode_id": "y#0", "detector_correct": "no", "trigger": "user_issue"},
    ]
    b = [  # deliberately reversed
        {"episode_id": "y#0", "detector_correct": "no", "trigger": "user_issue"},
        {"episode_id": "x#0", "detector_correct": "yes", "trigger": "proactive"},
    ]
    result = agreement(a, b, ["detector_correct", "trigger"])
    assert result["detector_correct"] == pytest.approx(1.0)
    assert result["trigger"] == pytest.approx(1.0)


def test_agreement_raises_when_sheets_share_no_episodes() -> None:
    with pytest.raises(ValueError, match="share no episode_id"):
        agreement([{"episode_id": "x#0"}], [{"episode_id": "z#0"}], ["detector_correct"])


def test_read_sheet_tolerates_a_bom(tmp_path: Path) -> None:
    """Coders often round-trip the sheet through a spreadsheet, which adds a BOM."""
    path = tmp_path / "sample.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["episode_id", "detector_correct"])
        writer.writerow(["a/b#0", "yes"])
    rows = read_sheet(path)
    assert rows[0]["episode_id"] == "a/b#0"
    assert precision(rows) == 1.0


def test_the_codebook_documents_every_column_a_coder_sees(codebook: str) -> None:
    """A column in the sheet with no codebook entry is a column nobody can fill."""
    from mcp_migration.report.sampling import CODEBOOK_COLUMNS, PREFILLED_COLUMNS

    doc = codebook
    missing = [c for c in (*CODEBOOK_COLUMNS, *PREFILLED_COLUMNS) if f"`{c}`" not in doc]
    assert not missing, f"undocumented columns: {missing}"


def test_every_coded_column_has_its_own_row(codebook: str) -> None:
    """Sharing one row hid four features behind "one column per deprecated
    feature", which is a mention rather than instructions. Prefilled columns may
    still be grouped; the coder is not filling those in."""
    import re

    from mcp_migration.report.sampling import CODEBOOK_COLUMNS

    rows = [line for line in codebook.splitlines() if line.startswith("| `")]
    grouped = [
        c
        for c in CODEBOOK_COLUMNS
        if not any(re.match(rf"\|\s*`{re.escape(c)}`\s*\|", line) for line in rows)
    ]
    assert not grouped, f"columns without their own codebook row: {grouped}"


def test_every_static_era_has_an_end_state_a_coder_can_choose() -> None:
    """Without this, episodes in an unmapped era have no valid end_state."""
    from mcp_migration.mine.classify import StaticEra
    from mcp_migration.report.sampling import END_STATE_BY_ERA

    assert set(END_STATE_BY_ERA) == set(StaticEra)


def test_the_codebook_lists_every_allowed_end_state(codebook: str) -> None:
    from mcp_migration.report.sampling import END_STATE_BY_ERA, EXTRA_END_STATES

    doc = codebook
    for value in (*END_STATE_BY_ERA.values(), *EXTRA_END_STATES):
        assert f"`{value}`" in doc, f"end_state value missing from the codebook: {value}"


def test_the_codebook_lists_every_value_the_labeling_app_offers(codebook: str) -> None:
    """The app only accepts these values, so a coder reading the codebook must see them."""
    from mcp_migration.report.sampling import (
        AUTHOR_KINDS_VERIFIED,
        DEPRECATED_FEATURES,
        DETECTOR_VERDICTS,
        DISPOSITIONS,
        TRIGGERS,
    )

    values = (*DETECTOR_VERDICTS, *TRIGGERS, *AUTHOR_KINDS_VERIFIED)
    values += (*DEPRECATED_FEATURES, *DISPOSITIONS)
    missing = [v for v in values if f"`{v}`" not in codebook]
    assert not missing, f"values missing from the codebook: {missing}"


@pytest.fixture(scope="session")
def codebook(repo_root: Path) -> str:
    path = repo_root / "docs" / "codebook.md"
    if not path.exists():  # pragma: no cover - docs are optional for the package
        pytest.skip("docs/codebook.md not present")
    return path.read_text(encoding="utf-8")


def test_reordered_multivalue_cells_are_not_a_disagreement() -> None:
    """`deprecated` holds a set; comparing it literally would measure typing order."""
    from mcp_migration.report.labels import agreement

    a = [{"episode_id": "x", "deprecated": "sampling:removed;roots:kept"}]
    b = [{"episode_id": "x", "deprecated": "roots:kept;sampling:removed"}]
    assert agreement(a, b, ["deprecated"])["deprecated"] == 1.0


def test_case_and_spacing_do_not_count_as_disagreement() -> None:
    from mcp_migration.report.labels import agreement

    a = [{"episode_id": "x", "end_state": "still_v1"}]
    b = [{"episode_id": "x", "end_state": " Still_V1 "}]
    assert agreement(a, b, ["end_state"])["end_state"] == 1.0


def test_genuinely_different_answers_still_disagree() -> None:
    """Normalising must not paper over a real split."""
    from mcp_migration.report.labels import agreement

    a = [{"episode_id": "x", "deprecated": "sampling:removed"}]
    b = [{"episode_id": "x", "deprecated": "sampling:replaced"}]
    assert agreement(a, b, ["deprecated"])["deprecated"] < 1.0


def test_show_prints_an_episode_and_reports_a_missing_one(tmp_path: Path, capsys: Any) -> None:
    """`show` is the coder's entry point for every row; a silent failure there
    would send them to the wrong episode."""
    from mcp_migration.cli import main

    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    (data / "processed" / "episodes.jsonl").write_text(
        json.dumps(
            {
                "episode_id": "a/b#0",
                "repo": "a/b",
                "kind": "migration",
                "scope": "sdk_and_protocol",
                "start": "2026-08-01T00:00:00+00:00",
                "end": "2026-08-02T00:00:00+00:00",
                "shas": ["abc123", "def456"],
                "lines_added": 10,
                "lines_deleted": 2,
                "revisions_added": ["2026-07-28"],
                "revisions_removed": [],
                "deprecated_removed": [],
                "replacements": [],
                "head_static_era": "sdk_v2_default",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert main(["--data-dir", str(data), "show", "a/b#0"]) == 0
    out = capsys.readouterr().out
    assert "a/b#0" in out
    # multi-commit episodes must link the range, not just the first commit
    assert "compare/abc123^...def456" in out
    assert main(["--data-dir", str(data), "show", "a/b#99"]) == 1


def test_wilson_interval_stays_inside_zero_and_one() -> None:
    """The normal approximation runs past 1 when every judgement agrees, which
    is exactly the case per-kind precision is expected to hit."""
    from mcp_migration.report.labels import wilson_interval

    low, high = wilson_interval(20, 20)
    assert 0.0 < low < 1.0
    assert high == 1.0


def test_wilson_interval_narrows_as_the_sample_grows() -> None:
    from mcp_migration.report.labels import wilson_interval

    small = wilson_interval(45, 50)
    large = wilson_interval(450, 500)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_interval_of_nothing_is_not_an_error() -> None:
    from mcp_migration.report.labels import wilson_interval

    assert wilson_interval(0, 0) == (0.0, 0.0)


def _population(sizes: dict[tuple[str, str], int]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for (kind, era), size in sizes.items():
        for i in range(size):
            out[f"{kind}/{era}#{i}"] = {"kind": kind, "head_static_era": era}
    return out


def _judged(kind: str, era: str, verdicts: list[str]) -> list[dict[str, str]]:
    return [
        {"episode_id": f"{kind}/{era}#{i}", "detector_correct": v} for i, v in enumerate(verdicts)
    ]


def test_precision_is_weighted_by_stratum_size_not_by_rows_sampled() -> None:
    """Equal allocation over strata of very different size: the big one decides."""
    from mcp_migration.report.labels import weighted_precision

    episodes = _population({("v1_removal", "sdk_v1"): 900, ("migration", "sdk_v1"): 100})
    rows = _judged("v1_removal", "sdk_v1", ["yes"] * 9 + ["no"]) + _judged(
        "migration", "sdk_v1", ["no"] * 10
    )
    p = weighted_precision(rows, episodes)
    assert p["precision_unweighted"] == 0.45
    assert round(float(p["precision"]), 3) == 0.81  # 0.9 * 0.9 + 0.1 * 0.0
    assert p["ci_low"] < p["precision"] < p["ci_high"]
    assert p["coverage"] == 100.0


def test_unanimous_judgements_fall_back_to_the_plain_count() -> None:
    from mcp_migration.report.labels import weighted_precision

    episodes = _population({("migration", "a"): 50, ("migration", "b"): 500})
    rows = _judged("migration", "a", ["yes"] * 5) + _judged("migration", "b", ["yes"] * 5)
    p = weighted_precision(rows, episodes)
    assert p["precision"] == 1.0
    assert p["n_effective"] == 10.0
    assert p["ci_high"] == 1.0 and p["ci_low"] < 1.0


def test_unjudged_strata_lower_coverage_rather_than_counting_as_perfect() -> None:
    from mcp_migration.report.labels import weighted_precision

    episodes = _population({("migration", "a"): 300, ("migration", "b"): 100})
    p = weighted_precision(_judged("migration", "b", ["yes", "no"]), episodes)
    assert p["precision"] == 0.5
    assert p["coverage"] == 25.0


def test_per_kind_precision_weights_eras_within_the_kind() -> None:
    from mcp_migration.report.labels import precision_by_signal

    episodes = _population({("migration", "big"): 800, ("migration", "small"): 200})
    rows = _judged("migration", "big", ["yes"] * 4) + _judged("migration", "small", ["no"] * 4)
    by_kind = precision_by_signal(rows, episodes)
    assert round(float(by_kind["migration"]["precision"]), 3) == 0.8
