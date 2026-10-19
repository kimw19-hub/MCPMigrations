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


def _coded(eid: str, verdict: str, **labels: str) -> dict[str, str]:
    return {"episode_id": eid, "detector_correct": verdict, **labels}


def test_dependent_columns_count_a_one_sided_blank_as_disagreement() -> None:
    """A blank author is an answer; dropping it hid every author disagreement."""
    from mcp_migration.report.labels import agreement_details

    a = [
        _coded("x", "yes", author_kind_verified=""),
        _coded("y", "yes", author_kind_verified="bot"),
    ]
    b = [
        _coded("x", "yes", author_kind_verified="human"),
        _coded("y", "yes", author_kind_verified="bot"),
    ]
    d = agreement_details(a, b, ["author_kind_verified"])["author_kind_verified"]
    assert d["n"] == 2
    assert d["agreement"] == 0.5
    assert d["kappa"] < 1.0


def test_dependent_columns_are_compared_only_where_both_coders_said_yes() -> None:
    from mcp_migration.report.labels import agreement_details

    a = [
        _coded("x", "yes", trigger="proactive"),
        _coded("y", "no"),
        _coded("z", "yes", trigger="unknown"),
    ]
    b = [_coded("x", "yes", trigger="proactive"), _coded("y", "no"), _coded("z", "no")]
    d = agreement_details(a, b, ["detector_correct", "trigger"])
    assert d["detector_correct"]["n"] == 3
    assert d["trigger"]["n"] == 1


def test_consensus_does_not_depend_on_sheet_order() -> None:
    from mcp_migration.report.labels import consensus

    a = [_coded("x", "yes"), _coded("y", "yes"), _coded("z", "no")]
    b = [_coded("x", "yes"), _coded("y", "no"), _coded("z", "no")]
    ab, split_ab = consensus(a, b)
    ba, split_ba = consensus(b, a)
    verdicts = lambda rows: {r["episode_id"]: r["detector_correct"] for r in rows}  # noqa: E731
    assert verdicts(ab) == verdicts(ba) == {"x": "yes", "y": "no", "z": "no"}
    assert split_ab == split_ba == ["y"]


def test_invalid_rows_catch_typos_and_labels_on_rejected_episodes() -> None:
    from mcp_migration.report.labels import invalid_rows

    rows = [
        _coded("ok", "yes", end_state="dual_era", trigger="proactive", author_kind_verified=""),
        _coded("typo", "yes", end_state="dual_eraa", trigger="proactive", author_kind_verified=""),
        _coded("stray", "no", end_state="", trigger="", author_kind_verified="bot"),
    ]
    problems = invalid_rows(rows)
    assert [p.split(":")[0] for p in problems] == ["typo", "stray"]


def _write_sheet(path: Path, rows: list[dict[str, str]]) -> Path:
    from mcp_migration.report.sampling import CODEBOOK_COLUMNS

    fields = ["episode_id", *[c for c in CODEBOOK_COLUMNS if c != "notes"]]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})
    return path


def test_labels_command_refuses_an_invalid_sheet(tmp_path: Path) -> None:
    from mcp_migration.cli import main

    a = _write_sheet(tmp_path / "a.csv", [_coded("x#0", "no", author_kind_verified="bot")])
    b = _write_sheet(tmp_path / "b.csv", [_coded("x#0", "no")])
    out = tmp_path / "report.json"
    assert main(["--data-dir", str(tmp_path), "labels", str(a), str(b), "--out", str(out)]) == 1
    assert not out.exists()


def test_labels_command_precision_ignores_argument_order(tmp_path: Path) -> None:
    from mcp_migration.cli import main

    yes = dict(end_state="still_v1", trigger="proactive")
    a = _write_sheet(tmp_path / "a.csv", [_coded("x#0", "yes", **yes), _coded("y#0", "yes", **yes)])
    b = _write_sheet(tmp_path / "b.csv", [_coded("x#0", "yes", **yes), _coded("y#0", "no")])
    reports = []
    for order in ((a, b), (b, a)):
        out = tmp_path / "report.json"
        assert (
            main(["--data-dir", str(tmp_path), "labels", *map(str, order), "--out", str(out)]) == 0
        )
        reports.append(json.loads(out.read_text()))
    assert reports[0]["precision_overall"] == reports[1]["precision_overall"] == 0.5
    assert reports[0]["split_episodes"] == ["y#0"]
    assert reports[0]["agreement"]["detector_correct"]["n"] == 2


def test_labels_command_takes_precision_from_the_adjudicated_sheet(tmp_path: Path) -> None:
    from mcp_migration.cli import main

    yes = dict(end_state="still_v1", trigger="proactive")
    a = _write_sheet(tmp_path / "a.csv", [_coded("x#0", "yes", **yes)])
    b = _write_sheet(tmp_path / "b.csv", [_coded("x#0", "no")])
    adj = _write_sheet(tmp_path / "adj.csv", [_coded("x#0", "yes", **yes)])
    out = tmp_path / "report.json"
    args = ["--data-dir", str(tmp_path), "labels", str(a), str(b), "--adjudicated", str(adj)]
    assert main([*args, "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["precision_overall"] == 1.0
    assert report["precision_source"].startswith("adjudicated")


def test_sample_never_redraws_an_episode_an_earlier_sheet_labeled(tmp_path: Path) -> None:
    """Episodes that shaped the rules cannot also measure them."""
    from mcp_migration.cli import main

    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    base = {
        "repo": "a/b",
        "kind": "revision_change",
        "scope": "protocol_only",
        "start": "2026-08-01T00:00:00+00:00",
        "end": "2026-08-01T00:00:00+00:00",
        "duration_days": 0,
        "shas": ["abc"],
        "lines_added": 1,
        "lines_deleted": 0,
        "author_kinds": ["human_or_unknown"],
        "deprecated_removed": [],
        "revisions_added": [],
        "revisions_removed": [],
        "replacements": [],
        "head_static_era": "sdk_v1",
    }
    with (data / "processed" / "episodes.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(4):
            fh.write(json.dumps({**base, "episode_id": f"a/b#{i}"}) + "\n")
    earlier = _write_sheet(tmp_path / "round1.csv", [_coded("a/b#0", "no"), _coded("a/b#1", "no")])
    out = tmp_path / "round2.csv"
    args = ["--data-dir", str(data), "sample", "--per-stratum", "10"]
    assert main([*args, "--exclude", str(earlier), "--out", str(out)]) == 0
    assert sorted(r["episode_id"] for r in read_sheet(out)) == ["a/b#2", "a/b#3"]


def test_prelabel_uptake_counts_changed_cells_and_skips_blind_episodes() -> None:
    from mcp_migration.report.labels import prelabel_uptake

    pre = [_coded("x", "yes", trigger="proactive"), _coded("y", "yes", trigger="unknown")]
    sheet = [_coded("x", "yes", trigger="user_issue"), _coded("y", "no")]
    u = prelabel_uptake(sheet, pre, ["detector_correct", "trigger"])
    assert u["cells"] == 4
    assert u["cells_changed"] == 3  # x.trigger, y.detector, y.trigger
    assert u["episodes_changed"] == 2
    blind = prelabel_uptake(sheet, pre, ["detector_correct", "trigger"], skip=["y"])
    assert blind["cells"] == 2 and blind["cells_changed"] == 1


def test_labels_command_reports_uptake_and_blind_agreement(tmp_path: Path) -> None:
    from mcp_migration.cli import main

    yes = dict(end_state="still_v1", trigger="proactive")
    pre = _write_sheet(
        tmp_path / "llm.csv", [_coded("x#0", "yes", **yes), _coded("y#0", "yes", **yes)]
    )
    a = _write_sheet(tmp_path / "a.csv", [_coded("x#0", "yes", **yes), _coded("y#0", "no")])
    b = _write_sheet(tmp_path / "b.csv", [_coded("x#0", "yes", **yes), _coded("y#0", "no")])
    blind = tmp_path / "blind.txt"
    blind.write_text("y#0\n", encoding="utf-8")
    out = tmp_path / "report.json"
    args = ["--data-dir", str(tmp_path), "labels", str(a), str(b), "--prelabels", str(pre)]
    assert main([*args, "--blind", str(blind), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    uptake = report["assisted"]["uptake"][str(a)]
    assert uptake["episodes"] == 1 and uptake["accepted"] == 1.0  # the blind one is skipped
    assert report["assisted"]["llm_vs_reference_rows"] == "blind"
    assert report["blind"]["n"] == 1


def test_the_coded_profile_counts_only_genuine_episodes_of_the_kind() -> None:
    from mcp_migration.report.labels import coded_profile

    rows = [
        {
            "kind": "pin_below_2",
            "detector_correct": "yes",
            "end_state": "pinned_below_2",
            "trigger": "downstream_change",
        },
        {"kind": "pin_below_2", "detector_correct": "no", "end_state": "", "trigger": ""},
        {
            "kind": "migration",
            "detector_correct": "yes",
            "end_state": "sdk_v2_default",
            "trigger": "proactive",
        },
    ]
    profile = coded_profile(rows, "pin_below_2")
    assert profile == {
        "n_sampled": 2,
        "n": 1,
        "end_state": {"pinned_below_2": 1},
        "trigger": {"downstream_change": 1},
    }


def test_the_interval_never_claims_more_than_the_episodes_judged() -> None:
    """One "no" in a tiny stratum must not shrink the interval to nothing."""
    from mcp_migration.report.labels import weighted_precision

    episodes = {f"big#{i}": {"kind": "m", "head_static_era": "a"} for i in range(10_000)}
    episodes |= {f"small#{i}": {"kind": "m", "head_static_era": "b"} for i in range(10)}
    rows = [{"episode_id": f"big#{i}", "detector_correct": "yes"} for i in range(20)]
    rows += [{"episode_id": "small#0", "detector_correct": "no"}]
    rows += [{"episode_id": f"small#{i}", "detector_correct": "yes"} for i in range(1, 5)]
    result = weighted_precision(rows, episodes)
    assert result["n_effective"] <= result["n"] == 25
    assert result["ci_high"] - result["ci_low"] > 0.05
