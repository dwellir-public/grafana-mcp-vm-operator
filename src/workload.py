"""Install and configure the pinned Grafana MCP workload."""

import ipaddress
import json
import os
import pwd
import re
import subprocess
import tarfile
import tempfile
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

VERSION = "2.2.0"
ARCHIVE_URL = (
    "https://github.com/grafana/mcp-grafana/releases/download/v2.2.0/"
    "mcp-grafana_Linux_x86_64.tar.gz"
)
ARCHIVE_SHA256 = "f93bfbd94cde68cc2e43cf5ba55da4f8e28b1a36b14fb14dadd566fc36b1312b"
BINARY = Path("/opt/grafana-mcp-vm/bin/mcp-grafana")
VERSION_FILE = BINARY.parent / ".version"
ENVIRONMENT = Path("/etc/grafana-mcp-vm/environment")
UNIT = Path("/etc/systemd/system/grafana-mcp-vm.service")
READ_TOOLS = "search,datasource,incident,prometheus,loki,alerting,dashboard,folder,oncall,tempo"
HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def install_archive(data: bytes, expected_sha256: str, destination: Path) -> None:
    """Verify a release archive and atomically replace the installed executable."""
    if sha256(data).hexdigest() != expected_sha256:
        raise ValueError("release archive checksum mismatch")
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
        member = next(
            (item for item in archive if item.name == "mcp-grafana" and item.isfile()), None
        )
        if member is None:
            raise ValueError("release archive lacks mcp-grafana binary")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise ValueError("cannot read mcp-grafana binary")
        binary = extracted.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as staging:
        staging.write(binary)
        temporary = Path(staging.name)
    try:
        temporary.chmod(0o755)
        previous = destination.with_suffix(".previous")
        if destination.exists():
            os.replace(destination, previous)
        try:
            os.replace(temporary, destination)
        except OSError:
            if previous.exists():
                os.replace(previous, destination)
            raise
    finally:
        temporary.unlink(missing_ok=True)


def download_release() -> bytes:
    """Fetch the fixed upstream release with a bounded request timeout."""
    with urlopen(ARCHIVE_URL, timeout=45) as response:
        return response.read(100_000_000)


def restore_previous(destination: Path) -> bool:
    """Restore the executable saved by the last verified archive installation."""
    previous = destination.with_suffix(".previous")
    if not previous.exists():
        return False
    os.replace(previous, destination)
    return True


def render_service(address: str, port: int, allowed_host: str = "") -> str:
    """Render a read-only MCP service with exact local and optional public Host values."""
    parsed = ipaddress.ip_address(address)
    if not (parsed.is_loopback or parsed in ipaddress.ip_network("100.64.0.0/10")):
        raise ValueError("listen address must be loopback or Tailscale IPv4")
    if not 1 <= port <= 65535:
        raise ValueError("invalid listen port")
    host = allowed_host.strip().lower()
    if host and (
        len(host) > 253
        or not HOSTNAME.fullmatch(host)
        or any(len(label) > 63 for label in host.split("."))
    ):
        raise ValueError("invalid allowed host")
    hosts = [f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"]
    if host:
        hosts.extend((host, f"{host}:443"))
    command = " ".join(
        (
            str(BINARY),
            "-t streamable-http",
            f"-address {address}:{port}",
            f"--allowed-hosts={','.join(hosts)}",
            "--disable-write",
            f"--enabled-tools={READ_TOOLS}",
            "--usage-stats=disabled",
            "--loki-guardrail-mode=enforce",
            "--prometheus-guardrail-mode=enforce",
        )
    )
    return f"""[Unit]
Description=Grafana MCP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=grafana-mcp
Group=grafana-mcp
EnvironmentFile={ENVIRONMENT}
ExecStart={command}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""


def write_environment(path: Path, grafana_url: str, grafana_token: str, caller_token: str) -> None:
    """Write both tokens as private systemd environment values without shell evaluation."""
    if not grafana_url.startswith("https://"):
        raise ValueError("Grafana URL must use HTTPS")
    if not grafana_token or not caller_token:
        raise ValueError("both tokens are required")
    values = (grafana_url, grafana_token, caller_token)
    if any("\n" in value or "\r" in value for value in values):
        raise ValueError("newline in environment value")
    content = "".join(
        f"{key}={json.dumps(value)}\n"
        for key, value in (
            ("GRAFANA_URL", grafana_url),
            ("GRAFANA_SERVICE_ACCOUNT_TOKEN", grafana_token),
            ("MCP_GRAFANA_SERVER_TOKEN", caller_token),
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as staging:
        staging.write(content)
        temporary = Path(staging.name)
    try:
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_user() -> None:
    """Create the unprivileged service identity once, without a login shell."""
    try:
        pwd.getpwnam("grafana-mcp")
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                "grafana-mcp",
            ],
            check=True,
        )


def run_systemctl(*arguments: str) -> None:
    """Run one systemd lifecycle operation with an explicit argument list."""
    subprocess.run(["systemctl", *arguments], check=True, timeout=30)


def is_healthy(address: str, port: int) -> bool:
    """Check the workload's local health endpoint without sending credentials."""
    with urlopen(f"http://{address}:{port}/healthz", timeout=3) as response:
        return response.status == 200
