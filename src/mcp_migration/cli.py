"""Command-line entry point: ``mcp-migration <command>``.

Pipeline order: search -> seed -> metadata -> filter -> clone -> mine ->
episodes -> sample -> stats.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mcp_migration.collect import search as gh
from mcp_migration.collect.clone import clone_or_update
from mcp_migration.collect.filtering import (
    DEFAULT_MAX_DISK_KB,
    DEFAULT_PUSHED_AFTER,
    Criteria,
    apply,
)
from mcp_migration.collect.metadata import fetch_metadata
from mcp_migration.config import (
    DEFAULT_DATA_DIR,
    DEFAULT_QUERIES_FILE,
    DEFAULT_SINCE,
    Paths,
    github_token,
    github_tokens,
)
from mcp_migration.mine.episodes import DEFAULT_MAX_GAP_DAYS, INCIDENTAL, build_episodes
from mcp_migration.mine.history import (
    TIMEOUT_PREFIX,
    backfill_parents,
    mine_many,
    mine_repo,
)
from mcp_migration.models import Episode
from mcp_migration.report.labels import (
    agreement_details,
    consensus,
    invalid_rows,
    only,
    precision,
    precision_by_signal,
    prelabel_uptake,
    read_sheet,
    weighted_precision,
)
from mcp_migration.report.sampling import stratified_sample, write_labeling_sheet
from mcp_migration.report.stats import exclusion_profile, load_all, summarize, to_latex
from mcp_migration.repos import load_seed_file, repo_dir_name
from mcp_migration.storage import (
    read_json,
    read_jsonl,
    read_lines,
    write_json,
    write_jsonl,
    write_lines,
)

log = logging.getLogger("mcp_migration")


def cmd_search(args: argparse.Namespace, paths: Paths) -> int:
    tokens = github_tokens()
    if not tokens:
        log.warning("GITHUB_TOKEN not set; search will run at the unauthenticated rate limit")
    client = gh.GitHubClient(tokens)
    queries = gh.load_queries(args.queries)
    if args.only:
        queries = [q for q in queries if q.name in set(args.only)]
    until = args.until or date.today()
    for query in queries:
        out = paths.search_dir / f"{query.name}.jsonl"
        if out.exists() and not args.force:
            log.info("%s: already collected, skipping (use --force to redo)", query.name)
            continue
        count = write_jsonl(out, gh.search(client, query, until))
        log.info("%s: %d hits -> %s", query.name, count, out)
    merged = write_jsonl(
        paths.search_hits,
        (hit for f in sorted(paths.search_dir.glob("*.jsonl")) for hit in read_jsonl(f)),
    )
    log.info("merged %d hits into %s", merged, paths.search_hits)
    return 0


def cmd_seed(args: argparse.Namespace, paths: Paths) -> int:
    slugs: set[str] = set()
    for source in args.sources:
        found = load_seed_file(source)
        log.info("%s: %d repositories", source, len(found))
        slugs |= found
    if args.include_search_hits:
        # Read the per-query files rather than the merged one: the merge happens
        # only after every query completes, so an interrupted or partial search
        # would otherwise contribute nothing.
        files = sorted(paths.search_dir.glob("*.jsonl"))
        for path in files:
            from_query = {h["repo"] for h in read_jsonl(path) if h.get("repo")}
            log.info("%s: %d repositories", path.name, len(from_query))
            slugs |= from_query
        if not files:
            log.warning("no search hits found in %s", paths.search_dir)
    count = write_lines(paths.repo_list, sorted(slugs))
    log.info("wrote %d unique repositories to %s", count, paths.repo_list)
    return 0


def cmd_metadata(args: argparse.Namespace, paths: Paths) -> int:
    slugs = read_lines(paths.repo_list)
    done = (
        {r["repo"] for r in read_jsonl(paths.repo_meta)}
        if paths.repo_meta.exists() and not args.force
        else set()
    )
    todo = [s for s in slugs if s not in done]
    log.info("%d repositories, %d already fetched, %d to go", len(slugs), len(done), len(todo))
    if not todo:
        return 0
    client = gh.GitHubClient(github_tokens())
    records = list(read_jsonl(paths.repo_meta)) if paths.repo_meta.exists() else []
    # Flush periodically: a run over tens of thousands of repositories takes long
    # enough that losing all of it to one failure is not acceptable. Re-running
    # picks up from the last flush.
    try:
        for record in fetch_metadata(client, todo, args.batch_size):
            records.append(record)
            if len(records) % args.flush_every == 0:
                write_jsonl(paths.repo_meta, iter(records))
    finally:
        write_jsonl(paths.repo_meta, iter(records))
    log.info("wrote %d metadata records to %s", len(records), paths.repo_meta)
    return 0


def cmd_filter(args: argparse.Namespace, paths: Paths) -> int:
    criteria = Criteria(
        pushed_after=args.pushed_after,
        max_disk_kb=args.max_disk_kb,
        exclude_archived=not args.include_archived,
        exclude_forks=not args.include_forks,
        exclude_upstream=not args.include_upstream,
    )
    metadata = list(read_jsonl(paths.repo_meta))
    kept, reasons = apply(metadata, criteria)
    write_lines(paths.clone_list, kept)
    log.info("kept %d of %d repositories", len(kept), len(metadata))
    for reason, count in reasons.most_common():
        log.info("  excluded %6d: %s", count, reason)
    log.info("wrote %s", paths.clone_list)
    return 0


def cmd_clone(args: argparse.Namespace, paths: Paths) -> int:
    source = paths.clone_list if paths.clone_list.exists() else paths.repo_list
    log.info("cloning from %s", source)
    slugs = read_lines(source)[: args.limit]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda s: clone_or_update(s, paths.repos), slugs))
    log.info("cloned/updated %d of %d repositories", sum(results), len(slugs))
    return 0


def cmd_mine(args: argparse.Namespace, paths: Paths) -> int:
    repo_dirs = sorted(d for d in paths.repos.glob("*") if (d / ".git").exists())
    if not args.force:
        repo_dirs = [d for d in repo_dirs if not (paths.heads_dir / f"{d.name}.json").exists()]
    since = datetime.combine(args.since, datetime.min.time(), tzinfo=UTC)
    log.info("mining %d repositories with %d workers", len(repo_dirs), args.workers)
    failures = 0
    for result in mine_many(repo_dirs, since, args.workers):
        name = result.repo_dir.name
        if result.error or result.head is None:
            failures += 1
            log.warning("%s failed: %s", name, result.error)
            continue
        write_jsonl(paths.commits_dir / f"{name}.jsonl", (c.to_dict() for c in result.commits))
        write_json(paths.heads_dir / f"{name}.json", result.head.to_dict())
        log.info("%s: %d signal commits, era=%s", name, len(result.commits), result.head.static_era)
    log.info("done (%d failures)", failures)
    return 1 if failures and failures == len(repo_dirs) else 0


def _harvest_one(
    slug: str,
    paths: Paths,
    since: datetime,
    keep: bool,
    budget_s: float | None = None,
    prefilter: bool = False,
) -> tuple[str, str | None]:
    """Clone one repository, mine it, and remove the clone again.

    The unit of work is one repository so that peak disk is bounded by what is
    in flight rather than by the corpus: the seed is 2.1 TB cloned end to end,
    which is what forces a size cap, but only a handful of repositories exist
    at once here. What is kept is the mined output, which is three orders of
    magnitude smaller.

    The download is not avoided, only the accumulation. Re-mining under changed
    signal patterns means fetching everything again, so a run that intends to
    iterate should pass --keep.
    """
    import shutil

    name = repo_dir_name(slug)
    repo_dir = paths.repos / name
    already = (repo_dir / ".git").exists()
    try:
        if not clone_or_update(slug, paths.repos):
            return name, "clone failed"
        result = mine_repo(repo_dir, since, budget_s, prefilter)
        if result.error or result.head is None:
            return name, result.error or "no head"
        write_jsonl(paths.commits_dir / f"{name}.jsonl", (c.to_dict() for c in result.commits))
        write_json(paths.heads_dir / f"{name}.json", result.head.to_dict())
        return name, None
    finally:
        # Never delete a clone this call did not create: a --keep corpus from an
        # earlier run is someone else's working set.
        if not keep and not already and repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)


def harvest_todo(args: argparse.Namespace, paths: Paths) -> tuple[list[str], Path]:
    """Which repositories a harvest run still has to do, and where they came from.

    Separate from the run itself so it can be checked directly: a pass over
    thousands of repositories has to be resumable after an interrupt, and the
    process pool that does the work cannot be reached from a test.
    """
    source = paths.clone_list if paths.clone_list.exists() else paths.repo_list
    slugs = read_lines(source)[: args.limit]
    if not args.force:
        slugs = [s for s in slugs if not (paths.heads_dir / f"{repo_dir_name(s)}.json").exists()]
    return slugs, source


def cmd_harvest(args: argparse.Namespace, paths: Paths) -> int:
    """Clone, mine and discard each repository in turn."""
    slugs, source = harvest_todo(args, paths)
    since = datetime.combine(args.since, datetime.min.time(), tzinfo=UTC)
    log.info(
        "harvesting %d repositories from %s with %d workers (%s%s)",
        len(slugs),
        source,
        args.workers,
        "keeping clones" if args.keep else "deleting each clone after mining",
        ", git prefilter" if args.prefilter else "",
    )
    done = failures = 0
    ledger = OutcomeLedger(paths.harvest_outcomes)
    # Processes, not threads. Cloning is network-bound but mining is CPU-bound
    # Python: under a ThreadPoolExecutor the workers serialise on the GIL and
    # the whole run pins one core, which is a slowdown large enough to look
    # like a hang. `mine_many` has always used processes for the same reason.
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(_harvest_one, s, paths, since, args.keep, args.budget, args.prefilter)
            for s in slugs
        ]
        by_reason: Counter[str] = Counter()
        for future in as_completed(futures):
            name, error = future.result()
            ledger.record(name, error)
            if error:
                failures += 1
                by_reason[_outcome_reason(error)] += 1
                log.warning("%s failed: %s", name, error)
            else:
                done += 1
                if done % 100 == 0:
                    log.info("  %d mined, %d failed", done, failures)
    log.info("%d outcomes on record in %s", len(ledger), paths.harvest_outcomes)
    log.info("harvested %d repositories (%d not mined: %s)", done, failures, dict(by_reason))
    return 1 if failures and not done else 0


class OutcomeLedger:
    """Why each repository a harvest could not mine was dropped, kept on disk.

    Written as each repository finishes rather than once at the end: a run
    over thousands of repositories takes days, and an interrupt that lost
    every verdict would leave the paper unable to say what was dropped. Keyed
    by repository so a resumed run corrects an earlier verdict instead of
    double-counting it, and a repository that succeeds on retry is struck off,
    since it was not dropped after all.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        prior = read_jsonl(path) if path.exists() else iter(())
        self._by_repo = {str(o["repo"]): o for o in prior}

    def __len__(self) -> int:
        return len(self._by_repo)

    def record(self, name: str, error: str | None) -> None:
        if error:
            self._by_repo[name] = {
                "repo": name,
                "reason": _outcome_reason(error),
                "detail": error,
            }
        elif self._by_repo.pop(name, None) is None:
            return  # nothing on record changed
        # Rewritten whole, atomically: a few thousand lines at most, and an
        # interrupt mid-write must not leave a truncated ledger behind.
        write_jsonl(self.path, iter(self._by_repo.values()))


def _outcome_reason(error: str) -> str:
    """Bucket a failure into something countable in a table."""
    if error.startswith(TIMEOUT_PREFIX):
        return "timeout"
    if "gone" in error or "clone failed" in error:
        return "unavailable"
    return "mining_error"


def cmd_backfill_parents(args: argparse.Namespace, paths: Paths) -> int:
    """Fill parent_sha into records mined before the field existed.

    Run this while the clones are still on disk. After a harvest deletes them
    the information is only recoverable by cloning again.
    """
    files = sorted(paths.commits_dir.glob("*.jsonl"))
    log.info("backfilling parents across %d repositories", len(files))
    filled = touched = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(backfill_parents, f, paths.repos / f.stem) for f in files]
        for future in futures:
            n = future.result()
            if n:
                touched += 1
                filled += n
                if touched % 500 == 0:
                    log.info("  %d repositories, %d commits filled", touched, filled)
    log.info("filled %d commits across %d repositories", filled, touched)
    return 0


def cmd_episodes(args: argparse.Namespace, paths: Paths) -> int:
    episodes: list[Episode] = []
    for commits_file in sorted(paths.commits_dir.glob("*.jsonl")):
        head_file = paths.heads_dir / f"{commits_file.stem}.json"
        head = read_json(head_file) if head_file.exists() else None
        episodes.extend(build_episodes(read_jsonl(commits_file), head, args.max_gap_days))
    kept = [e for e in episodes if e.kind != INCIDENTAL]
    count = write_jsonl(paths.episodes, (e.to_dict() for e in kept))
    dropped = write_jsonl(
        paths.incidental_episodes, (e.to_dict() for e in episodes if e.kind == INCIDENTAL)
    )
    log.info("wrote %d episodes to %s", count, paths.episodes)
    log.info("set aside %d incidental episodes in %s", dropped, paths.incidental_episodes)
    return 0


def cmd_sample(args: argparse.Namespace, paths: Paths) -> int:
    episodes = [Episode(**record) for record in read_jsonl(paths.episodes)]
    if args.kinds:
        episodes = [e for e in episodes if e.kind in set(args.kinds)]
    # A sheet that shaped the detector's rules cannot also measure them, so a
    # later round draws only episodes no earlier sheet has seen.
    seen = {str(r.get("episode_id", "")) for sheet in args.exclude or [] for r in read_sheet(sheet)}
    if seen:
        before = len(episodes)
        episodes = [e for e in episodes if e.episode_id not in seen]
        log.info("excluded %d episodes already labeled", before - len(episodes))
    out = args.out or paths.labeling_sheet(args.round)
    sample = stratified_sample(episodes, args.per_stratum, args.seed)
    write_labeling_sheet(out, sample)
    log.info("wrote %d episodes to %s", len(sample), out)
    return 0


def cmd_show(args: argparse.Namespace, paths: Paths) -> int:
    """Print one episode in full, for a coder working through the sheet."""
    episode = next(
        (e for e in read_jsonl(paths.episodes) if e["episode_id"] == args.episode_id), None
    )
    if episode is None:
        log.error("no episode %s in %s", args.episode_id, paths.episodes)
        return 1
    repo, shas = episode["repo"], episode["shas"]
    print(f"{episode['episode_id']}   {episode['kind']}   scope={episode['scope']}")
    print(f"  repo           https://github.com/{repo}")
    print(f"  window         {episode['start']} .. {episode['end']}")
    print(f"  commits        {len(shas)}   +{episode['lines_added']}/-{episode['lines_deleted']}")
    print(f"  detector says  era={episode['head_static_era']}  (judge before believing)")
    if episode["revisions_added"] or episode["revisions_removed"]:
        print(f"  revisions      +{episode['revisions_added']} -{episode['revisions_removed']}")
    if episode["deprecated_removed"]:
        print(f"  deprecated     {episode['deprecated_removed']} removed")
    if episode["replacements"]:
        print(f"  replacements   {episode['replacements']}")
    print()
    if len(shas) == 1:
        print(f"  view  https://github.com/{repo}/commit/{shas[0]}")
    else:
        print(f"  view  https://github.com/{repo}/compare/{shas[0]}^...{shas[-1]}")
    print()
    commits_file = paths.commits_dir / f"{repo.replace('/', '__')}.jsonl"
    commits = {c["sha"]: c for c in read_jsonl(commits_file)} if commits_file.exists() else {}
    for sha in shas:
        c = commits.get(sha)
        if c is None:
            print(f"  {sha[:10]}  (not in the mined commits)")
            continue
        lines = str(c.get("message", "")).splitlines()
        subject = lines[0] if lines else ""
        print(f"  {sha[:10]}  {c['date'][:10]}  {c['author_kind']:18s} {subject}")
        for label, counts in (("+", c.get("added", {})), ("-", c.get("removed", {}))):
            if counts:
                print(f"              {label} {', '.join(sorted(counts))}")
    return 0


def cmd_qualitative(args: argparse.Namespace, paths: Paths) -> int:
    """Code what developers wrote about the break, item by item."""
    import random
    import time

    from mcp_migration.report.qualitative import (
        count_issues,
        count_pins,
        issue_evidence,
        pin_evidence,
    )

    episodes = [e for e in read_jsonl(paths.episodes) if e["kind"] == "pin_below_2"]
    pin_commits: list[dict[str, Any]] = []
    by_repo: dict[str, list[dict[str, Any]]] = {}
    for episode in episodes:
        by_repo.setdefault(str(episode["repo"]), []).append(episode)
    for repo, group in by_repo.items():
        commits_file = paths.commits_dir / f"{repo.replace('/', '__')}.jsonl"
        if not commits_file.exists():
            continue
        commits = {c["sha"]: c for c in read_jsonl(commits_file)}
        for episode in group:
            for sha in episode["shas"]:
                record = commits.get(sha)
                if record and "pin" in str(record.get("message", "")).lower():
                    pin_commits.append(
                        {
                            "repo": repo,
                            "sha": sha,
                            "subject": str(record["message"]).splitlines()[0],
                        }
                    )

    hits: list[dict[str, Any]] = []
    for name in ("issue_no_back_channel", "issue_mcperror_import", "issue_fastmcp_import_error"):
        path = paths.search_dir / f"{name}.jsonl"
        if path.exists():
            hits.extend(read_jsonl(path))
    tokens = github_tokens()
    issues: list[dict[str, Any]] = []
    if hits and tokens:
        client = gh.GitHubClient(tokens)
        for hit in random.Random(args.seed).sample(hits, min(args.issues, len(hits))):
            try:
                data = client.get(f"/repos/{hit['repo']}/issues/{hit['id']}", {})
            except Exception as exc:  # pragma: no cover - network
                log.warning("issue %s#%s: %s", hit["repo"], hit["id"], exc)
                continue
            issues.append(
                {
                    "repo": hit["repo"],
                    "number": hit["id"],
                    "title": data.get("title") or "",
                    "body": data.get("body") or "",
                    "state": data.get("state"),
                    "comments": data.get("comments") or 0,
                }
            )
            time.sleep(0.1)
    elif not tokens:
        log.warning("no token; coding pin messages only")

    # The coded items are the record; the counts are read back off them, so a
    # percentage in the paper and a row in the explorer cannot disagree.
    pins, coded_issues = pin_evidence(pin_commits), issue_evidence(issues)
    report: dict[str, Any] = {
        "pins": count_pins(pins),
        "issues": count_issues(coded_issues),
        "evidence": {"pins": pins, "issues": coded_issues},
    }
    write_json(args.out, report)
    log.info("pins: %s", report["pins"])
    log.info("issues: n=%d themes coded", report["issues"]["n"])
    return 0


def cmd_export_viz(args: argparse.Namespace, paths: Paths) -> int:
    """Write the two payloads the web explorer reads."""
    from mcp_migration.report import viz

    if not paths.episodes.exists():
        log.error("no episodes at %s; run `episodes` first", paths.episodes)
        return 1
    episodes = list(read_jsonl(paths.episodes))
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    rows = out / "episodes.json"
    rows.write_text(
        viz.dumps(viz.episode_rows(episodes, paths.commits_dir, paths.repos)),
        encoding="utf-8",
    )
    log.info("wrote %s (%.0f KB)", rows, rows.stat().st_size / 1024)

    if paths.qualitative.exists():
        coded = out / "qualitative.json"
        coded.write_text(
            viz.dumps(viz.qualitative_payload(read_json(paths.qualitative))), encoding="utf-8"
        )
        log.info("wrote %s (%.0f KB)", coded, coded.stat().st_size / 1024)
    else:
        log.warning("no coded text at %s; run `qualitative` first", paths.qualitative)

    page = out / "index.html"
    if page.exists():
        page.write_text(
            viz.inline_summary(page.read_text(encoding="utf-8"), viz.summary(episodes)),
            encoding="utf-8",
        )
        log.info("inlined the summary into %s", page)
    else:
        log.warning("%s not found; wrote the episode list only", page)
    return 0


def cmd_figures(args: argparse.Namespace, paths: Paths) -> int:
    """Regenerate the manuscript's figures from the episode records."""
    try:
        from mcp_migration.report import figures
    except ImportError:  # pragma: no cover - depends on the optional extra
        log.error("figures need matplotlib: pip install -e '.[paper]'")
        return 1
    if not paths.episodes.exists():
        log.error("no episodes at %s; run `episodes` first", paths.episodes)
        return 1
    from mcp_migration.report.lag import corpus_seeds, load_lag

    episodes = list(read_jsonl(paths.episodes))
    lag = load_lag(paths, corpus_seeds(paths))
    # The revised measurements are expensive to recompute, so read them from
    # the summary `stats` writes; run `stats` first.
    summary_file = args.summary or paths.root / "processed" / "summary.json"
    measurement = read_json(summary_file).get("measurement") if summary_file.exists() else None
    if measurement is None:
        log.warning(
            "no measurements in %s; the deferral and support figures are skipped", summary_file
        )
    for path in figures.build_all(episodes, args.out_dir, lag, measurement):
        log.info("figure: %s", path)
    return 0


def cmd_labels(args: argparse.Namespace, paths: Paths) -> int:
    """Report detector precision and inter-rater agreement from coded sheets."""
    from mcp_migration.report.sampling import AGREEMENT_COLUMNS

    if len(args.sheets) > 2:
        log.error("give one sheet per coder, at most two; got %d", len(args.sheets))
        return 1
    sheets = [read_sheet(path) for path in args.sheets]
    adjudicated = read_sheet(args.adjudicated) if args.adjudicated else None
    named = list(zip(args.sheets, sheets, strict=True))
    if adjudicated is not None:
        named.append((args.adjudicated, adjudicated))
    problems = [f"{path}: {p}" for path, rows in named for p in invalid_rows(rows)]
    if problems:
        for p in problems:
            log.error("invalid label: %s", p)
        return 1
    episodes = (
        {str(e["episode_id"]): e for e in read_jsonl(paths.episodes)}
        if paths.episodes.exists()
        else {}
    )
    # Precision needs one verdict per episode. Taking the first sheet would make
    # the figure depend on argument order and ignore the second coder.
    split: list[str] = []
    if adjudicated is not None:
        judged, source = adjudicated, f"adjudicated ({args.adjudicated})"
    elif len(sheets) == 2:
        judged, split = consensus(sheets[0], sheets[1])
        source = "both coders agree; splits count as no"
    else:
        judged, source = sheets[0], f"single coder ({args.sheets[0]})"
    n_labeled = sum(1 for r in judged if str(r.get("detector_correct", "")).strip())
    weighted = weighted_precision(judged, episodes)
    overall = float(weighted.get("precision", precision(judged)))
    by_kind = precision_by_signal(judged, episodes)
    details: dict[str, dict[str, float | int]] = {}
    if len(sheets) == 2:
        details = agreement_details(sheets[0], sheets[1], AGREEMENT_COLUMNS)
    else:
        log.warning("only one sheet given; inter-rater agreement needs two")
    kappa = {column: float(d["kappa"]) for column, d in details.items()}

    # Assisted labeling: the coders verified LLM pre-labels. Their agreement with
    # each other then measures shared acceptance, so report what they changed,
    # and use the blind subset (coded without suggestions) for independence.
    blind_ids = (
        [ln.strip() for ln in args.blind.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if args.blind
        else []
    )
    assisted: dict[str, Any] = {}
    if args.prelabels:
        pre = read_sheet(args.prelabels)
        reference = judged if adjudicated is not None or len(sheets) < 2 else consensus(*sheets)[0]
        assisted = {
            "prelabels": str(args.prelabels),
            "uptake": {
                str(path): prelabel_uptake(rows, pre, AGREEMENT_COLUMNS, skip=blind_ids)
                for path, rows in zip(args.sheets, sheets, strict=True)
            },
            # Against the blind subset where there is one: elsewhere the
            # reference was itself shaped by the suggestion it is compared with.
            "llm_vs_reference_rows": "blind" if blind_ids else "all (anchored)",
            "llm_vs_reference": agreement_details(
                only(reference, blind_ids) if blind_ids else reference,
                pre,
                AGREEMENT_COLUMNS,
            ),
        }
    blind: dict[str, Any] = {}
    if blind_ids and len(sheets) == 2:
        blind = {
            "n": len(blind_ids),
            "agreement": agreement_details(
                only(sheets[0], blind_ids), only(sheets[1], blind_ids), AGREEMENT_COLUMNS
            ),
        }

    write_json(
        args.out,
        {
            "n_labeled": n_labeled,
            "precision_overall": overall,
            # Weighted by stratum size; the unweighted share is kept to show
            # how far equal allocation would have moved it.
            "precision_ci": [weighted.get("ci_low"), weighted.get("ci_high")],
            "precision_unweighted": precision(judged),
            "coverage": weighted.get("coverage"),
            "precision_by_kind": by_kind,
            "precision_source": source,
            "split_episodes": split,
            "kappa": kappa,
            "agreement": details,
            "assisted": assisted,
            "blind": blind,
        },
    )
    for path, u in assisted.get("uptake", {}).items():
        log.info(
            "  %s kept %.1f%% of LLM pre-labels (%d of %d cells changed, %d episodes)",
            path,
            100 * u["accepted"],
            u["cells_changed"],
            u["cells"],
            u["episodes_changed"],
        )
    for column, d in assisted.get("llm_vs_reference", {}).items():
        log.info(
            "  llm vs reference [%s] %-14s kappa=%.3f n=%d",
            assisted["llm_vs_reference_rows"],
            column,
            d["kappa"],
            d["n"],
        )
    for column, d in blind.get("agreement", {}).items():
        log.info("  blind kappa %-22s %.3f  n=%d", column, d["kappa"], d["n"])
    log.info("precision from: %s", source)
    if split:
        log.info("coders split on %d episodes (counted as no until adjudicated)", len(split))
    log.info(
        "n_labeled=%d precision=%.3f [%.3f, %.3f]",
        n_labeled,
        overall,
        weighted.get("ci_low", 0.0),
        weighted.get("ci_high", 0.0),
    )
    for kind, stats_row in by_kind.items():
        log.info(
            "  %-16s n=%-4s precision=%.3f [%.3f, %.3f]",
            kind,
            stats_row["n"],
            stats_row["precision"],
            stats_row["ci_low"],
            stats_row["ci_high"],
        )
    for column, d in details.items():
        log.info(
            "  kappa %-22s %.3f  agreement=%.3f  n=%d", column, d["kappa"], d["agreement"], d["n"]
        )
    return 0


def cmd_label(args: argparse.Namespace, paths: Paths) -> int:
    """Serve the labeling app for one coder's sheet."""
    import shutil
    import threading
    import webbrowser

    try:
        import uvicorn

        from mcp_migration.labeling.app import create_app
        from mcp_migration.labeling.context import GitHubFetcher
    except ImportError:  # pragma: no cover - depends on the optional extra
        log.error("the labeling app needs FastAPI: pip install -e '.[label]'")
        return 1
    if not paths.episodes.exists():
        log.error("no episodes at %s; run scripts/reproduce.sh first", paths.episodes)
        return 1
    sheet = args.sheet or paths.coder_sheet(args.coder, args.round)
    if not sheet.exists():
        if args.sheet:
            log.error("no sheet at %s", sheet)
            return 1
        # Each coder starts from a copy of the shared draw, never the draw itself.
        sheet.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paths.labeling_sheet(args.round), sheet)
        log.info("started %s from the blank sheet", sheet)

    fetcher = None if args.offline else GitHubFetcher(paths.github_cache, github_token())
    if fetcher is not None and not fetcher.authenticated:
        log.warning("no GITHUB_TOKEN: GitHub allows 60 commit lookups an hour without one")
    app = create_app(paths, sheet, args.coder, Path("docs/codebook.md"), fetcher)
    url = f"http://{args.host}:{args.port}/"
    log.info("coder %s, sheet %s: open %s", args.coder, sheet, url)
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_stats(args: argparse.Namespace, paths: Paths) -> int:
    loaded = load_all(paths)
    cap_kb = getattr(args, "max_disk_kb", None) or DEFAULT_MAX_DISK_KB
    summary = summarize(
        loaded["episodes"],
        loaded["heads"],
        n_search_hits=loaded["n_search_hits"],
        n_seed_repos=loaded["n_seed_repos"],
        n_signal_commits=loaded["n_signal_commits"],
        n_resolved_repos=loaded["n_resolved_repos"],
        n_clone_candidates=loaded["n_clone_candidates"],
        exclusions=loaded["exclusions"],
        gap_rows=loaded["gap_sensitivity"],
        label_report=loaded["labels"],
        qualitative=loaded["qualitative"],
        repo_sizes=loaded["repo_sizes"],
        exclusion=exclusion_profile(loaded["metadata"], loaded["repo_queries"], cap_kb),
        cap_kb=cap_kb,
        lag=loaded["lag"],
        lag_all=loaded["lag_all"],
        lag_repos=loaded["lag_repos"],
        harvest_outcomes=loaded["harvest_outcomes"],
        n_search_queries=loaded["n_search_queries"],
        n_sample_size=loaded["n_sample_size"],
        measurement=loaded["measurement"],
        coded_pins=loaded["coded_pins"],
    )
    write_json(args.out, summary)
    log.info("wrote summary to %s", args.out)
    if args.tex:
        args.tex.parent.mkdir(parents=True, exist_ok=True)
        args.tex.write_text(to_latex(summary), encoding="utf-8")
        log.info("wrote LaTeX macros to %s", args.tex)
    for key in ("n_mined_repos", "n_signal_commits", "n_episodes"):
        log.info("%s = %s", key, summary[key])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-migration", description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    cpus = os.cpu_count() or 2

    p = sub.add_parser("search", help="run GitHub searches from a TOML query file")
    p.add_argument("--queries", type=Path, default=DEFAULT_QUERIES_FILE)
    p.add_argument("--until", type=date.fromisoformat, help="YYYY-MM-DD (default: today)")
    p.add_argument("--only", nargs="+", metavar="NAME", help="run only these query names")
    p.add_argument("--force", action="store_true", help="re-run queries already collected")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("seed", help="merge repository lists into one deduplicated list")
    p.add_argument("sources", nargs="*", type=Path, help="files containing GitHub URLs or slugs")
    p.add_argument("--include-search-hits", action="store_true")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("metadata", help="fetch repository metadata via GraphQL")
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--flush-every", type=int, default=1000, help="records between flushes")
    p.add_argument("--force", action="store_true", help="refetch repositories already fetched")
    p.set_defaults(func=cmd_metadata)

    p = sub.add_parser("filter", help="apply inclusion criteria before cloning")
    p.add_argument("--pushed-after", type=date.fromisoformat, default=DEFAULT_PUSHED_AFTER)
    p.add_argument("--max-disk-kb", type=int, default=DEFAULT_MAX_DISK_KB)
    p.add_argument("--include-archived", action="store_true")
    p.add_argument("--include-forks", action="store_true")
    p.add_argument(
        "--include-upstream",
        action="store_true",
        help="keep the official SDK/spec repos, which define rather than undergo the change",
    )
    p.set_defaults(func=cmd_filter)

    p = sub.add_parser("clone", help="clone repositories that passed the filter")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--workers", type=int, default=8)
    p.set_defaults(func=cmd_clone)

    p = sub.add_parser("mine", help="extract signal commits and HEAD state")
    p.add_argument("--since", type=date.fromisoformat, default=DEFAULT_SINCE)
    p.add_argument("--workers", type=int, default=max(1, cpus - 1))
    p.add_argument("--force", action="store_true", help="re-mine already mined repositories")
    p.set_defaults(func=cmd_mine)

    p = sub.add_parser(
        "harvest",
        help="clone, mine and delete each repository in turn (bounded disk)",
    )
    p.add_argument("--since", type=date.fromisoformat, default=DEFAULT_SINCE)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--force", action="store_true", help="re-harvest already mined repositories")
    p.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="SECONDS",
        help="abandon a repository that takes longer than this to mine, and record a timeout",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="keep each clone after mining; re-mining otherwise re-downloads everything",
    )
    p.add_argument(
        "--prefilter",
        action="store_true",
        help="let `git log -G` pick the commits worth diffing; same records, far less time",
    )
    p.set_defaults(func=cmd_harvest)

    p = sub.add_parser(
        "backfill-parents",
        help="fill parent_sha into older commit records, before clones are deleted",
    )
    p.add_argument("--workers", type=int, default=8)
    p.set_defaults(func=cmd_backfill_parents)

    p = sub.add_parser("episodes", help="group signal commits into episodes")
    p.add_argument("--max-gap-days", type=int, default=DEFAULT_MAX_GAP_DAYS)
    p.set_defaults(func=cmd_episodes)

    p = sub.add_parser("sample", help="write a stratified labeling sheet")
    p.add_argument("--per-stratum", type=int, default=15)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--kinds", nargs="+", help="restrict to episode kinds, e.g. migration")
    p.add_argument(
        "--exclude", nargs="+", type=Path, help="labeled sheets whose episodes not to draw again"
    )
    p.add_argument("--round", type=int, default=1, help="labeling round the sheet is for")
    p.add_argument(
        "--out", type=Path, help="write here instead of data/labeling/round<N>/sample.csv"
    )
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("qualitative", help="code developer text about the break")
    p.add_argument("--issues", type=int, default=70, help="issues to sample")
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--out", type=Path, default=Path("data/processed/qualitative.json"))
    p.set_defaults(func=cmd_qualitative)

    p = sub.add_parser("export-viz", help="write the web explorer's data files")
    p.add_argument("--out-dir", type=Path, default=Path("webapp"))
    p.set_defaults(func=cmd_export_viz)

    p = sub.add_parser("show", help="print one episode in full, for manual coding")
    p.add_argument("episode_id", help="e.g. owner/repo#0")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("figures", help="regenerate the manuscript's figures")
    p.add_argument("--out-dir", type=Path, default=Path("results/figures"))
    p.add_argument(
        "--summary",
        type=Path,
        help="the summary `stats` wrote (default data/processed/summary.json)",
    )
    p.set_defaults(func=cmd_figures)

    p = sub.add_parser("labels", help="precision and inter-rater agreement from coded sheets")
    p.add_argument("sheets", nargs="+", type=Path, help="one coded CSV per coder (one or two)")
    p.add_argument(
        "--adjudicated",
        type=Path,
        help="the sheet with disagreements resolved; precision is computed from it",
    )
    p.add_argument(
        "--prelabels",
        type=Path,
        help="the LLM pre-labelled sheet the coders verified; reports what they changed",
    )
    p.add_argument(
        "--blind",
        type=Path,
        help="episode ids (one per line) the coders labeled without seeing pre-labels",
    )
    p.add_argument("--out", type=Path, default=Path("data/processed/label_report.json"))
    p.set_defaults(func=cmd_labels)

    p = sub.add_parser("label", help="code the labeling sheet in a local web app")
    p.add_argument(
        "--coder", required=True, help="coder letter; codes data/labeling/round<N>/coder<X>.csv"
    )
    p.add_argument("--round", type=int, default=1, help="labeling round to code")
    p.add_argument("--sheet", type=Path, help="code this CSV instead of the coder's own copy")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--offline", action="store_true", help="never contact GitHub")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    p.set_defaults(func=cmd_label)

    p = sub.add_parser("stats", help="summarize the dataset for the manuscript")
    p.add_argument(
        "--max-disk-kb",
        type=int,
        default=DEFAULT_MAX_DISK_KB,
        help="the cap the manuscript reports; characterises what it excludes",
    )
    p.add_argument("--out", type=Path, default=Path("data/processed/summary.json"))
    p.add_argument(
        "--tex",
        type=Path,
        default=Path("results/numbers.tex"),
        help="LaTeX macro file consumed by paper/main.tex",
    )
    p.set_defaults(func=cmd_stats)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # One INFO line per commit visited, across 192k commits, is noise that also
    # slows a large mine measurably.
    logging.getLogger("pydriller").setLevel(logging.WARNING)
    result: int = args.func(args, Paths(args.data_dir))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
