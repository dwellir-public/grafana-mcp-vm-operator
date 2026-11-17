"""Behavior checks for the Grafana MCP workload helper."""

import tarfile
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

from workload import READ_TOOLS, install_archive, render_service, write_environment


def _archive(binary: bytes) -> bytes:
    """Build a small release-shaped archive for an installation test."""
    data = BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        info = tarfile.TarInfo("mcp-grafana")
        info.size = len(binary)
        archive.addfile(info, BytesIO(binary))
    return data.getvalue()


def test_install_archive_verifies_checksum_before_replacing_binary(tmp_path: Path) -> None:
    """A bad release must leave the current binary intact."""
    destination = tmp_path / "mcp-grafana"
    destination.write_bytes(b"current")
    with pytest.raises(ValueError, match="checksum"):
        install_archive(_archive(b"new"), "0" * 64, destination)
    assert destination.read_bytes() == b"current"


def test_install_archive_replaces_binary_atomically(tmp_path: Path) -> None:
    """A verified release becomes executable at the fixed path."""
    data = _archive(b"new")
    destination = tmp_path / "mcp-grafana"
    install_archive(data, sha256(data).hexdigest(), destination)
    assert destination.read_bytes() == b"new"
    assert destination.stat().st_mode & 0o111


def test_service_is_read_only_and_keeps_tokens_out_of_argv() -> None:
    """The systemd command must use read tools and an environment file."""
    unit = render_service("127.0.0.1", 8000)
    assert "--disable-write" in unit
    assert f"--enabled-tools={READ_TOOLS}" in unit
    assert "EnvironmentFile=/etc/grafana-mcp-vm/environment" in unit
    assert "--server-auth-token" not in unit


def test_service_allows_funnel_host_and_retains_loopback_hosts() -> None:
    """An approved public Host adds to the three local Host values."""
    unit = render_service("127.0.0.1", 8000, "mcp.example.com")
    assert (
        "--allowed-hosts=localhost:8000,127.0.0.1:8000,[::1]:8000,"
        "mcp.example.com,mcp.example.com:443"
    ) in unit
    assert "--disable-write" in unit
    assert "EnvironmentFile=/etc/grafana-mcp-vm/environment" in unit


@pytest.mark.parametrize("hostname", ["*", "bad host", "example.com --disable-write", "foo,bar"])
def test_service_rejects_unsafe_public_hostnames(hostname: str) -> None:
    """Host configuration cannot disable validation or add process arguments."""
    with pytest.raises(ValueError, match="allowed host"):
        render_service("127.0.0.1", 8000, hostname)


def test_environment_file_is_private_and_contains_both_tokens(tmp_path: Path) -> None:
    """Both credentials are stored in a root-readable-only runtime file."""
    path = tmp_path / "environment"
    write_environment(path, "https://grafana.example.com", "grafana-secret", "caller-secret")
    assert path.stat().st_mode & 0o777 == 0o600
    content = path.read_text()
    assert 'GRAFANA_SERVICE_ACCOUNT_TOKEN="grafana-secret"' in content
    assert 'MCP_GRAFANA_SERVER_TOKEN="caller-secret"' in content


def test_environment_rejects_newlines_in_credentials(tmp_path: Path) -> None:
    """A token cannot inject another systemd environment setting."""
    with pytest.raises(ValueError, match="newline"):
        write_environment(
            tmp_path / "environment", "https://grafana.example.com", "ok\nBAD=1", "ok"
        )
