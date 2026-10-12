"""Coding of developer-written text."""

import json

from mcp_migration.report.qualitative import (
    code_issues,
    code_pin_messages,
    count_issues,
    count_pins,
    issue_evidence,
    pin_evidence,
)


def test_breakage_wording_is_counted_separately_from_intent() -> None:
    counts = code_pin_messages(
        [
            "fix(deps): pin mcp<2, fresh installs crash on start",
            "chore: pin mcp<2 until we migrate to FastMCP 2",
            "chore(deps): pin mcp<2",
        ]
    )
    assert counts == {"n": 3, "breakage": 1, "temporary": 1}


def test_unpin_and_repin_are_not_promises_to_return() -> None:
    # `unpinned` contains `pin`, and loose intent patterns match the surrounding
    # wording; these describe the mechanism, not a plan to come back.
    counts = code_pin_messages(
        [
            "fix(deps): pin mcp<2, unpinned installs break",
            "revert: re-pin mcp below 2",
            "chore: unpin mcp now that we are on 2",
        ]
    )
    assert counts["temporary"] == 0


def test_issue_themes_are_counted_per_issue_not_per_match() -> None:
    counts = code_issues(
        [
            {
                "title": "ModuleNotFoundError: No module named 'mcp.server.fastmcp'",
                "body": "Fresh install fails to start. Please pin mcp<2; "
                "the manifest has no upper bound.",
                "state": "closed",
            },
            {"title": "Docs typo", "body": "", "state": "open"},
        ]
    )
    assert counts["n"] == 2
    assert counts["closed"] == 1
    assert counts["names_rename"] == 1
    assert counts["import_error"] == 1
    assert counts["proposes_pin"] == 1
    assert counts["fresh_install"] == 1
    assert counts["no_upper_bound"] == 1


def test_missing_fields_do_not_raise() -> None:
    assert code_issues([{}])["n"] == 1
    assert code_pin_messages([])["n"] == 0


def test_offsets_point_at_the_word_that_fired() -> None:
    """A subject can contain the matched word twice; only one is the match."""
    subject = "Race-week audit fixes: pin mcp<2, fix runbook hostnames"
    [item] = pin_evidence([{"repo": "a/b", "sha": "0" * 40, "subject": subject}])
    at, length = item["hits"]["breakage"]
    assert subject[at : at + length] == "fix"
    # Not the "fix" inside "fixes", which is where a substring search would land.
    assert at == subject.index("fix runbook")


def test_spans_quote_the_source_rather_than_reflowing_it() -> None:
    """Punctuation sits against the identifier in the source; keep it there."""
    body = "The import\n\n    from `mcp.server.fastmcp` import FastMCP\n\nnow raises."
    [item] = issue_evidence([{"repo": "a/b", "number": 1, "title": "Broken", "body": body}])
    span = item["spans"]["names_rename"]
    assert "`mcp.server.fastmcp`" in span["text"]
    assert span["text"][span["at"] : span["at"] + span["len"]] == "mcp.server.fastmcp"


def test_only_a_bounded_window_around_each_match_is_stored() -> None:
    """The stored record is evidence for the coding, not a copy of the issue.

    A short issue does end up inside its own span -- the window is what is
    bounded, not the share of the issue it covers.
    """
    far = "paragraph the coder never looked at"
    body = "pin mcp<2. " + "filler word " * 40 + far
    [item] = issue_evidence([{"repo": "a/b", "number": 1, "title": "t", "body": body}])
    assert "body" not in item
    assert far not in json.dumps(item)
    # `src` points into a body nobody can see, so it is not published either.
    assert all("src" not in span for span in item["spans"].values())


def test_counts_are_read_back_off_the_published_records() -> None:
    """If these could drift, the paper and the explorer could disagree."""
    commits = [
        {"repo": "a/b", "sha": "1" * 40, "subject": "fix: pin mcp<2, installs crash"},
        {"repo": "a/c", "sha": "2" * 40, "subject": "chore: pin mcp<2 until we migrate"},
        {"repo": "a/d", "sha": "3" * 40, "subject": "chore: pin mcp<2"},
    ]
    evidence = pin_evidence(commits)
    assert count_pins(evidence) == code_pin_messages(c["subject"] for c in commits)

    issues = [
        {"repo": "a/b", "number": 1, "title": "ModuleNotFoundError", "body": "", "state": "closed"},
        {"repo": "a/c", "number": 2, "title": "docs", "body": "", "state": "open"},
    ]
    assert count_issues(issue_evidence(issues)) == code_issues(issues)
