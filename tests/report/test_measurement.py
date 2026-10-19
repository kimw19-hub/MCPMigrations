"""The revised measurements: time to event, and protocol support by dimension."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from mcp_migration.report.measurement import (
    deferral,
    kaplan_meier,
    protocol_dimensions,
    rewind,
)

CENSOR = date(2026, 9, 15)


def test_kaplan_meier_keeps_the_censored_in_the_denominator() -> None:
    start = date(2026, 8, 1)
    rows = [(start, date(2026, 8, 11)), (start, None), (start, None), (start, None)]
    km = kaplan_meier(rows, CENSOR)
    assert km["n"] == 4 and km["events"] == 1
    assert km["by_day"][30]["pct"] == 25.0
    assert km["by_day"][30]["ci_low"] <= 25.0 <= km["by_day"][30]["ci_high"]


def test_an_event_after_the_censoring_date_is_censored() -> None:
    km = kaplan_meier([(date(2026, 8, 1), date(2026, 10, 1))], CENSOR)
    assert km["events"] == 0 and km["by_day"][30]["pct"] == 0.0


def test_no_events_gives_an_honest_upper_bound_not_a_point() -> None:
    """Zero of 33 is not certainty: the exact upper bound is about 10%."""
    km = kaplan_meier([(date(2026, 8, 1), None)] * 33, CENSOR)
    at = km["by_day"][30]
    assert at["pct"] == 0.0 and at["ci_low"] == 0.0
    assert 10.0 < at["ci_high"] < 11.0


def test_a_checkpoint_beyond_all_follow_up_is_not_extrapolated() -> None:
    km = kaplan_meier([(date(2026, 9, 1), None)], CENSOR)
    assert 30 not in km["by_day"]


def _episode(repo: str, kind: str, day: str, after: str = "sdk_v1") -> dict[str, object]:
    return {"repo": repo, "kind": kind, "start": day, "end": day, "era_after": after}


def test_deferral_compares_pinned_with_unpinned_v1_and_records_sequences() -> None:
    episodes = [
        _episode("a/pinner", "v1_removal", "2026-03-01"),
        _episode("a/pinner", "pin_below_2", "2026-08-02", "sdk_v1_pinned_below_2"),
        _episode("a/mover", "v1_removal", "2026-03-01"),
        _episode("a/mover", "migration", "2026-08-10", "sdk_v2_default"),
        _episode("a/both", "pin_below_2", "2026-08-01", "sdk_v1_pinned_below_2"),
        _episode("a/both", "migration", "2026-09-01", "sdk_v2_default"),
    ]
    out = deferral(episodes, CENSOR)
    assert out["pinned"]["n"] == 2 and out["pinned"]["events"] == 1
    assert out["unpinned_v1"]["n"] == 1 and out["unpinned_v1"]["events"] == 1
    assert out["sequences"] == {"pin": 1, "move": 1, "pin_move": 1}


def test_python_serves_both_eras_typescript_only_with_an_entry_point() -> None:
    heads = {
        "a/py": {"signals": {"py_v2_mcpserver_import": 1}},
        "a/ts-plain": {"signals": {"ts_v2_split_pkg": 2}},
        "a/ts-modern": {"signals": {"ts_v2_split_pkg": 1}},
        "a/ts-strict": {"signals": {"ts_v2_split_pkg": 1}},
        "a/ts-unmined": {"signals": {"ts_v2_split_pkg": 1}},
        "a/v1": {"signals": {"py_v1_fastmcp_import": 1}},
    }
    ts_heads = {
        "a/ts-plain": {"signals": {}},
        "a/ts-modern": {"signals": {"ts_v2_modern_entry": 1}},
        "a/ts-strict": {"signals": {"ts_v2_modern_entry": 1, "ts_legacy_reject": 1}},
    }
    out = protocol_dimensions(heads, ts_heads)
    assert out["python"] == {
        "n": 1,
        "resolved": 1,
        "pct_modern": 100.0,
        "pct_legacy": 100.0,
        "pct_new_features": 0.0,
    }
    ts = out["typescript"]
    assert ts["n"] == 4 and ts["resolved"] == 3
    assert round(ts["pct_modern"], 1) == 66.7  # two of three resolved opted in
    assert round(ts["pct_legacy"], 1) == 66.7  # one of them turned legacy off


def test_rewind_undoes_changes_after_the_day() -> None:
    commits = [
        {"date": "2026-10-01T09:00:00+00:00", "added": {"ts_v2_modern_entry": 1}, "removed": {}}
    ]
    assert rewind({"ts_v2_modern_entry": 1}, commits, date(2026, 9, 20)) == {}
    assert rewind({"ts_v2_modern_entry": 1}, commits, date(2026, 10, 2)) == {
        "ts_v2_modern_entry": 1
    }


def test_ceiling_pairs_read_the_cohorts_pairs_by_keyword() -> None:
    from mcp_migration.report.measurement import ceiling_pairs

    pairs = [
        {
            "gap": 35,
            "pin_message": "fix: cap mcp<2 (2.0 removed FastMCP)",
            "move_message": "migrate to mcp 2.x and lift the <2 cap",
        },
        {
            "gap": 0,
            "pin_message": "release with [mcp] extra",
            "move_message": "support mcp 2.x with 1.x fallback",
        },
    ]
    assert ceiling_pairs(pairs) == {
        "pairs": 2,
        "same_day": 1,
        "three_weeks": 1,
        "pin_repair": 1,
        "move_lift": 1,
        "move_dual": 1,
    }


def test_a_typescript_client_is_not_counted_as_a_server() -> None:
    heads = {
        "a/server": {"signals": {"ts_v2_split_pkg": 1}},
        "a/client": {"signals": {"ts_v2_split_pkg": 1}},
    }
    ts_heads = {"a/server": {"signals": {}}, "a/client": {"signals": {}}}
    out = protocol_dimensions(heads, ts_heads, ts_servers={"a/server"})
    assert out["typescript"]["n"] == 1
    assert out["typescript_client_only"] == {"n": 1}


def test_upgrade_claims_read_a_standalone_version_only() -> None:
    from mcp_migration.report.measurement import _CLAIM

    claims = [
        "chore(mcp): upgrade TypeScript SDK to v2 (#7383)",
        "Migrate to MCP TypeScript SDK v2",
        "refactor: MCP 2.0 (2026-07-28)",
        "upgrade MCP to 2.x",
    ]
    others = [
        "chore(skills): sync vendored skills to mcp-ts-core 0.12.x",
        "feat: BigMCP Gateway v1.2.0",
        "feat(mcp): add MCP server to expose skills as tools",
    ]
    assert all(_CLAIM.search(c) for c in claims)
    assert not any(_CLAIM.search(o) for o in others)


def test_deferral_follows_quick_movers_and_judges_scope_at_the_pin_commit() -> None:
    """The reviewer's case A: pin on day 0, v2 on day 10, must be counted."""
    from mcp_migration.report.measurement import deferral_from_commits

    def c(
        day: str, sha: str, added: dict[str, int], removed: dict[str, int] | None = None
    ) -> dict[str, object]:
        return {
            "date": f"2026-{day}T10:00:00+00:00",
            "sha": sha,
            "added": added,
            "removed": removed or {},
            "is_merge": False,
            "message": sha,
        }

    v1 = {"py_v1_fastmcp_import": 1}
    commits = {
        # Quick mover: deferral at the pin, v2 ten days later.
        "a/quick": [
            c("01-05", "q0", v1),
            c("08-01", "q1", {"py_pin_mcp_below_2": 1}),
            c("08-11", "q2", {"py_v2_mcpserver_import": 1}, v1),
        ],
        # v2 in the same commit as the ceiling: a bound, not a deferral.
        "a/same": [
            c("01-05", "s0", v1),
            c("08-01", "s1", {"py_pin_mcp_below_2": 1, "py_pin_mcp_v2": 1}),
        ],
        # v2 already present when the ceiling is added: a bound.
        "a/already": [
            c("01-05", "a0", {"py_v2_mcpserver_import": 1}),
            c("08-01", "a1", {"py_pin_mcp_below_2": 1}),
        ],
        # A control on v1, active before the pin date, that pins later.
        "a/ctrl": [c("07-20", "c0", v1), c("08-20", "c1", {"py_pin_mcp_below_2": 1})],
    }
    heads = {
        "a/quick": {"signals": {"py_pin_mcp_below_2": 1, "py_v2_mcpserver_import": 1}},
        "a/same": {
            "signals": {"py_v1_fastmcp_import": 1, "py_pin_mcp_below_2": 1, "py_pin_mcp_v2": 1}
        },
        "a/already": {"signals": {"py_v2_mcpserver_import": 1, "py_pin_mcp_below_2": 1}},
        "a/ctrl": {"signals": {"py_v1_fastmcp_import": 1, "py_pin_mcp_below_2": 1}},
    }
    out = deferral_from_commits(commits, heads, CENSOR, controls_per_pin=1)
    # a/ctrl's own ceiling is a deferral too; it is still a control for the
    # earlier pin, because its later ceiling is future information.
    assert out["scopes"] == {"deferral": 2, "v2_same_commit": 1, "v2_already_present": 1}
    assert out["pinned"]["n"] == 2 and out["pinned"]["events"] == 1
    first_rise = next(day for day, pct, *_ in out["pinned"]["curve"] if pct > 0)
    assert first_rise == 10  # dated by the actual v2 commit
    ctrl = out["controls"]
    assert ctrl["n"] == 1 and ctrl["events"] == 0  # censored at its own ceiling
    # The pairs behind the qualitative reading are the cohort's own.
    assert [(p["repo"], p["gap"]) for p in out["pairs"]] == [("a/quick", 10)]
    matching = out["matching"]
    assert matching["control_entries"] == 1 and matching["distinct_controls"] == 1
    assert matching["controls_later_pinned"] == 1 and matching["short_sets"] == 1


def test_controls_can_be_followed_past_their_own_ceiling() -> None:
    """Sensitivity: compare with repositories unpinned on the matching date."""
    from mcp_migration.report.measurement import deferral_from_commits

    def c(day: str, added: dict[str, int], removed: dict[str, int] | None = None) -> dict:
        return {
            "date": f"2026-{day}T10:00:00+00:00",
            "sha": day,
            "added": added,
            "removed": removed or {},
            "is_merge": False,
            "message": day,
        }

    v1 = {"py_v1_fastmcp_import": 1}
    commits = {
        "a/pin": [c("01-05", v1), c("08-01", {"py_pin_mcp_below_2": 1})],
        # Pins on day 5, then moves on day 10.
        "a/ctrl": [
            c("07-20", v1),
            c("08-06", {"py_pin_mcp_below_2": 1}),
            c("08-11", {"py_v2_mcpserver_import": 1}, v1),
        ],
    }
    heads = {
        "a/pin": {"signals": {"py_v1_fastmcp_import": 1, "py_pin_mcp_below_2": 1}},
        "a/ctrl": {"signals": {"py_pin_mcp_below_2": 1, "py_v2_mcpserver_import": 1}},
    }
    censored = deferral_from_commits(commits, heads, CENSOR, controls_per_pin=1)
    followed = deferral_from_commits(
        commits, heads, CENSOR, controls_per_pin=1, censor_at_own_pin=False
    )
    assert censored["controls"]["events"] == 0
    assert followed["controls"]["events"] == 1


def test_matched_difference_resamples_whole_sets() -> None:
    from mcp_migration.report.measurement import _matched_difference

    start = date(2026, 8, 1)
    moved = (start, date(2026, 8, 6))
    stayed = (start, None)
    sets = [(moved, [(start, None, CENSOR)])] * 3 + [(stayed, [(start, None, CENSOR)])]
    out = _matched_difference(sets, CENSOR, 30, reps=200, seed=1)
    assert out["pct"] == 75.0
    assert out["ci_low"] <= 75.0 <= out["ci_high"] and out["reps"] == 200


def test_application_dimensions_read_routes_and_ignore_tests() -> None:
    from mcp_migration.report.measurement import application_dimensions

    def scan(app: dict[str, int], every: dict[str, int] | None = None) -> dict[str, object]:
        return {"app_signals": app, "signals": every or app}

    srv = {"ts_v2_server_pkg": 1}
    scans = {
        "a/legacy": scan({**srv, "ts_v1_transport_class": 1}),
        "a/mixed": scan({**srv, "ts_v2_modern_entry": 1, "ts_v1_transport_class": 1}),
        # Rejects on the strict handler but routes legacy traffic elsewhere.
        "a/routed": scan(
            {**srv, "ts_v2_modern_entry": 1, "ts_legacy_reject": 1, "ts_legacy_route": 1}
        ),
        "a/strict": scan({**srv, "ts_v2_modern_entry": 1, "ts_legacy_reject": 1}),
        # A feature marker only in a test does not count.
        "a/py": scan(
            {"py_v2_mcpserver_import": 1}, {"py_v2_mcpserver_import": 1, "py_v2_elicit": 2}
        ),
        "a/testonly": scan({}, {"ts_v2_server_pkg": 1}),
    }
    out = application_dimensions(scans)
    ts = out["typescript"]
    assert ts["n"] == 4 and ts["legacy_only"] == 1 and ts["mixed"] == 1
    assert ts["reject_any"] == 2 and ts["legacy_retired"] == 1  # only a/strict
    assert out["python"]["pct_features"] == 0.0
    assert out["v2_outside_application_only"] == 1


def test_the_legacy_only_audit_corrects_the_classifier() -> None:
    from mcp_migration.report.measurement import application_dimensions

    legacy = {"app_signals": {"ts_v2_server_pkg": 1, "ts_v1_transport_class": 1}, "signals": {}}
    scans = {"a/real": legacy, "a/v1sdk": legacy, "a/custom": legacy}
    audit = {
        "a/real": {"label": "legacy_only"},
        "a/v1sdk": {"label": "not_v2_server"},
        "a/custom": {"label": "modern_custom"},
    }
    raw = application_dimensions(scans)["typescript"]
    assert raw["n"] == 3 and raw["legacy_only"] == 3
    out = application_dimensions(scans, audit=audit)
    ts = out["typescript"]
    assert ts["n"] == 2 and ts["legacy_only"] == 1 and ts["custom_modern"] == 1
    assert out["typescript_not_v2_server"] == 1


def test_a_typescript_repository_without_any_entry_point_is_unresolved() -> None:
    from mcp_migration.report.measurement import application_dimensions

    scans = {
        "a/none": {"app_signals": {"ts_v2_server_pkg": 1}, "signals": {"ts_v2_server_pkg": 1}},
        "a/legacy": {
            "app_signals": {"ts_v2_server_pkg": 1, "ts_v1_transport_class": 1},
            "signals": {},
        },
    }
    out = application_dimensions(scans)
    assert out["typescript"]["n"] == 1 and out["typescript"]["legacy_only"] == 1
    assert out["typescript_no_entry"] == 1


def test_responses_count_moves_and_ceilings_from_the_release_state() -> None:
    from mcp_migration.report.measurement import responses

    def c(day: str, added: dict[str, int], removed: dict[str, int] | None = None) -> dict:
        return {
            "date": f"2026-{day}T10:00:00+00:00",
            "added": added,
            "removed": removed or {},
            "is_merge": False,
        }

    v1 = {"py_v1_fastmcp_import": 1}
    commits = {
        "a/moved": [c("01-05", v1), c("08-10", {"py_v2_mcpserver_import": 1}, v1)],
        "a/pinned": [c("01-05", v1), c("08-02", {"py_pin_mcp_below_2": 1})],
        "a/ts": [c("01-05", {"ts_v1_sdk_pkg": 1}), c("08-03", {"ts_v2_split_pkg": 1})],
        # Already on v2 before the release: not part of either response.
        "a/early": [c("07-01", {"py_v2_mcpserver_import": 1})],
    }
    heads = {
        "a/moved": {"signals": {"py_v2_mcpserver_import": 1}},
        "a/pinned": {"signals": {"py_v1_fastmcp_import": 1, "py_pin_mcp_below_2": 1}},
        "a/ts": {"signals": {"ts_v1_sdk_pkg": 1, "ts_v2_split_pkg": 1}},
        "a/early": {"signals": {"py_v2_mcpserver_import": 1}},
    }
    out = responses(commits, heads)
    assert out["python"] == {
        "on_v1": 2,
        "moved": 1,
        "deferred": 1,
        "ceiling_any": 1,
        "moved_only": 1,
        "deferred_only": 1,
        "both": 0,
        "neither": 0,
    }
    assert out["typescript"] == {"on_v1": 1, "moved": 1}
    assert sorted(out["cohort"]) == ["a/moved", "a/pinned"]


def test_issue_themes_count_only_relevant_issues(tmp_path: Path) -> None:
    import json

    from mcp_migration.report.measurement import issue_themes

    qual = tmp_path / "q.json"
    qual.write_text(
        json.dumps(
            {
                "evidence": {
                    "issues": [
                        {"repo": "a/x", "number": 1, "codes": ["names_rename", "import_error"]},
                        {"repo": "a/y", "number": 2, "codes": ["names_rename"]},
                    ]
                }
            }
        )
    )
    rel = tmp_path / "r.json"
    rel.write_text(json.dumps({"a/x#1": {"relevant": True}, "a/y#2": {"relevant": False}}))
    out = issue_themes(qual, rel)
    assert out["n"] == 1 and out["sampled"] == 2 and out["names_rename"] == 1


def test_clustered_bootstrap_keeps_a_repositorys_entries_together() -> None:
    from mcp_migration.report.measurement import _clustered

    start = date(2026, 8, 1)
    entries = [
        ("a/p1", "pinned", (start, date(2026, 8, 6))),
        ("a/p2", "pinned", (start, None)),
        # One control repository used twice: its two entries move together.
        ("a/c", "control", (start, None, CENSOR)),
        ("a/c", "control", (start, None, CENSOR)),
        ("a/d", "control", (start, date(2026, 8, 20), CENSOR)),
    ]
    out = _clustered(entries, CENSOR, 30, reps=200, seed=1)
    assert out["pinned"]["pct"] == 50.0
    assert round(out["controls"]["pct"], 1) == 33.3
    assert out["gap"]["ci_low"] <= out["gap"]["pct"] <= out["gap"]["ci_high"]
    assert out["repositories"]["pct"] == 4.0


def test_controls_need_not_be_reused() -> None:
    from mcp_migration.report.measurement import deferral_from_commits

    def c(day: str, added: dict[str, int]) -> dict:
        return {
            "date": f"2026-{day}T10:00:00+00:00",
            "sha": day,
            "added": added,
            "removed": {},
            "is_merge": False,
            "message": day,
        }

    v1 = {"py_v1_fastmcp_import": 1}
    pin = {"py_pin_mcp_below_2": 1}
    commits = {
        "a/p1": [c("07-20", v1), c("08-01", pin)],
        "a/p2": [c("07-21", v1), c("08-02", pin)],
        "a/ctrl": [c("07-22", v1)],
    }
    heads = {
        "a/p1": {"signals": {**v1, **pin}},
        "a/p2": {"signals": {**v1, **pin}},
        "a/ctrl": {"signals": v1},
    }
    reused = deferral_from_commits(commits, heads, CENSOR, controls_per_pin=1, seed=2)
    once = deferral_from_commits(
        commits, heads, CENSOR, controls_per_pin=1, seed=2, reuse_controls=False
    )
    assert once["matching"]["max_reuse"] <= 1
    assert reused["matching"]["control_entries"] >= once["matching"]["control_entries"]
