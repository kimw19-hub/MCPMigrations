"""Lexical signals for MCP SDK and protocol-revision migrations.

Every pattern is a heuristic: measure precision on a labeled sample before
reporting counts. To add a signal, add it to ``SIGNAL_PATTERNS``, add it to a
group below if classification should use it, and add a case to
``tests/test_signals.py``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import PurePosixPath

PROTOCOL_REVISIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28")
REVISION_PREFIX = "revision_literal:"
_REVISION_RX = re.compile(r"\b(" + "|".join(map(re.escape, PROTOCOL_REVISIONS)) + r")\b")

SIGNAL_PATTERNS: dict[str, str] = {
    # Python SDK generation (identifiers from the official v1 -> v2 migration guide).
    "py_v1_fastmcp_import": r"\bmcp\.server\.fastmcp\b",
    "py_v2_mcpserver_import": r"\bmcp\.server\.mcpserver\b",
    "py_v1_McpError": r"\bMcpError\b",
    "py_v2_MCPError": r"\bMCPError\b",
    "py_v1_streamablehttp_client": r"\bstreamablehttp_client\b",
    "py_v2_httpx2": r"\bhttpx2\b",
    "py_pin_mcp_v2": r"(?<![\w.-])mcp(\[[^\]]*\])?\s*(>=|==|~=)\s*2(?:\.\d+)*\b",
    "py_pin_mcp_below_2": r"(?<![\w.-])mcp(\[[^\]]*\])?\s*[^\"'\n]*<\s*2(?![\d.]*\d)",
    "py_standalone_fastmcp": r"^\s*(from|import)\s+fastmcp\b|(?<![\w.-])fastmcp\s*[<>=~]",
    # TypeScript SDK generation. The lookahead excludes reference servers such as
    # @modelcontextprotocol/server-filesystem.
    "ts_v1_sdk_pkg": r"@modelcontextprotocol/sdk(?![\w-])",
    "ts_v2_split_pkg": r"@modelcontextprotocol/(server|client)(?![\w-])",
    # Protocol-era markers.
    # Bounded so workflow names and URLs such as "acme/mcp-server/discover" or
    # ".../authorization-server/discovery" do not count.
    "modern_server_discover": r"(?<![\w/.-])server/discover(?![\w/-])",
    "modern_subscriptions_listen": r"subscriptions/listen",
    "modern_input_required": r"\bInputRequiredResult\b",
    "modern_version_const": r"\bLATEST_MODERN_VERSION\b|\bMODERN_PROTOCOL_VERSIONS\b",
    "legacy_session_header": r"(?i)\bmcp-session-id\b",
    "legacy_initialized_notif": r"notifications/initialized",
    "legacy_session_id_generator": r"\bsessionIdGenerator\b",  # TS v1 option; verify
    "legacy_sse_transport": (
        r"\bSSEServerTransport\b|\bSseServerTransport\b|transport\s*=\s*[\"']sse[\"']"
    ),
    # v1 already had a stateless HTTP mode; this is NOT a 2026-07-28 marker.
    "v1_stateless_flag": r"\bstateless_http\b|\bstateless\s*=\s*True",
    # Features deprecated in 2026-07-28 (SEP-2577, SEP-2575).
    "dep_sampling": r"sampling/createMessage|\bcreate_message\s*\(|\bcreateMessage\s*\(",
    "dep_roots": r"roots/list|\blist_roots\s*\(|\blistRoots\s*\(",
    "dep_logging": r"logging/setLevel|\bsend_log_message\s*\(|\bsendLoggingMessage\s*\(",
    "dep_resource_subscribe": r"resources/subscribe|\bsubscribe_resource\b",
    "dep_warning_handled": r"\bMCPDeprecationWarning\b|\bNoBackChannelError\b",
    # TypeScript v2 era serving. Unlike Python's v2, whose server always answers
    # both eras, a TypeScript v2 server built from the v1-style classes keeps
    # speaking only the 2025 protocol; 2026-07-28 needs one of these entry
    # points, and legacy traffic is refused only on request. Deliberately in no
    # group below, so adding them changes no episode.
    "ts_v2_modern_entry": r"\bcreateMcpHandler\b|\bserveStdio\b",
    "ts_legacy_reject": r"\blegacy\s*:\s*[\"']reject[\"']",
    # The two halves of ts_v2_split_pkg, kept apart because only a server can
    # serve a protocol revision; a client-only repository is not at risk.
    "ts_v2_server_pkg": r"@modelcontextprotocol/server(?![\w-])",
    "ts_v2_client_pkg": r"@modelcontextprotocol/client(?![\w-])",
    # Feature use through SDK helpers, not only the protocol strings: the
    # TypeScript inputRequired(...) builder and the Python v2 elicitation type
    # and subscription bus (client-side listen/listChanged are not server use).
    "ts_v2_input_required": r"\binputRequired(?:\.\w+)?\s*\(",
    "py_v2_elicit": r"\bElicit\[",
    "py_v2_subscription_bus": r"\bSubscriptionBus\b",
    # Legacy serving that survives legacy:'reject' on another handler: the
    # documented routing helpers, and v1-style transports constructed on v2.
    "ts_legacy_route": r"\bisLegacyRequest\b|\blegacyStatelessFallback\b",
    # Not when the transport is handed to serveStdio as its `transport:` option,
    # which serves both eras through the new entry point.
    "ts_v1_transport_class": (
        r"^(?!.*\btransport\s*:).*\bnew\s+"
        r"(?:StreamableHTTPServerTransport|StdioServerTransport|SSEServerTransport)\b"
    ),
}

SIGNALS: dict[str, re.Pattern[str]] = {k: re.compile(v) for k, v in SIGNAL_PATTERNS.items()}

# Groups used by classification and episode building.
V1_SDK_KEYS = ("py_v1_fastmcp_import", "ts_v1_sdk_pkg")
V2_SDK_KEYS = ("py_v2_mcpserver_import", "ts_v2_split_pkg", "py_pin_mcp_v2")
MODERN_FEATURE_KEYS = (
    "modern_server_discover",
    "modern_subscriptions_listen",
    "modern_input_required",
)
V2_ADDED_KEYS = (*V2_SDK_KEYS, "py_v2_MCPError", *MODERN_FEATURE_KEYS)
# What an episode's kind may rest on. MCPError still makes a commit related,
# so grouping and episode ids are unchanged, but alone it proves nothing:
# projects define their own MCPError class (Python, Go, Rust), and in the coded
# sample 33 of the 45 episodes resting on it alone were false.
V2_EVIDENCE_KEYS = tuple(k for k in V2_ADDED_KEYS if k != "py_v2_MCPError")
V1_REMOVED_KEYS = (
    *V1_SDK_KEYS,
    "py_v1_McpError",
    "py_v1_streamablehttp_client",
    "legacy_session_header",
    "legacy_session_id_generator",
)
PIN_BELOW_2_KEY = "py_pin_mcp_below_2"
DEPRECATED_KEYS = tuple(k for k in SIGNAL_PATTERNS if k.startswith("dep_") and "warning" not in k)

SOURCE_SUFFIXES = frozenset(
    {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".go", ".cs", ".java", ".kt", ".rs", ".csproj"}
)
MANIFEST_NAMES = frozenset({"pyproject.toml", "setup.py", "setup.cfg", "package.json", "go.mod"})


# Directories and files that hold tests, examples and documentation rather than
# the application: a marker there is not evidence of use.
NON_APP_PARTS = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "spec",
        "specs",
        "example",
        "examples",
        "demo",
        "demos",
        "docs",
        "doc",
        "fixtures",
        "e2e",
        "samples",
        "sample",
        "benchmark",
        "benchmarks",
        "release",
        "releases",
        "mocks",
        "__mocks__",
    }
)
# Test files by name, including test servers and fixtures named by token.
_NON_APP_FILE = re.compile(
    r"(^test_.*\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$|^conftest\.py$"
    r"|(^|[-_.])(test|tests|fixture|fixtures|mock|mocks)[-_.])"
)


def is_comment_line(line: str) -> bool:
    """A line that is only a comment, in Python or JavaScript/TypeScript."""
    stripped = line.lstrip()
    return stripped.startswith(("#", "//", "/*", "*"))


def is_application_path(path: str) -> bool:
    """A relevant path outside tests, examples and documentation."""
    p = PurePosixPath(path.replace("\\", "/"))
    if NON_APP_PARTS.intersection(part.lower() for part in p.parts[:-1]):
        return False
    return not _NON_APP_FILE.search(p.name)


SKIP_DIRS = frozenset(
    {"node_modules", ".git", "dist", "build", ".venv", "venv", "vendor", "__pycache__"}
)


def is_relevant_path(path: str | None) -> bool:
    """True for source files and dependency manifests outside vendored directories."""
    if not path:
        return False
    p = PurePosixPath(path.replace("\\", "/"))
    if SKIP_DIRS.intersection(p.parts):
        return False
    return (
        p.suffix in SOURCE_SUFFIXES
        or p.name in MANIFEST_NAMES
        or (p.name.startswith("requirements") and p.suffix in {".txt", ".in"})
    )


def count_signals(lines: Iterable[str]) -> Counter[str]:
    """Count lines matching each signal, plus protocol-revision literals."""
    counts: Counter[str] = Counter()
    for line in lines:
        for name, rx in SIGNALS.items():
            if rx.search(line):
                counts[name] += 1
        for revision in _REVISION_RX.findall(line):
            counts[REVISION_PREFIX + revision] += 1
    return counts


# Groups used to derive an episode's scope: whether it moved the SDK
# generation, the protocol era, or both.
# The pin and the standalone package are dependency-generation choices, not
# protocol ones: capping at mcp<2 is a statement about the SDK major, and
# depending on standalone FastMCP is a statement about which SDK is used at all.
SDK_GENERATION_KEYS = (
    *V1_SDK_KEYS,
    *V2_SDK_KEYS,
    PIN_BELOW_2_KEY,
    "py_v1_McpError",
    "py_v2_MCPError",
    "py_standalone_fastmcp",
)
PROTOCOL_ERA_KEYS = (
    *MODERN_FEATURE_KEYS,
    "modern_version_const",
    "legacy_session_header",
    "legacy_initialized_notif",
    "legacy_sse_transport",
)

# Structural replacements: a capability that disappeared in one episode
# alongside the mechanism that took over its job. Keyed by (removed, added), so
# the pairing has to occur within a single episode to count; a server that
# dropped sampling and adopted InputRequiredResult months apart is not evidence
# that one replaced the other. Only replacements with a signal on both sides are
# detectable: moving protocol logging to the language's own logger, or to an
# external model API, leaves nothing to match on.
REPLACEMENT_PAIRS = {
    ("dep_sampling", "modern_input_required"): "mrtr_input_required",
    ("dep_resource_subscribe", "modern_subscriptions_listen"): "subscriptions_listen",
    ("legacy_initialized_notif", "modern_server_discover"): "server_discover",
}


# A coarse pattern git can evaluate itself, to find the commits worth handing
# to the miner. Mining costs about a second per commit because pydriller builds
# a full diff for each, and in the largest repositories almost none of those
# commits can match: llvm and Firefox are C++, and the 900s budget ran out on
# them with no signal found. `git log -G` walks the same history in C.
#
# It must be a strict superset of SIGNAL_PATTERNS and the revision literals --
# every line any signal matches must also match here -- or the prefilter
# silently drops commits the full walk would have kept. Keywords are therefore
# loose and case-insensitive, and tests/mine/test_signals.py holds an example
# for every alternative of every signal. Adding a signal means adding a keyword
# here if none already covers it, and an example there.
PREFILTER_KEYWORDS = (
    "mcp",  # every SDK import, pin, error class and session header
    "modelcontextprotocol",
    "streamablehttp_client",
    "httpx2",
    "server/discover",
    "subscri",  # subscriptions/listen, resources/subscribe, subscribe_resource
    "inputrequired",  # InputRequiredResult and the inputRequired(...) builder
    "elicit",
    "modern_",
    "notifications/initialized",
    "sessionidgenerator",
    "transport",
    "stateless",
    "create_?message",
    "roots",
    "logging/setlevel",
    "send_?log(ging)?_?message",
    "nobackchannelerror",
    "servestdio",  # createMcpHandler is caught by "mcp"
    "legacy",
    *PROTOCOL_REVISIONS,
)
# POSIX extended syntax, which is what `git log -G` compiles; used with -i.
PREFILTER_REGEX = "|".join(PREFILTER_KEYWORDS)
# Pathspecs covering everything is_relevant_path accepts. Deliberately wider
# (a leading * matches any directory and any prefix of the name), since the
# miner applies the exact rule to whatever comes through.
PREFILTER_PATHSPECS = (
    *(f"*{suffix}" for suffix in sorted(SOURCE_SUFFIXES)),
    *(f"*{name}" for name in sorted(MANIFEST_NAMES)),
    "*requirements*.txt",
    "*requirements*.in",
)
