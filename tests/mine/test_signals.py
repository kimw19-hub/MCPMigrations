import pytest

from mcp_migration.mine.signals import count_signals, is_relevant_path


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("from mcp.server.fastmcp import FastMCP", {"py_v1_fastmcp_import"}),
        ("from mcp.server.mcpserver import MCPServer", {"py_v2_mcpserver_import"}),
        ('"mcp>=1.9,<2"', {"py_pin_mcp_below_2"}),
        ('"mcp>=1.9,<2.5"', set()),
        ('"mcp[cli]>=2.0.1"', {"py_pin_mcp_v2"}),
        ('"fastmcp>=2.0"', {"py_standalone_fastmcp"}),
        ('"@modelcontextprotocol/sdk": "^1.2"', {"ts_v1_sdk_pkg"}),
        ('import { x } from "@modelcontextprotocol/server"', {"ts_v2_split_pkg"}),
        ('"@modelcontextprotocol/server-filesystem": "1"', set()),
        ('mcp = FastMCP("Demo", stateless_http=True)', {"v1_stateless_flag"}),
        ("await ctx.session.create_message(msgs)", {"dep_sampling"}),
        ("headers['Mcp-Session-Id']", {"legacy_session_header"}),
    ],
)
def test_signal_matches(line: str, expected: set[str]) -> None:
    found = {k for k in count_signals([line]) if not k.startswith("revision_literal:")}
    assert found == expected


def test_revision_literals_counted() -> None:
    counts = count_signals(['PROTOCOL = "2025-06-18"', "supports 2026-07-28 and 2025-06-18"])
    assert counts["revision_literal:2025-06-18"] == 2
    assert counts["revision_literal:2026-07-28"] == 1


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/server.py", True),
        ("package.json", True),
        ("requirements-dev.txt", True),
        ("node_modules/x/index.js", False),
        ("README.md", False),
        (None, False),
    ],
)
def test_is_relevant_path(path: str | None, expected: bool) -> None:
    assert is_relevant_path(path) is expected


# At least one line per alternative of every signal. Kept separate from the
# cases above because its job is different: it pins down everything the
# prefilter must let through, so a signal missing from here fails the test.
SIGNAL_EXAMPLES = {
    "py_v1_fastmcp_import": ["from mcp.server.fastmcp import FastMCP"],
    "py_v2_mcpserver_import": ["from mcp.server.mcpserver import MCPServer"],
    "py_v1_McpError": ["except McpError as e:"],
    "py_v2_MCPError": ["raise MCPError(code)"],
    "py_v1_streamablehttp_client": ["async with streamablehttp_client(url) as s:"],
    "py_v2_httpx2": ["import httpx2"],
    "py_pin_mcp_v2": ['"mcp>=2.0"', "mcp[cli]==2.1", "mcp ~= 2"],
    "py_pin_mcp_below_2": ['"mcp>=1.9,<2"', "mcp<2"],
    "py_standalone_fastmcp": ["from fastmcp import FastMCP", "import fastmcp", "fastmcp>=2.3"],
    "ts_v1_sdk_pkg": ['"@modelcontextprotocol/sdk": "^1.2"'],
    "ts_v2_split_pkg": ['from "@modelcontextprotocol/server"', '"@modelcontextprotocol/client"'],
    "modern_server_discover": ['method: "server/discover"'],
    "modern_subscriptions_listen": ['"subscriptions/listen"'],
    "modern_input_required": ["return InputRequiredResult(...)"],
    "modern_version_const": ["LATEST_MODERN_VERSION", "in MODERN_PROTOCOL_VERSIONS"],
    "legacy_session_header": ["headers['Mcp-Session-Id']", "MCP-SESSION-ID"],
    "legacy_initialized_notif": ['"notifications/initialized"'],
    "legacy_session_id_generator": ["sessionIdGenerator: undefined"],
    "legacy_sse_transport": [
        "new SSEServerTransport(res)",
        "SseServerTransport::new()",
        'mcp.run(transport = "sse")',
        "run(transport='sse')",
    ],
    "v1_stateless_flag": ["stateless_http=True", "App(stateless = True)"],
    "dep_sampling": ['"sampling/createMessage"', "ctx.create_message (m)", "createMessage(m)"],
    "dep_roots": ['"roots/list"', "await list_roots()", "client.listRoots()"],
    "dep_logging": ['"logging/setLevel"', "send_log_message(x)", "sendLoggingMessage(x)"],
    "dep_resource_subscribe": ['"resources/subscribe"', "def subscribe_resource(uri):"],
    "dep_warning_handled": ["MCPDeprecationWarning", "except NoBackChannelError:"],
}


def test_every_signal_has_prefilter_examples() -> None:
    from mcp_migration.mine.signals import SIGNAL_PATTERNS

    assert set(SIGNAL_EXAMPLES) == set(SIGNAL_PATTERNS)


@pytest.mark.parametrize(
    ("signal", "line"), [(s, line) for s, lines in SIGNAL_EXAMPLES.items() for line in lines]
)
def test_the_prefilter_lets_through_every_signal(signal: str, line: str) -> None:
    """A line the prefilter misses is a commit silently dropped from the corpus."""
    import re

    from mcp_migration.mine.signals import PREFILTER_REGEX

    assert signal in count_signals([line]), "the example must exercise its own signal"
    assert re.search(PREFILTER_REGEX, line, re.IGNORECASE)


def test_the_prefilter_lets_through_every_revision() -> None:
    import re

    from mcp_migration.mine.signals import PREFILTER_REGEX, PROTOCOL_REVISIONS

    for revision in PROTOCOL_REVISIONS:
        assert re.search(PREFILTER_REGEX, f'VERSION = "{revision}"', re.IGNORECASE)


@pytest.mark.parametrize(
    "path",
    ["src/server.py", "a/b/package.json", "pyproject.toml", "x/requirements-dev.txt", "go.mod"],
)
def test_the_prefilter_paths_cover_every_relevant_file(path: str) -> None:
    from fnmatch import fnmatch

    from mcp_migration.mine.signals import PREFILTER_PATHSPECS

    assert is_relevant_path(path)
    # git matches a pathspec without glob magic like fnmatch without FNM_PATHNAME.
    assert any(fnmatch(path, spec) for spec in PREFILTER_PATHSPECS)
