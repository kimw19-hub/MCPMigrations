"""Coding of developer-written text around the breaking change.

Two sources, both public. Commit subjects from ``pin_below_2`` episodes say why
a project pinned; issues matching the breakage queries say how the break was
discovered and what was proposed. Both are coded here by pattern so the figures
the paper cites are regenerated rather than tallied by hand.

Every coded item is kept next to its counts: the identifier, enough to build a
link to the source on GitHub, and the text that made each code fire. A
percentage alone cannot be checked; an itemised list with its evidence can, so
the explorer publishes one.

What is stored of an issue is a bounded window either side of each match rather
than the body, so the linked issue stays the canonical copy. The bound is on
the window, not on the share of the issue it covers: a short issue can sit
inside its own span.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# What a maintainer says when the pin is a repair rather than a plan.
BREAKAGE = re.compile(
    r"\b(broke|broken|break|breaking|crash|fail|failing|failure|fix|hotfix|"
    r"restore|blocker|regress|error|unable)\b",
    re.I,
)
# Wording that frames the pin as something to undo later. `unpin` and `repin`
# are excluded: they describe the mechanism, not an intention to return, and
# matching them overstates this category roughly eightfold.
TEMPORARY = re.compile(
    r"\b(temporar\w*|for now|until|revisit\w*|interim|short[- ]term|eventually|"
    r"once we|when we|plan to|intend\w*)\b",
    re.I,
)
_UNPIN = re.compile(r"\b(un|re)-?pinn?\w*\b", re.I)

PIN_CODES = ("breakage", "temporary")

ISSUE_THEMES = {
    "names_rename": re.compile(r"FastMCP|MCPServer|mcp\.server\.fastmcp", re.I),
    "import_error": re.compile(r"ModuleNotFoundError|No module named", re.I),
    "proposes_pin": re.compile(r"mcp\s*<\s*2|<\s*2\.0|upper bound|\bpin\b|\bcap\b", re.I),
    "fresh_install": re.compile(
        r"fresh install|clean environment|fails? to start|cannot start|"
        r"won'?t start|on install|uvx|pip install",
        re.I,
    ),
    "no_upper_bound": re.compile(
        r"no upper bound|unbounded|no ceiling|unpinned|missing upper", re.I
    ),
}

_CONTEXT = 80  # characters of context either side of a match
_WS = re.compile(r"\s+")


def _span(text: str, match: re.Match[str]) -> dict[str, Any]:
    """The matched text with a little either side, whitespace collapsed.

    Enough to judge whether the pattern caught what it was aiming at, and not
    so much that the store becomes a copy of the issue.

    ``at``/``len`` locate the trigger inside ``text``, and ``src`` locates it in
    the text that was searched. Offsets rather than the literal, because a
    subject like "audit fixes: ... fix runbook" contains the matched word twice
    and searching for it again would highlight the wrong one.
    """
    start = max(0, match.start() - _CONTEXT)
    end = min(len(text), match.end() + _CONTEXT)
    left = _WS.sub(" ", text[start : match.start()])
    hit = _WS.sub(" ", match.group(0))
    right = _WS.sub(" ", text[match.end() : end])
    # The window cuts by character, so drop whatever word it cut in half.
    if start > 0 and left and not left[0].isspace():
        left = left.partition(" ")[2]
    if end < len(text) and right and not right[-1].isspace():
        right = right.rpartition(" ")[0]
    left, right = left.lstrip(), right.rstrip()
    lead = "... " if start > 0 else ""
    trail = " ..." if end < len(text) else ""
    return {
        "text": lead + left + hit + right + trail,
        "at": len(lead) + len(left),
        "len": len(hit),
        "src": match.start(),
    }


def code_pin(subject: str) -> dict[str, dict[str, Any]]:
    """Codes that fire on one pin commit subject, each with its evidence."""
    found: dict[str, dict[str, Any]] = {}
    if match := BREAKAGE.search(subject):
        found["breakage"] = _span(subject, match)
    # Blank out unpin/repin before asking whether the text promises a return,
    # padding to length so the offsets still point into the original subject.
    masked = _UNPIN.sub(lambda m: " " * len(m.group(0)), subject)
    if match := TEMPORARY.search(masked):
        found["temporary"] = _span(subject, match)
    return found


def code_issue(title: str, body: str) -> dict[str, dict[str, Any]]:
    """Themes present in one issue report, each with its evidence."""
    text = f"{title}\n{body}"
    found: dict[str, dict[str, Any]] = {}
    for name, pattern in ISSUE_THEMES.items():
        if match := pattern.search(text):
            found[name] = _span(text, match)
    return found


def pin_evidence(commits: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One record per pin commit: where it is, what it says, why it counted.

    No spans here and no URL: the whole subject is one line and it is stored,
    so a span of it would repeat the record, and the commit link follows from
    the repository and the hash. ``hits`` is kept, since which word fired is
    not recoverable from the subject alone.
    """
    out = []
    for commit in commits:
        subject = str(commit["subject"])
        coded = code_pin(subject)
        out.append(
            {
                "repo": str(commit["repo"]),
                "sha": str(commit["sha"])[:10],
                "subject": subject,
                "codes": sorted(coded),
                "hits": {code: [span["src"], span["len"]] for code, span in coded.items()},
            }
        )
    return out


def issue_evidence(issues: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One record per coded issue.

    Unlike a pin commit, the coding cannot be checked against the identifier
    alone, since the body it read is not stored -- hence ``spans``, a window
    around each match.
    """
    out = []
    for issue in issues:
        coded = code_issue(str(issue.get("title") or ""), str(issue.get("body") or ""))
        # `src` points into a body that is not stored, so it would mean nothing
        # to a reader of this file.
        spans = {c: {k: v for k, v in s.items() if k != "src"} for c, s in coded.items()}
        out.append(
            {
                "repo": str(issue["repo"]),
                "number": issue["number"],
                "title": str(issue.get("title") or ""),
                "state": str(issue.get("state") or ""),
                "comments": int(issue.get("comments") or 0),
                "codes": sorted(spans),
                "spans": spans,
            }
        )
    return out


def count_pins(evidence: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Tally the coded pin commits. Counted from the published records, so the
    paper's percentages and the explorer's rows cannot disagree."""
    counts = {"n": len(evidence), **dict.fromkeys(PIN_CODES, 0)}
    for item in evidence:
        for code in item["codes"]:
            counts[code] += 1
    return counts


def count_issues(evidence: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Tally the coded issues, per the note on :func:`count_pins`."""
    counts = {"n": len(evidence), "closed": 0, **dict.fromkeys(ISSUE_THEMES, 0)}
    for item in evidence:
        if item.get("state") == "closed":
            counts["closed"] += 1
        for code in item["codes"]:
            counts[code] += 1
    return counts


def code_pin_messages(messages: Iterable[str]) -> dict[str, int]:
    """Count what pin commit subjects say, given only the subjects."""
    return count_pins([{"codes": code_pin(m)} for m in messages])


def code_issues(issues: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count the themes present in breakage issue reports."""
    return count_issues(
        [
            {
                "state": issue.get("state"),
                "codes": code_issue(str(issue.get("title") or ""), str(issue.get("body") or "")),
            }
            for issue in issues
        ]
    )
