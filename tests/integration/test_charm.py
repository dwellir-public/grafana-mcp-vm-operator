"""Check an explicitly selected deployed unit without exposing its credentials."""

import json
import os

import jubilant
import pytest

APP = "grafana-mcp-vm"

REMOTE_PROBE = r"""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

endpoint = "http://127.0.0.1:8000/mcp"
lines = Path("/etc/grafana-mcp-vm/environment").read_text().splitlines()
environment = dict(line.split("=", 1) for line in lines)
token = json.loads(environment["MCP_GRAFANA_SERVER_TOKEN"])
headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
body = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "charm-integration-test", "version": "0.1"},
    },
}).encode()
try:
    urlopen(Request(endpoint, body, headers=headers), timeout=5)
    unauthenticated = 200
except HTTPError as error:
    unauthenticated = error.code
headers["Authorization"] = "Bearer " + token
with urlopen(Request(endpoint, body, headers=headers), timeout=10) as response:
    authenticated = response.status
    session = response.headers.get("Mcp-Session-Id")
    response.read()
if session:
    headers["Mcp-Session-Id"] = session
listing = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode()
with urlopen(Request(endpoint, listing, headers=headers), timeout=10) as response:
    tools = [tool["name"] for tool in json.load(response)["result"]["tools"]]
with urlopen("http://127.0.0.1:8000/healthz", timeout=5) as response:
    health = response.status
checks = {
    "health": health, "unauthenticated": unauthenticated,
    "authenticated": authenticated, "query_prometheus": "query_prometheus" in tools,
    "read_only": "create_incident" not in tools and "add_annotation" not in tools,
}
print(json.dumps(checks))
"""


def test_deployed_charm_requires_auth_and_has_read_tools() -> None:
    """Probe an explicitly selected application through its local listener."""
    model = os.environ.get("JUJU_TEST_MODEL")
    if not model:
        pytest.skip("Set JUJU_TEST_MODEL to probe an existing test application")
    juju = jubilant.Juju(model=model, wait_timeout=600)
    status = juju.status()
    if APP not in status.apps:
        pytest.fail("Deploy and configure the charm before running this test")
    result = juju.exec("python3", "-c", REMOTE_PROBE, unit=f"{APP}/0")
    checks = json.loads(result.stdout)
    assert checks == {
        "health": 200,
        "unauthenticated": 401,
        "authenticated": 200,
        "query_prometheus": True,
        "read_only": True,
    }
