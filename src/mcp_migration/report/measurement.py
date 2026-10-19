"""The revised measurements: what each indicator of migration can establish.

A dependency upgrade, support for the new protocol revision, retirement of the
legacy one, and adoption of the revision's new features are measured here as
separate dimensions rather than folded into one "migrated or not" state. They
diverge because the two official SDKs differ:

- Python v2 routes every request by its MCP-Protocol-Version header and serves
  both eras, always; there is no switch to turn the legacy era off.
- TypeScript v2 built from the v1-style classes keeps speaking only the 2025
  protocol; 2026-07-28 needs ``createMcpHandler`` or ``serveStdio``, which also
  serve legacy clients unless told ``legacy: 'reject'``.

So in Python the dependency version predicts protocol support, and in
TypeScript it does not. Neither predicts adoption of the new features.

Deferral is measured as time to an event with censoring, against a comparison
group, rather than read off monthly aggregates.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

RELEASE = date(2026, 7, 28)
MOVE_KINDS = ("migration", "v2_adoption")
PY_V2_KEYS = ("py_v2_mcpserver_import", "py_pin_mcp_v2")
TS_V2_KEYS = ("ts_v2_split_pkg",)
TS_MODERN_ENTRY = "ts_v2_modern_entry"
TS_LEGACY_REJECT = "ts_legacy_reject"
# Markers of a hand-written 2026-07-28 implementation, for code using no SDK.
CUSTOM_MODERN_KEYS = (
    "modern_server_discover",
    "modern_subscriptions_listen",
    "modern_version_const",
)
NEW_FEATURE_KEYS = ("modern_input_required", "modern_subscriptions_listen")
CHECKPOINTS = (30, 45)


def _day(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


# --- Time to event ------------------------------------------------------------


def _durations(
    rows: Iterable[tuple[date, date | None] | tuple[date, date | None, date]], censor: date
) -> list[tuple[int, bool]]:
    """(time, event happened) per row; a third element is a row's own censoring date."""
    out: list[tuple[int, bool]] = []
    for row in rows:
        start, event = row[0], row[1]
        end = min(censor, row[2]) if len(row) > 2 else censor
        if event is not None and event <= end:
            out.append((max(0, (event - start).days), True))
        else:
            out.append((max(0, (end - start).days), False))
    return out


def kaplan_meier(
    rows: Iterable[tuple[date, date | None] | tuple[date, date | None, date]],
    censor: date,
    checkpoints: Sequence[int] = CHECKPOINTS,
) -> dict[str, Any]:
    """Cumulative incidence of an event, with Greenwood 95% intervals.

    Each row is (start, event-or-None), optionally with its own censoring date
    as a third element (a control that leaves the comparison group). A row whose
    event is missing or after its censoring date is censored there. Returned
    shares are percentages; a checkpoint later than the longest follow-up is
    omitted rather than extrapolated. Where no event has occurred by a
    checkpoint, Greenwood's interval collapses to a point, so the upper limit is
    the exact one-sided 97.5% Clopper-Pearson bound on the number at risk.
    """
    durations = _durations(rows, censor)
    n = len(durations)
    if not n:
        return {}
    by_time: dict[int, list[bool]] = defaultdict(list)
    for t, happened in durations:
        by_time[t].append(happened)
    at_risk, survival, greenwood = n, 1.0, 0.0
    curve: list[tuple[int, float, float, float]] = []
    for t in sorted(by_time):
        events = sum(by_time[t])
        if events and at_risk:
            survival *= 1 - events / at_risk
            if at_risk > events:
                greenwood += events / (at_risk * (at_risk - events))
        se = survival * math.sqrt(greenwood)
        curve.append((t, survival, max(0.0, survival - 1.96 * se), min(1.0, survival + 1.96 * se)))
        at_risk -= len(by_time[t])
    longest = max(t for t, _ in durations)
    at: dict[int, dict[str, float]] = {}
    for checkpoint in checkpoints:
        if checkpoint > longest:
            continue
        s, lo, hi, risk = 1.0, 1.0, 1.0, n
        remaining = n
        for t, s_t, lo_t, hi_t in curve:
            if t > checkpoint:
                break
            s, lo, hi = s_t, lo_t, hi_t
            remaining -= len(by_time[t])
            risk = max(remaining, 1)
        if s == 1.0:
            # No event yet: Greenwood's interval collapses to a point, which
            # reads as certainty. Use the exact one-sided 97.5% upper bound on
            # the number still at risk at the checkpoint (Clopper-Pearson).
            upper = 100 * (1 - 0.025 ** (1 / max(risk, 1)))
            at[checkpoint] = {"pct": 0.0, "ci_low": 0.0, "ci_high": upper}
        else:
            at[checkpoint] = {
                "pct": 100 * (1 - s),
                "ci_low": 100 * (1 - hi),
                "ci_high": 100 * (1 - lo),
            }
    return {
        "n": n,
        "events": sum(1 for _, happened in durations if happened),
        "median_follow_up_days": sorted(t for t, _ in durations)[n // 2],
        "by_day": at,
        # The whole step function, as cumulative incidence in percent, for
        # plotting: (day, estimate, lower, upper).
        "curve": [
            (t, 100 * (1 - s_t), 100 * (1 - hi_t), 100 * (1 - lo_t)) for t, s_t, lo_t, hi_t in curve
        ],
    }


def _by_repo(episodes: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for episode in episodes:
        grouped[str(episode["repo"])].append(episode)
    for group in grouped.values():
        group.sort(key=lambda e: str(e["start"]))
    return grouped


def _first(
    group: Sequence[Mapping[str, Any]], kinds: Sequence[str], after: date | None = None
) -> date | None:
    for episode in group:
        start = _day(episode["start"])
        if episode["kind"] in kinds and (after is None or start > after):
            return start
    return None


def _on_v1_at_release(group: Sequence[Mapping[str, Any]]) -> bool:
    """On the v1 SDK, pinned or not, as of the last episode before the release."""
    before = [e for e in group if _day(e["end"]) < RELEASE]
    return bool(before) and str(before[-1].get("era_after")) in ("sdk_v1", "sdk_v1_pinned_below_2")


def deferral(
    episodes: Iterable[Mapping[str, Any]], censor: date, repos: set[str] | None = None
) -> dict[str, Any]:
    """Do repositories that pin below the major go on to move to v2?

    Two cohorts, both followed to ``censor``: repositories whose first pin came
    after the release, clocked from that pin; and repositories on v1 at the
    release that never pinned, clocked from the release. The event is the first
    migration or v2 adoption after the clock starts. Also counts, per
    repository, the order of its post-release responses, which is what monthly
    co-movement of pins and migrations cannot show.
    """
    grouped = _by_repo(episodes)
    if repos is not None:
        grouped = {r: g for r, g in grouped.items() if r in repos}
    pinned: list[tuple[date, date | None]] = []
    unpinned: list[tuple[date, date | None]] = []
    sequences: Counter[str] = Counter()
    for group in grouped.values():
        first_pin = _first(group, ("pin_below_2",))
        if first_pin is not None and RELEASE <= first_pin <= censor:
            pinned.append((first_pin, _first(group, MOVE_KINDS, after=first_pin)))
        elif first_pin is None and _on_v1_at_release(group):
            unpinned.append(
                (
                    RELEASE,
                    _first(group, MOVE_KINDS, after=date.fromordinal(RELEASE.toordinal() - 1)),
                )
            )
        responses = [
            "pin" if e["kind"] == "pin_below_2" else "move"
            for e in group
            if e["kind"] in ("pin_below_2", *MOVE_KINDS) and RELEASE <= _day(e["start"]) <= censor
        ]
        collapsed = [r for i, r in enumerate(responses) if i == 0 or r != responses[i - 1]]
        if collapsed:
            sequences["_".join(collapsed[:2])] += 1
    return {
        "pinned": kaplan_meier(pinned, censor),
        "unpinned_v1": kaplan_meier(unpinned, censor),
        "sequences": dict(sequences),
    }


# --- Deferral from commits, without episodes or hindsight ----------------------

PY_V2_ADD = ("py_v2_mcpserver_import", "py_pin_mcp_v2")
PY_V1_KEYS = ("py_v1_fastmcp_import", "py_v1_McpError", "py_v1_streamablehttp_client")
RISK_DAYS = (0, 10, 20, 30, 40)


def deferral_from_commits(
    commits_by_repo: Mapping[str, Sequence[Mapping[str, Any]]],
    heads: Mapping[str, Mapping[str, Any]],
    censor: date,
    controls_per_pin: int = 5,
    seed: int = 11,
    repos: set[str] | None = None,
    active_days: int | None = 90,
    drop_same_day: bool = False,
    censor_at_own_pin: bool = True,
    bootstrap: int = 0,
    reuse_controls: bool = True,
) -> dict[str, Any]:
    """Time from a deferring ceiling to Python v2, against date-matched controls.

    Corrects the episode-based cohort, which excluded a ceiling whenever v2
    arrived within the same 30-day episode and so removed exactly the quick
    movers. Here nothing about a repository's future decides its group:

    - The pinning event is the first post-release commit adding ``mcp<2``.
    - Its scope is read from the repository's state at that commit: if v2 is
      already declared or imported, or arrives in the same commit, the ceiling
      bounds an older dependency alongside v2 and is not a deferral.
    - The event is the first later commit adding Python v2 code.
    - Controls are Python repositories on v1 and without a ceiling on the
      pinning date, followed from that date; a control that later pins is
      censored at its own ceiling. With ``active_days``, a control must also
      have had a signal commit in that many days before the date: a project
      adding a ceiling is evidently maintained, and a dormant control would
      confound the comparison with activity. Only past information is used.
    - ``drop_same_day`` removes ceilings followed by v2 on the same day, which
      mix quick repairs with dual-version bounds split across two commits.
    - ``censor_at_own_pin=False`` keeps following a control after it adds its
      own ceiling, so the comparison is with repositories unpinned on the
      matching date rather than ones that stay unpinned throughout.
    - ``bootstrap`` resamples repository identities, with every entry of a
      sampled repository in either role, that many times. A control repository
      can serve several pinned repositories and a later pinner can be an earlier
      control, so entries sharing a repository are dependent; resampling
      repositories rather than entries or matched sets keeps that dependence.
    - ``reuse_controls=False`` draws each control repository at most once
      across all matched sets, visiting pins in random order, as a design check
      on reuse; with one control per pin the pool rarely runs short.

    ``mcp<2`` is a Python constraint, so both groups are Python repositories.
    """
    import random

    from mcp_migration.mine.episodes import Rewinder

    def py_v1(c: Mapping[str, int]) -> bool:
        return any(c.get(k, 0) > 0 for k in PY_V1_KEYS) and not any(
            c.get(k, 0) > 0 for k in PY_V2_ADD
        )

    ordered: dict[str, list[Mapping[str, Any]]] = {}
    rewinders: dict[str, Rewinder] = {}
    for repo, commits in commits_by_repo.items():
        head = heads.get(repo)
        if head is None or (repos is not None and repo not in repos):
            continue
        ordered[repo] = sorted(
            (c for c in commits if not c.get("is_merge")), key=lambda c: str(c["date"])
        )
        rewinders[repo] = Rewinder(ordered[repo], head.get("signals") or {})

    def first_after(repo: str, moment: str, keys: Sequence[str]) -> Mapping[str, Any] | None:
        return next(
            (
                c
                for c in ordered[repo]
                if str(c["date"]) > moment and any(c["added"].get(k) for k in keys)
            ),
            None,
        )

    scopes: Counter[str] = Counter()
    pinned: list[tuple[date, date | None]] = []
    pin_dates: list[tuple[str, str]] = []  # (repo, ISO moment)
    pairs: list[dict[str, Any]] = []  # each pinned repository's ceiling and later v2 commit
    pin_messages: list[str] = []  # the ceiling commit's subject, per pinned repository
    for repo, commits in ordered.items():
        pin = next(
            (
                c
                for c in commits
                if c["added"].get("py_pin_mcp_below_2") and RELEASE <= _day(c["date"]) <= censor
            ),
            None,
        )
        if pin is None:
            continue
        moment = str(pin["date"])
        before = rewinders[repo].counts(moment, inclusive=True)
        if any(pin["added"].get(k) for k in PY_V2_ADD):
            scopes["v2_same_commit"] += 1
            continue
        if any(before.get(k, 0) > 0 for k in PY_V2_ADD):
            scopes["v2_already_present"] += 1
            continue
        move = first_after(repo, moment, PY_V2_ADD)
        if drop_same_day and move is not None and _day(move["date"]) == _day(moment):
            scopes["same_day_v2"] += 1
            continue
        scopes["deferral"] += 1
        pinned.append((_day(moment), _day(move["date"]) if move else None))
        pin_dates.append((repo, moment))
        pin_messages.append(str(pin.get("message", "")))
        if move is not None and _day(move["date"]) <= censor:
            pairs.append(
                {
                    "repo": repo,
                    "gap": (_day(move["date"]) - _day(moment)).days,
                    "pin_message": str(pin.get("message", "")),
                    "move_message": str(move.get("message", "")),
                }
            )

    # Date-matched controls: on Python v1 and unpinned on the pinning date.
    rng = random.Random(seed)
    # Only repositories that ever touched the Python v1 SDK can be on it. A
    # repository that pins later stays in the pool: whether it ever pins is
    # future information, so it is eligible until it does and censored then.
    pool = sorted(
        r
        for r in ordered
        if (
            any(c["added"].get(k) or c["removed"].get(k) for c in ordered[r] for k in PY_V1_KEYS)
            or any((heads[r].get("signals") or {}).get(k, 0) > 0 for k in PY_V1_KEYS)
        )
    )
    # Eligibility is a property of the calendar day: state at its start.
    eligible_on: dict[date, list[str]] = {}
    for day in sorted({_day(m) for _, m in pin_dates}):
        start = f"{day.isoformat()}T00:00:00+00:00"
        floor = (day - timedelta(days=active_days)).isoformat() if active_days is not None else None
        eligible_on[day] = [
            r
            for r in pool
            if py_v1(state := rewinders[r].counts(start, inclusive=True))
            and state.get("py_pin_mcp_below_2", 0) == 0
            and (
                floor is None
                or any(floor <= str(c["date"])[:10] < day.isoformat() for c in ordered[r])
            )
        ]
    controls: list[tuple[date, date | None, date]] = []
    sets: list[tuple[tuple[date, date | None], list[tuple[date, date | None, date]]]] = []
    chosen: Counter[str] = Counter()
    switched = short_sets = 0
    prior_pinned: list[int] = []
    prior_controls: list[int] = []

    def prior_activity(repo: str, moment: str) -> int:
        """Signal commits in the 90 days before the matching date."""
        day = _day(moment)
        floor = (day - timedelta(days=90)).isoformat()
        return sum(1 for c in ordered[repo] if floor <= str(c["date"])[:10] < day.isoformat())

    entries: list[tuple[str, str, Any]] = []  # (repository, role, row)
    order = list(zip(pin_dates, pinned, strict=True))
    if not reuse_controls:
        # Without reuse, early pins would use up the pool and leave later ones
        # short, tilting controls toward early index dates; visit pins in
        # random order instead so shortfalls fall evenly across dates.
        rng.shuffle(order)
    for (pinner, moment), pin_row in order:
        entries.append((pinner, "pinned", pin_row))
        eligible = [
            r
            for r in eligible_on[_day(moment)]
            if r != pinner and (reuse_controls or r not in chosen)
        ]
        sample = rng.sample(eligible, min(controls_per_pin, len(eligible)))
        short_sets += len(sample) < controls_per_pin
        prior_pinned.append(prior_activity(pinner, moment))
        members: list[tuple[date, date | None, date]] = []
        for r in sample:
            chosen[r] += 1
            prior_controls.append(prior_activity(r, moment))
            move = first_after(r, moment, PY_V2_ADD)
            own_pin = first_after(r, moment, ("py_pin_mcp_below_2",))
            end: date | None = _day(move["date"]) if move else None
            leaves = censor
            if own_pin is not None and _day(own_pin["date"]) <= censor:
                switched += 1
                # A control that adds its own ceiling leaves the comparison
                # group then: censored at that date, not excluded in hindsight.
                if censor_at_own_pin:
                    leaves = _day(own_pin["date"])
                    if end is not None and _day(own_pin["date"]) <= end:
                        end = None
            members.append((_day(moment), end, leaves))
            entries.append((r, "control", members[-1]))
        controls.extend(members)
        sets.append((pin_row, members))

    def risk_table(rows: Sequence[Any]) -> list[dict[str, int]]:
        """Numbers still at risk at each day, and cumulative events by then."""
        durations = _durations(rows, censor)
        return [
            {
                "day": day,
                "at_risk": sum(1 for t, _ in durations if t >= day),
                "events": sum(1 for t, happened in durations if happened and t <= day),
            }
            for day in RISK_DAYS
        ]

    def median(values: Sequence[int]) -> float | None:
        ordered_values = sorted(values)
        if not ordered_values:
            return None
        mid = len(ordered_values) // 2
        if len(ordered_values) % 2:
            return float(ordered_values[mid])
        return (ordered_values[mid - 1] + ordered_values[mid]) / 2

    out: dict[str, Any] = {
        "scopes": dict(scopes),
        "pinned": kaplan_meier(pinned, censor),
        "controls": kaplan_meier(controls, censor),
        "risk_pinned": risk_table(pinned),
        "risk_controls": risk_table(controls),
        "controls_per_pin": controls_per_pin,
        "matching": {
            "control_entries": len(controls),
            "distinct_controls": len(chosen),
            "max_reuse": max(chosen.values(), default=0),
            "short_sets": short_sets,
            "controls_later_pinned": switched,
            "pool": len(pool),
            "prior_signal_commits_median_pinned": median(prior_pinned),
            "prior_signal_commits_median_controls": median(prior_controls),
        },
        "pairs": pairs,
        "pin_messages": pin_messages,
    }
    out["pinned_repos"] = [repo for repo, _ in pin_dates]
    if bootstrap and sets:
        out["difference_30"] = _matched_difference(sets, censor, 30, bootstrap, seed)
        out["clustered_30"] = _clustered(entries, censor, 30, bootstrap, seed)
    return out


def _clustered(
    entries: Sequence[tuple[str, str, Any]], censor: date, day: int, reps: int, seed: int
) -> dict[str, dict[str, float]]:
    """Repository-clustered bootstrap for both shares and their difference by ``day``.

    Resamples repository identities with replacement; a sampled repository
    brings every entry it has, as a pinned repository and as a control, so
    dependence between entries that share a repository is preserved. Percentile
    intervals, 95%; inference is over repositories, not over the control draw.
    """
    import random

    by_repo: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for repo, role, row in entries:
        by_repo[repo].append((role, row))
    repos = sorted(by_repo)

    def estimate(sample: Sequence[str]) -> tuple[float | None, float | None]:
        pinned = [row for r in sample for role, row in by_repo[r] if role == "pinned"]
        controls = [row for r in sample for role, row in by_repo[r] if role == "control"]
        return _incidence_at(pinned, censor, day), _incidence_at(controls, censor, day)

    rng = random.Random(seed)
    draws: dict[str, list[float]] = {"pinned": [], "controls": [], "gap": []}
    for _ in range(reps):
        a, b = estimate([repos[rng.randrange(len(repos))] for _ in repos])
        if a is None or b is None:
            continue
        draws["pinned"].append(a)
        draws["controls"].append(b)
        draws["gap"].append(a - b)
    a, b = estimate(repos)
    if a is None or b is None or not draws["gap"]:
        return {}
    point = {"pinned": a, "controls": b, "gap": a - b}
    out: dict[str, dict[str, float]] = {}
    for key, values in draws.items():
        values.sort()
        out[key] = {
            "pct": point[key],
            "ci_low": values[int(0.025 * (len(values) - 1))],
            "ci_high": values[int(0.975 * (len(values) - 1))],
            "reps": len(values),
        }
    out["repositories"] = {"pct": float(len(repos))}
    return out


def _incidence_at(rows: Sequence[Any], censor: date, day: int) -> float | None:
    """Kaplan-Meier cumulative incidence (percent) by ``day``, without intervals."""
    durations = sorted(_durations(rows, censor))
    if not durations:
        return None
    at_risk, survival, i = len(durations), 1.0, 0
    while i < len(durations) and durations[i][0] <= day:
        t = durations[i][0]
        group = [h for tt, h in durations[i:] if tt == t]
        events = sum(group)
        if events:
            survival *= 1 - events / at_risk
        at_risk -= len(group)
        i += len(group)
    if day > durations[-1][0] and survival > 0:
        return None  # beyond the longest follow-up, unless everyone had the event
    return 100 * (1 - survival)


def _matched_difference(
    sets: Sequence[tuple[Any, Sequence[Any]]], censor: date, day: int, reps: int, seed: int
) -> dict[str, float]:
    """Pinned minus control incidence by ``day``, with a matched-set bootstrap interval.

    Each resample draws whole matched sets with replacement, so a pinned
    repository stays with its controls and the interval reflects the matching.
    Percentile interval, 95%.
    """
    import random

    def estimate(chosen: Sequence[tuple[Any, Sequence[Any]]]) -> float | None:
        pinned = [p for p, _ in chosen]
        controls = [c for _, members in chosen for c in members]
        a, b = _incidence_at(pinned, censor, day), _incidence_at(controls, censor, day)
        return None if a is None or b is None else a - b

    point = estimate(sets)
    rng = random.Random(seed)
    draws = sorted(
        d
        for _ in range(reps)
        if (d := estimate([sets[rng.randrange(len(sets))] for _ in sets])) is not None
    )
    if point is None or not draws:
        return {}
    return {
        "pct": point,
        "ci_low": draws[int(0.025 * (len(draws) - 1))],
        "ci_high": draws[int(0.975 * (len(draws) - 1))],
        "reps": len(draws),
    }


# --- Protocol support as separate dimensions -----------------------------------


def _language(signals: Mapping[str, int]) -> str | None:
    py = any(signals.get(k, 0) > 0 for k in PY_V2_KEYS)
    ts = any(signals.get(k, 0) > 0 for k in TS_V2_KEYS)
    if py and ts:
        return "both"
    return "python" if py else "typescript" if ts else None


def rewind(
    signals: Mapping[str, int], commits: Iterable[Mapping[str, Any]], to: date
) -> dict[str, int]:
    """Signal counts as of the end of day ``to``: HEAD minus every later change.

    Used to bring a later mining pass back to the day the same repository was
    mined in the corpus, so the two describe one moment.
    """
    counts: Counter[str] = Counter({k: int(v) for k, v in signals.items()})
    for commit in commits:
        if _day(commit["date"]) > to:
            counts.subtract(commit["added"])
            counts.update(commit["removed"])
    return {k: n for k, n in counts.items() if n > 0}


def protocol_dimensions(
    heads: Mapping[str, Mapping[str, Any]],
    ts_heads: Mapping[str, Mapping[str, Any]],
    repos: set[str] | None = None,
    ts_servers: set[str] | None = None,
    ts_scanned: set[str] | None = None,
) -> dict[str, Any]:
    """Modern support, legacy support and new-feature adoption, per SDK language.

    The unit is a repository declaring the v2 SDK at the snapshot. Python v2
    serves both eras by construction. TypeScript v2 serves 2026-07-28 only with
    a modern entry point, read from ``ts_heads`` (a separate, TypeScript-only
    mining pass with those signals); a repository missing from it is reported
    as unresolved rather than guessed.
    """
    rows: dict[str, Counter[str]] = defaultdict(Counter)
    for repo, head in heads.items():
        if repos is not None and repo not in repos:
            continue
        signals = head.get("signals") or {}
        language = _language(signals)
        if language is None:
            continue
        row = rows[language]
        row["n"] += 1
        if any(signals.get(k, 0) > 0 for k in NEW_FEATURE_KEYS):
            row["new_features"] += 1
        # A repository with both SDKs serves 2026-07-28 through its Python side.
        if language in ("python", "both"):
            row["modern"] += 1
            row["legacy"] += 1
            continue
        # Only a server serves a protocol revision. ts_v2_split_pkg matches the
        # client package too, so a TypeScript repository counts only when its
        # code depends on @modelcontextprotocol/server.
        # A repository the scan could not reach is unresolved, not a client.
        scanned = ts_scanned is None or repo in ts_scanned
        if ts_servers is not None and scanned and repo not in ts_servers:
            row["n"] -= 1
            row["new_features"] -= any(signals.get(k, 0) > 0 for k in NEW_FEATURE_KEYS)
            rows["typescript_client_only"]["n"] += 1
            continue
        ts = ts_heads.get(repo)
        if ts is None:
            row["unresolved"] += 1
            continue
        ts_signals = ts.get("signals") or {}
        modern = ts_signals.get(TS_MODERN_ENTRY, 0) > 0
        row["modern"] += modern
        row["legacy"] += not (modern and ts_signals.get(TS_LEGACY_REJECT, 0) > 0)
    out: dict[str, Any] = {}
    client_only = rows.pop("typescript_client_only", None)
    if client_only:
        out["typescript_client_only"] = {"n": client_only["n"]}
    for language, row in sorted(rows.items()):
        resolved = row["n"] - row["unresolved"]
        out[language] = {
            "n": row["n"],
            "resolved": resolved,
            "pct_modern": 100 * row["modern"] / resolved if resolved else None,
            "pct_legacy": 100 * row["legacy"] / resolved if resolved else None,
            "pct_new_features": 100 * row["new_features"] / row["n"],
        }
    return out


# --- What a ceiling is used for ----------------------------------------------------

_REPAIR = re.compile(
    r"fix|break|broke|crash|error|unblock|import|fastmcp|modulenotfound|fail|restor", re.I
)
_LIFT = re.compile(r"lift|drop\b.*\b(pin|cap)|remove\b.*\b(cap|pin)|unpin", re.I)
_DUAL = re.compile(r"fallback|alongside|both lines|1\.x and 2\.x|keeps? 1\.x", re.I)


def ceiling_pairs(pairs: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Keyword reading of RQ2's pinned repositories that later added v2 code.

    ``pairs`` comes from ``deferral_from_commits``: each pinned repository's
    ceiling commit with its first later commit adding Python v2, within the
    follow-up. Both subject lines are classified by keyword, so the counts
    describe the same cohort as the time-to-event analysis.
    """
    out: Counter[str] = Counter()
    for pair in pairs:
        gap = int(pair["gap"])
        out["pairs"] += 1
        out["same_day"] += gap == 0
        out["three_weeks"] += gap >= 21
        out["pin_repair"] += bool(_REPAIR.search(str(pair["pin_message"])))
        out["move_lift"] += bool(_LIFT.search(str(pair["move_message"])))
        out["move_dual"] += bool(_DUAL.search(str(pair["move_message"])))
    return dict(out)


# A version must stand alone: "0.12.x" and "v1.2.0" are other packages' numbers.
_CLAIM = re.compile(
    r"migrat|upgrad|\bport\b|\bv2\b|(?<![\w.])2\.x|(?<![\w.])2\.0\b|scoped packages", re.I
)


def upgrade_claims(
    commits_by_repo: Mapping[str, Sequence[Mapping[str, Any]]],
    legacy_servers: Iterable[str],
    sample: int = 30,
    seed: int = 7,
) -> dict[str, Any]:
    """Do the commits that brought v2 to legacy-only servers call it a migration?

    A fixed-seed random sample of TypeScript server repositories that serve only
    the legacy protocol; for each, the subject of the commit that first added
    the v2 package, flagged by keyword. The subjects are returned too, so the
    keyword call can be read and checked by hand.
    """
    import random

    pool = sorted(r for r in legacy_servers if r in commits_by_repo)
    chosen = random.Random(seed).sample(pool, min(sample, len(pool)))
    subjects: list[tuple[str, str, bool]] = []
    for repo in chosen:
        first = next(
            (
                c
                for c in sorted(commits_by_repo[repo], key=lambda c: str(c["date"]))
                if c["added"].get("ts_v2_split_pkg")
            ),
            None,
        )
        if first is not None:
            text = str(first["message"])
            subjects.append((repo, text, bool(_CLAIM.search(text))))
    return {
        "n": len(subjects),
        "claims": sum(1 for _, _, claim in subjects if claim),
        "subjects": subjects,
    }


# --- How projects responded: moves and ceilings after the release --------------


def responses(
    commits_by_repo: Mapping[str, Sequence[Mapping[str, Any]]],
    heads: Mapping[str, Mapping[str, Any]],
    repos: set[str] | None = None,
) -> dict[str, Any]:
    """Repositories on a v1 SDK at the release, and where they are at harvest.

    State at the release is reconstructed from HEAD and the commits after it.
    A Python repository has *moved* if v2 is declared or imported at HEAD. It
    has *deferred* if its first post-release ceiling is a deferral under the
    same scope rule as the time-to-event analysis: the ceiling commit adds no v2
    code and v2 is not already present when it lands. Any ceiling addition is
    counted separately. A TypeScript repository has moved if it uses the split
    v2 packages at HEAD; TypeScript ceilings are not measured.
    """
    from mcp_migration.mine.episodes import Rewinder

    start = f"{RELEASE.isoformat()}T00:00:00+00:00"
    out: dict[str, Counter[str]] = {"python": Counter(), "typescript": Counter()}
    cohort: list[str] = []
    for repo, commits in commits_by_repo.items():
        if repos is not None and repo not in repos:
            continue
        head = (heads.get(repo) or {}).get("signals") or {}
        if not head and repo not in heads:
            continue
        ordered = sorted(
            (c for c in commits if not c.get("is_merge")), key=lambda c: str(c["date"])
        )
        rewinder = Rewinder(ordered, head)
        before = rewinder.counts(start, inclusive=False)
        if any(before.get(k, 0) > 0 for k in PY_V1_KEYS) and not any(
            before.get(k, 0) > 0 for k in PY_V2_ADD
        ):
            row = out["python"]
            row["on_v1"] += 1
            cohort.append(repo)
            moved = any(head.get(k, 0) > 0 for k in PY_V2_ADD)
            pin = next(
                (
                    c
                    for c in ordered
                    if c["added"].get("py_pin_mcp_below_2") and _day(c["date"]) >= RELEASE
                ),
                None,
            )
            deferred = (
                pin is not None
                and not any(pin["added"].get(k) for k in PY_V2_ADD)
                and not any(
                    rewinder.counts(str(pin["date"]), inclusive=True).get(k, 0) > 0
                    for k in PY_V2_ADD
                )
            )
            row["ceiling_any"] += pin is not None
            row["moved"] += moved
            row["deferred"] += deferred
            row["moved_only"] += moved and not deferred
            row["deferred_only"] += deferred and not moved
            row["both"] += moved and deferred
            row["neither"] += not moved and not deferred
        if before.get("ts_v1_sdk_pkg", 0) > 0 and before.get("ts_v2_split_pkg", 0) <= 0:
            row = out["typescript"]
            row["on_v1"] += 1
            row["moved"] += head.get("ts_v2_split_pkg", 0) > 0
    result: dict[str, Any] = {k: dict(v) for k, v in out.items()}
    result["cohort"] = cohort
    return result


def issue_themes(qualitative_path: Any, relevance_path: Any) -> dict[str, int]:
    """Themes of the sampled breakage issues, among those read as on topic."""
    import json

    if not qualitative_path.exists() or not relevance_path.exists():
        return {}
    relevant = {
        k
        for k, v in json.loads(relevance_path.read_text(encoding="utf-8")).items()
        if v["relevant"]
    }
    issues = json.loads(qualitative_path.read_text(encoding="utf-8"))["evidence"]["issues"]
    kept = [e for e in issues if f"{e['repo']}#{e['number']}" in relevant]
    out: Counter[str] = Counter(n=len(kept), sampled=len(issues))
    for e in kept:
        out.update(e["codes"])
    return dict(out)


def _feature_check(path: Any) -> dict[str, int]:
    """How many sampled feature-marker positives implement the feature in server code."""
    import json

    if not path.exists():
        return {}
    labels = json.loads(path.read_text(encoding="utf-8"))["labels"]
    return {"n": len(labels), "true": sum(1 for v in labels.values() if v["server_feature_use"])}


def _vignette(path: Any) -> dict[str, int]:
    """Counts from the manual reading of sampling use in renamed repositories."""
    import json

    if not path.exists():
        return {}
    labels = json.loads(path.read_text(encoding="utf-8"))["labels"]
    out = Counter(labels.values())
    out["n"] = len(labels)
    return dict(out)


# --- RQ1 from application code -------------------------------------------------

APP_FEATURE_KEYS = (
    "modern_input_required",
    "ts_v2_input_required",
    "py_v2_elicit",
    "modern_subscriptions_listen",
    "py_v2_subscription_bus",
)


def application_dimensions(
    scans: Mapping[str, Mapping[str, Any]],
    repos: set[str] | None = None,
    audit: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Source-inferred protocol support per SDK, from application code at HEAD.

    The unit is a repository whose application code (tests, examples and
    documentation excluded) declares a v2 SDK; a label means at least one
    application endpoint has the property. Support is inferred under the SDKs'
    documented behavior, not observed:

    - Python v2: both eras on every server, by the SDK's design, so those two
      dimensions are set by construction and serve as a control.
    - TypeScript v2 servers: new-revision support needs ``createMcpHandler`` or
      ``serveStdio``. A repository with such an entry point and a v1-style
      transport is *mixed*. Legacy support counts as retired only when
      ``legacy: 'reject'`` appears with no legacy route and no v1-style
      transport, since the documented pattern routes legacy traffic to another
      handler in front of a strict one.
    - Feature use: the protocol strings or the SDK helpers for multi-round-trip
      input and subscriptions, in application code.

    ``audit`` holds a manual reading of every TypeScript server the classifier
    calls legacy-only. With it, a repository read as having no v2 server (its
    server runs on the v1 SDK, or v2 appears only in a client, test server or
    bundle) leaves the denominator, and one serving 2026-07-28 through its own
    code counts as supporting the revision.
    """
    rows: dict[str, Counter[str]] = defaultdict(Counter)
    outside_app = 0
    for repo, scan in scans.items():
        if repos is not None and repo not in repos:
            continue
        app = scan.get("app_signals") or {}
        every = scan.get("signals") or {}
        py = any(app.get(k, 0) > 0 for k in PY_V2_KEYS)
        ts = app.get("ts_v2_server_pkg", 0) > 0
        if not (py or ts):
            if any(every.get(k, 0) > 0 for k in (*PY_V2_KEYS, "ts_v2_server_pkg")):
                outside_app += 1
            continue
        language = "both" if py and ts else "python" if py else "typescript"
        row = rows[language]
        row["n"] += 1
        row["features"] += any(app.get(k, 0) > 0 for k in APP_FEATURE_KEYS)
        if language in ("python", "both"):
            row["modern"] += 1
            row["legacy"] += 1
            continue
        modern = app.get(TS_MODERN_ENTRY, 0) > 0
        v1_style = app.get("ts_v1_transport_class", 0) > 0
        route = app.get("ts_legacy_route", 0) > 0
        reject = app.get(TS_LEGACY_REJECT, 0) > 0
        if not (modern or v1_style or route):
            # The server package is imported but no entry point is visible:
            # not evidence of a legacy-only server, so unresolved.
            row["n"] -= 1
            row["features"] -= any(app.get(k, 0) > 0 for k in APP_FEATURE_KEYS)
            rows["typescript_no_entry"]["n"] += 1
            continue
        label = (audit or {}).get(repo, {}).get("label") if not modern else None
        if label == "not_v2_server":
            row["n"] -= 1
            row["features"] -= any(app.get(k, 0) > 0 for k in APP_FEATURE_KEYS)
            rows["typescript_not_v2"]["n"] += 1
            continue
        if label == "modern_custom":
            modern = True
            row["custom_modern"] += 1
        row["modern"] += modern
        row["mixed"] += modern and v1_style
        row["legacy_only"] += not modern
        row["reject_any"] += reject
        retired = modern and reject and not route and not v1_style
        row["legacy"] += not retired
    out: dict[str, Any] = {"v2_outside_application_only": outside_app}
    no_entry = rows.pop("typescript_no_entry", None)
    out["typescript_no_entry"] = int(no_entry["n"]) if no_entry else 0
    not_v2 = rows.pop("typescript_not_v2", None)
    out["typescript_not_v2_server"] = int(not_v2["n"]) if not_v2 else 0
    for language, row in sorted(rows.items()):
        n = row["n"]
        entry: dict[str, Any] = {
            "n": n,
            "pct_modern": 100 * row["modern"] / n,
            "pct_legacy": 100 * row["legacy"] / n,
            "pct_features": 100 * row["features"] / n,
            "features": row["features"],
        }
        if language == "typescript":
            entry.update(
                {
                    "legacy_only": row["legacy_only"],
                    "mixed": row["mixed"],
                    "modern_entry_only": row["modern"] - row["mixed"],
                    "reject_any": row["reject_any"],
                    "modern": row["modern"],
                    "custom_modern": row["custom_modern"],
                    "legacy_retired": n - row["legacy"],
                }
            )
        out[language] = entry
    return out


def _history_validation(path: Any) -> dict[str, Any]:
    """Summary of report.validate_history results, if they have been produced."""
    import json

    if not path.exists():
        return {}
    results = json.loads(path.read_text(encoding="utf-8"))
    rows = [(r.get("merges", 0), x) for r in results if "rows" in r for x in r["rows"]]
    if not rows:
        return {}
    merged = [x for m, x in rows if m > 0]
    return {
        "repos": sum(1 for r in results if "rows" in r),
        "commits": len(rows),
        "pct_counts": 100 * sum(x["counts_equal"] for _, x in rows) / len(rows),
        "pct_era": 100 * sum(x["era_equal"] for _, x in rows) / len(rows),
        "merged_commits": len(merged),
        "pct_counts_merged": 100 * sum(x["counts_equal"] for x in merged) / len(merged)
        if merged
        else None,
    }


def classify_application(app: Mapping[str, int]) -> dict[str, Any]:
    """The RQ1 calls the classifier makes for one repository's application code."""
    py = any(app.get(k, 0) > 0 for k in PY_V2_KEYS)
    modern = app.get(TS_MODERN_ENTRY, 0) > 0
    v1_style = app.get("ts_v1_transport_class", 0) > 0
    route = app.get("ts_legacy_route", 0) > 0
    reject = app.get(TS_LEGACY_REJECT, 0) > 0
    return {
        "v2": py or app.get("ts_v2_server_pkg", 0) > 0,
        "server": modern or v1_style or route,
        "modern": modern,
        "legacy": not (modern and reject and not route and not v1_style),
        "mixed": modern and v1_style,
    }


def audit_agreement(
    scans: Mapping[str, Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Agreement between the classifier and a manual reading of the code.

    Compares only the judgements the reader could establish (non-null labels):
    for TypeScript whether a server entry point exists, and if so its new-revision
    support, legacy support and mixed status; for Python whether a v2 SDK is
    declared in application code.
    """
    decisions: Counter[str] = Counter()
    agreed: Counter[str] = Counter()
    for repo, label in labels.items():
        scan = scans.get(repo)
        if scan is None:
            continue
        call = classify_application(scan.get("app_signals") or {})
        fields = ("v2",) if "v2" in label else ("server", "modern", "legacy", "mixed")
        for field in fields:
            truth = label.get(field)
            if truth is None or (field != "server" and field != "v2" and not label.get("server")):
                continue
            decisions[field] += 1
            agreed[field] += call[field] == truth
    total = sum(decisions.values())
    return {
        "repos": sum(1 for r in labels if r in scans),
        "decisions": total,
        "agreed": sum(agreed.values()),
        "pct": 100 * sum(agreed.values()) / total if total else None,
        "by_field": {f: {"n": decisions[f], "agreed": agreed[f]} for f in decisions},
    }


# --- Classifier artefacts (M7) ---------------------------------------------------


def migration_without_sdk(
    episodes: Iterable[Mapping[str, Any]],
    commits_by_repo: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Counter[str]:
    """What triggered "migration" episodes that end with no v2 SDK declared.

    Counts the added v2-side signal behind each such episode, to separate the
    rename of an error class or a protocol-feature string from an SDK upgrade.
    """
    triggers: Counter[str] = Counter()
    for episode in episodes:
        if episode["kind"] != "migration" or episode.get("era_after") not in (
            "sdk_v1",
            "no_official_sdk",
            "sdk_v1_pinned_below_2",
        ):
            continue
        shas = set(episode["shas"])
        added: Counter[str] = Counter()
        for commit in commits_by_repo.get(str(episode["repo"]), ()):
            if commit["sha"] in shas:
                added.update({k: 1 for k, v in commit["added"].items() if v})
        key = next(
            (
                k
                for k in (
                    "py_v2_MCPError",
                    "modern_server_discover",
                    "modern_subscriptions_listen",
                    "modern_input_required",
                    "py_pin_mcp_v2",
                    "ts_v2_split_pkg",
                    "py_v2_mcpserver_import",
                )
                if added.get(k)
            ),
            "other",
        )
        triggers[key] += 1
    return triggers


# --- Loading and the summary block ---------------------------------------------


def load_measurement(paths: Any, censor: date | None, seeds: set[str]) -> dict[str, Any]:
    """Every revised measurement for the snapshot on disk.

    TypeScript era serving comes from a separate mining pass in the sibling
    TypeScript data under ``data/ts`` (``paths.ts_root``), rewound to the day
    each repository was mined in the corpus so the two describe one moment.
    """
    import json
    from datetime import datetime

    from mcp_migration.storage import read_jsonl

    if censor is None or not paths.episodes.exists():
        return {}
    episodes = list(read_jsonl(paths.episodes))
    heads = {
        p.stem.replace("__", "/", 1): json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(paths.heads_dir.glob("*.json"))
    }
    mined_on = {
        p.stem.replace("__", "/", 1): datetime.fromtimestamp(p.stat().st_mtime).date()
        for p in paths.heads_dir.glob("*.json")
    }
    ts_root = paths.ts_root / "interim"
    ts_heads: dict[str, dict[str, Any]] = {}
    for head_file in sorted((ts_root / "heads").glob("*.json")) if ts_root.exists() else []:
        repo = head_file.stem.replace("__", "/", 1)
        commits_file = ts_root / "commits" / f"{head_file.stem}.jsonl"
        commits = list(read_jsonl(commits_file)) if commits_file.exists() else []
        signals = json.loads(head_file.read_text(encoding="utf-8")).get("signals") or {}
        ts_heads[repo] = {"signals": rewind(signals, commits, mined_on.get(repo, censor))}

    # Which TypeScript repositories ship a server: a shallow scan of their HEAD
    # with the split package signals, in data/ts/head-scan.
    scan_dir = paths.ts_root / "head-scan"
    ts_servers: set[str] | None = None
    ts_scanned: set[str] | None = None
    if scan_dir.exists() and any(scan_dir.glob("*.json")):
        ts_scanned = {p.stem.replace("__", "/", 1) for p in scan_dir.glob("*.json")}
        ts_servers = {
            p.stem.replace("__", "/", 1)
            for p in scan_dir.glob("*.json")
            if (json.loads(p.read_text(encoding="utf-8")).get("signals") or {}).get(
                "ts_v2_server_pkg", 0
            )
            > 0
        }
    # Application-level HEAD scans of every v2 repository (RQ1), in data/ts/app-scan.
    app_dir = paths.ts_root / "app-scan"
    app_scans = {
        p.stem.replace("__", "/", 1): json.loads(p.read_text(encoding="utf-8"))
        for p in (sorted(app_dir.glob("*.json")) if app_dir.exists() else [])
    }
    # One pass over the commit files serves both uses below.
    commits_by_repo = {
        f.stem.replace("__", "/", 1): list(read_jsonl(f)) for f in paths.commits_dir.glob("*.jsonl")
    }
    # Agent use before the release, so the stratifier cannot be produced by
    # the response it is meant to explain.
    agent_before = {
        repo
        for repo, commits in commits_by_repo.items()
        if any(
            c["author_kind"] == "ai_agent_assisted" and _day(c["date"]) < RELEASE for c in commits
        )
    }
    everyone = set(heads)
    ts_dir = paths.ts_root
    legacy_audit_file = ts_dir / "legacy-only-audit.json"
    legacy_audit = (
        json.loads(legacy_audit_file.read_text(encoding="utf-8"))
        if legacy_audit_file.exists()
        else None
    )

    def deferral(**options: Any) -> dict[str, Any]:
        options.setdefault("censor_at_own_pin", False)
        result = deferral_from_commits(commits_by_repo, heads, censor, **options)
        for key in ("pairs", "pin_messages", "pinned_repos"):
            result.pop(key, None)
        return result

    from mcp_migration.report.qualitative import code_pin_messages

    # The comparison is with repositories unpinned on the matching date, so a
    # control is followed whatever it does later; censoring it at its own
    # ceiling is the sensitivity analysis.
    primary = deferral_from_commits(
        commits_by_repo, heads, censor, bootstrap=1000, censor_at_own_pin=False
    )
    pairs = primary.pop("pairs", [])
    pin_messages = primary.pop("pin_messages", [])
    pinned_repos = set(primary.pop("pinned_repos", []))
    release = responses(commits_by_repo, heads)
    cohort = set(release.pop("cohort", []))
    release_corpus = responses(commits_by_repo, heads, repos=seeds)
    release_corpus.pop("cohort", None)
    return {
        "censor": censor.isoformat(),
        # RQ2 from commits: ceilings classified at their own commit, followed
        # forward, against date-matched controls active in the prior 90 days.
        "deferral": primary,
        "deferral_same_day_excluded": deferral(drop_same_day=True),
        "deferral_unmatched": deferral(active_days=None),
        "deferral_censor_controls": deferral(censor_at_own_pin=True),
        "deferral_no_reuse": deferral(reuse_controls=False, controls_per_pin=1),
        "deferral_corpus": deferral(repos=seeds),
        "deferral_agent": deferral(repos=agent_before & everyone),
        "deferral_no_agent": deferral(repos=everyone - agent_before),
        "protocol": protocol_dimensions(
            heads, ts_heads, ts_servers=ts_servers, ts_scanned=ts_scanned
        ),
        "protocol_corpus": protocol_dimensions(heads, ts_heads, seeds, ts_servers, ts_scanned),
        "migration_without_sdk": dict(migration_without_sdk(episodes, commits_by_repo)),
        "ceiling_pairs": ceiling_pairs(pairs),
        "pin_wording": {
            "n": len(pin_messages),
            **code_pin_messages(pin_messages),
            "check": json.loads((ts_dir / "pin-wording-check.json").read_text(encoding="utf-8"))
            if (ts_dir / "pin-wording-check.json").exists()
            else {},
        },
        "responses": release,
        "responses_corpus": release_corpus,
        "cohort_flow": {
            "pinned": len(pinned_repos),
            "pinned_in_release_cohort": len(pinned_repos & cohort),
        },
        "issue_themes": issue_themes(paths.qualitative, ts_dir / "issue-relevance.json"),
        "sampling_vignette": _vignette(ts_dir / "sampling-vignette-labels.json"),
        "feature_check": _feature_check(ts_dir / "feature-marker-check.json"),
        "history_validation": _history_validation(paths.ts_root / "history-validation.json"),
        # Classifier output, and the same after the manual reading of every
        # repository it calls legacy-only.
        "protocol_app_classifier": application_dimensions(app_scans) if app_scans else {},
        "protocol_app": application_dimensions(app_scans, audit=legacy_audit) if app_scans else {},
        "app_scanned": len(app_scans),
        "legacy_audit": dict(Counter(v["label"] for v in (legacy_audit or {}).values())),
        "audit": (
            audit_agreement(
                app_scans,
                json.loads((paths.ts_root / "audit-labels.json").read_text(encoding="utf-8")),
            )
            if app_scans and (paths.ts_root / "audit-labels.json").exists()
            else {}
        ),
        # A second sample, read after the audit's corrections and not used to
        # make them: the unbiased estimate of the classifier's agreement.
        "holdout": (
            audit_agreement(
                app_scans,
                json.loads((paths.ts_root / "holdout-labels.json").read_text(encoding="utf-8")),
            )
            if app_scans and (paths.ts_root / "holdout-labels.json").exists()
            else {}
        ),
        "protocol_app_corpus": (
            application_dimensions(app_scans, seeds, audit=legacy_audit) if app_scans else {}
        ),
        "app_scan_date": (
            datetime.fromtimestamp(max(p.stat().st_mtime for p in app_dir.glob("*.json")))
            .date()
            .isoformat()
            if app_scans
            else None
        ),
        # Sampled from TypeScript servers the application scan finds legacy-only.
        "upgrade_claims": (
            claims := upgrade_claims(
                commits_by_repo,
                {
                    r
                    for r, scan in app_scans.items()
                    if (call := classify_application(scan.get("app_signals") or {}))["server"]
                    and not call["modern"]
                    and not any((scan.get("app_signals") or {}).get(k, 0) > 0 for k in PY_V2_KEYS)
                    # Only those the manual reading confirms as legacy-only v2 servers.
                    and (
                        legacy_audit is None
                        or legacy_audit.get(r, {}).get("label") == "legacy_only"
                    )
                },
            )
        ),
        "upgrade_claims_manual": _manual_claims(
            ts_dir / "upgrade-claims-labels.json", claims["subjects"]
        ),
        "n_agent_before": len(agent_before & everyone),
    }


def _manual_claims(path: Any, subjects: Sequence[tuple[str, str, bool]]) -> dict[str, int]:
    """A manual reading of the sampled subjects, to check the keyword call."""
    import json

    if not path.exists():
        return {}
    labels = json.loads(path.read_text(encoding="utf-8"))
    return {
        "n": len(labels),
        "claims": sum(1 for v in labels.values() if v["claim"]),
        "names_revision": sum(1 for v in labels.values() if v["names_revision"]),
        "both": sum(1 for v in labels.values() if v["claim"] and v["names_revision"]),
        "keyword_agrees": sum(
            1 for repo, _, flag in subjects if repo in labels and labels[repo]["claim"] == flag
        ),
    }
