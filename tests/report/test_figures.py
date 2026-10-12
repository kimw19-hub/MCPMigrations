from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mcp_migration.mine.signals import PROTOCOL_REVISIONS
from mcp_migration.report.figures import (
    LINE_STYLES,
    MARKERS,
    PALETTE,
    _month,
    _month_range,
    _thin_labels,
    _whole_months,
)
from mcp_migration.report.stats import EPISODE_KINDS

EPISODES: list[dict[str, Any]] = [
    {
        "repo": "a/one",
        "kind": "migration",
        "start": "2026-07-30T10:00:00+00:00",
        "head_static_era": "sdk_v2_default",
        "revisions_added": ["2026-07-28"],
    },
    {
        "repo": "a/two",
        "kind": "v1_removal",
        "start": "2026-08-14T10:00:00+00:00",
        "head_static_era": "sdk_v1",
        "revisions_added": ["2026-07-28"],
    },
    {
        "repo": "a/one",
        "kind": "v2_adoption",
        "start": "2026-09-05T10:00:00+00:00",
        "head_static_era": "sdk_v2_default",
        "revisions_added": ["2026-07-28"],
    },
]


def test_month_bins_an_iso_timestamp() -> None:
    assert _month("2026-07-30T10:00:00+00:00") == "2026-07"


def test_month_range_is_inclusive_and_crosses_years() -> None:
    assert _month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_month_range_keeps_empty_months_visible() -> None:
    """Gaps must stay on the axis; collapsing them would distort the trend."""
    assert len(_month_range("2025-01", "2025-12")) == 12


def test_a_partial_trailing_month_is_dropped() -> None:
    """A mid-month snapshot otherwise ends every series on a fake decline."""
    months = ["2026-07", "2026-08", "2026-09"]
    assert _whole_months(months, "2026-09-16T10:00:00+00:00") == ["2026-07", "2026-08"]


def test_a_complete_trailing_month_is_kept() -> None:
    months = ["2026-07", "2026-08"]
    assert _whole_months(months, "2026-08-31T23:00:00+00:00") == ["2026-07", "2026-08"]


def test_february_is_not_treated_as_partial_on_its_last_day() -> None:
    assert _whole_months(["2025-02"], "2025-02-28T12:00:00+00:00") == ["2025-02"]
    assert _whole_months(["2024-02"], "2024-02-29T12:00:00+00:00") == ["2024-02"]
    # 2024 is a leap year, so the 28th is not the end of the month.
    assert _whole_months(["2024-02"], "2024-02-28T12:00:00+00:00") == []


def test_thin_labels_keeps_every_fourth() -> None:
    labels = _thin_labels([f"2025-{m:02d}" for m in range(1, 9)], keep=4)
    assert labels == ["2025-01", "", "", "", "2025-05", "", "", ""]


def test_every_series_has_a_colour_a_style_and_a_marker() -> None:
    """Greyscale printing must not collapse two series onto each other."""
    widest = max(len(EPISODE_KINDS), len(PROTOCOL_REVISIONS))
    assert len(PALETTE) >= widest
    assert len(LINE_STYLES) >= widest
    assert len(MARKERS) >= widest
    assert len(set(PALETTE)) == len(PALETTE)
    assert len(set(MARKERS)) == len(MARKERS)


def test_build_all_writes_every_figure(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from mcp_migration.report.figures import build_all

    # The author-share figure drops thin months, so give it months that survive
    # that filter rather than lowering the threshold it exists to enforce.
    bulk = [
        {
            **EPISODES[i % len(EPISODES)],
            "repo": f"r/{i}",
            "start": f"2026-0{7 + i % 2}-15T00:00:00+00:00",
        }
        for i in range(600)
    ]
    written = build_all(bulk, tmp_path)
    assert len(written) == 5
    for path in written:
        assert path.exists() and path.stat().st_size > 0


def test_author_share_drops_months_too_thin_to_be_a_ratio(tmp_path: Path) -> None:
    """A month holding three episodes swings between 0 and 100%; that is not signal."""
    pytest.importorskip("matplotlib")
    from mcp_migration.report.figures import author_kind_share

    with pytest.raises(ValueError, match="enough episodes"):
        author_kind_share(EPISODES, tmp_path / "x.pdf")


def test_plotting_an_empty_dataset_fails_loudly(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from mcp_migration.report.figures import episodes_by_kind

    with pytest.raises(ValueError, match="no episodes"):
        episodes_by_kind([], tmp_path / "x.pdf")


def test_end_state_figure_needs_migration_episodes(tmp_path: Path) -> None:
    """Every other kind is irrelevant here; plotting them would answer a
    different question than the one the figure is captioned with."""
    pytest.importorskip("matplotlib")
    from mcp_migration.report.figures import migration_end_states

    with pytest.raises(ValueError, match="no migration episodes"):
        migration_end_states(
            [{"kind": "v1_removal", "head_static_era": "sdk_v1", "start": "2026-08-01"}],
            tmp_path / "x.pdf",
        )
    out = migration_end_states(
        [{"kind": "migration", "head_static_era": "sdk_v2_default", "start": "2026-08-01"}],
        tmp_path / "y.pdf",
    )
    assert out.exists() and out.stat().st_size > 0


def test_technical_lag_draws_each_cohort_to_the_end_of_its_window(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from datetime import date

    from mcp_migration.report.figures import technical_lag
    from mcp_migration.report.lag import SDK_V2, Firsts, Release, lag_profile

    old = Release("2025-06-18", date(2025, 6, 18))
    sdk = Release(SDK_V2, date(2026, 7, 28), prerelease=date(2026, 6, 29))
    censor = date(2026, 9, 15)
    profiles = {
        "2025-06-18": lag_profile(
            old,
            [Firsts({"2025-03-26": date(2025, 4, 1), "2025-06-18": date(2025, 7, 1)})],
            censor,
        ),
        SDK_V2: lag_profile(sdk, [Firsts({}, v1_sdk=date(2026, 1, 1))], censor),
    }
    out = technical_lag(profiles, tmp_path / "lag.pdf")
    assert out.exists() and out.stat().st_size > 0
