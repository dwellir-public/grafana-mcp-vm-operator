"""Exercise Juju config and secret events without changing the host."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import ops
from ops.testing import Context, Secret, State

import workload
from charm import GrafanaMcpVmCharm


def _configured() -> State:
    """Create a state with separate Grafana and MCP caller tokens."""
    grafana = Secret({"token": "grafana-secret"})
    caller = Secret({"token": "caller-secret"})
    return State(
        config={
            "grafana-url": "https://grafana.example.com",
            "grafana-token-secret": grafana.id,
            "caller-token-secret": caller.id,
        },
        secrets=[grafana, caller],
    )


def _host(monkeypatch, tmp_path: Path) -> Mock:
    """Redirect host files and service calls to an isolated test directory."""
    monkeypatch.setattr(workload, "BINARY", tmp_path / "bin/mcp-grafana")
    monkeypatch.setattr(workload, "VERSION_FILE", tmp_path / "bin/version")
    monkeypatch.setattr(workload, "ENVIRONMENT", tmp_path / "etc/environment")
    monkeypatch.setattr(workload, "UNIT", tmp_path / "etc/grafana-mcp-vm.service")
    monkeypatch.setattr(workload, "download_release", lambda: b"archive")

    def install(*_args):
        workload.BINARY.parent.mkdir(parents=True, exist_ok=True)
        workload.BINARY.write_bytes(b"binary")

    monkeypatch.setattr(workload, "install_archive", install)
    monkeypatch.setattr(workload, "ensure_user", lambda: None)
    monkeypatch.setattr(workload, "is_healthy", lambda *_: True)
    run = Mock()
    monkeypatch.setattr(workload, "run_systemctl", run)
    return run


def test_missing_secret_config_blocks_without_installing(monkeypatch, tmp_path: Path) -> None:
    """The charm refuses to start if the secret references are absent."""
    run = _host(monkeypatch, tmp_path)
    ctx = Context(GrafanaMcpVmCharm)
    result = ctx.run(ctx.on.config_changed(), State())
    assert isinstance(result.unit_status, ops.BlockedStatus)
    run.assert_not_called()


def test_configured_start_uses_two_secrets(monkeypatch, tmp_path: Path) -> None:
    """A configured unit writes private credentials and starts the service."""
    run = _host(monkeypatch, tmp_path)
    ctx = Context(GrafanaMcpVmCharm)
    result = ctx.run(ctx.on.config_changed(), _configured())
    assert isinstance(result.unit_status, ops.ActiveStatus)
    content = workload.ENVIRONMENT.read_text()
    assert "grafana-secret" in content and "caller-secret" in content
    assert workload.ENVIRONMENT.stat().st_mode & 0o777 == 0o600
    assert run.called


def test_allowed_host_config_restarts_with_public_host(monkeypatch, tmp_path: Path) -> None:
    """The charm renders the approved hostname while keeping local access."""
    _host(monkeypatch, tmp_path)
    ctx = Context(GrafanaMcpVmCharm)
    state = _configured()
    configured = replace(
        state,
        config={
            **state.config,
            "allowed-host": "mcp.example.com",
        },
    )
    result = ctx.run(ctx.on.config_changed(), configured)
    assert isinstance(result.unit_status, ops.ActiveStatus)
    assert "--allowed-hosts=" in workload.UNIT.read_text()
    assert "mcp.example.com" in workload.UNIT.read_text()


def test_secret_change_rewrites_environment_and_restarts(monkeypatch, tmp_path: Path) -> None:
    """Rotating the Grafana token causes the running service to reload it."""
    run = _host(monkeypatch, tmp_path)
    ctx = Context(GrafanaMcpVmCharm)
    first = ctx.run(ctx.on.config_changed(), _configured())
    old = next(
        secret
        for secret in first.secrets
        if secret.tracked_content.get("token") == "grafana-secret"
    )
    rotated = replace(old, latest_content={"token": "new-grafana-secret"})
    state = replace(first, secrets=[rotated, *(s for s in first.secrets if s.id != old.id)])
    result = ctx.run(ctx.on.secret_changed(rotated), state)
    assert isinstance(result.unit_status, ops.ActiveStatus)
    assert "new-grafana-secret" in workload.ENVIRONMENT.read_text()
    assert run.call_count >= 2


def test_failed_upgrade_restores_previous_binary(monkeypatch, tmp_path: Path) -> None:
    """An unhealthy new release cannot replace the last working executable."""
    _host(monkeypatch, tmp_path)
    monkeypatch.setattr(workload, "VERSION_FILE", tmp_path / "bin/version")
    workload.BINARY.parent.mkdir(parents=True, exist_ok=True)
    workload.BINARY.write_bytes(b"old-binary")
    workload.VERSION_FILE.write_text("old-version")

    def install(*_args):
        workload.BINARY.with_suffix(".previous").write_bytes(workload.BINARY.read_bytes())
        workload.BINARY.write_bytes(b"new-binary")

    monkeypatch.setattr(workload, "install_archive", install)
    monkeypatch.setattr(workload, "is_healthy", lambda *_: False)
    monkeypatch.setattr("charm.time.sleep", lambda *_: None)
    ctx = Context(GrafanaMcpVmCharm)
    result = ctx.run(ctx.on.config_changed(), _configured())
    assert isinstance(result.unit_status, ops.BlockedStatus)
    assert workload.BINARY.read_bytes() == b"old-binary"
    assert workload.VERSION_FILE.read_text() == "old-version"


def test_remove_stops_service_and_deletes_private_environment(monkeypatch, tmp_path: Path) -> None:
    """Removing the charm leaves no running service or token file behind."""
    run = _host(monkeypatch, tmp_path)
    ctx = Context(GrafanaMcpVmCharm)
    configured = ctx.run(ctx.on.config_changed(), _configured())
    assert workload.ENVIRONMENT.exists()
    ctx.run(ctx.on.remove(), configured)
    assert not workload.ENVIRONMENT.exists()
    assert ("disable", "--now", "grafana-mcp-vm.service") in [
        call.args for call in run.call_args_list
    ]
