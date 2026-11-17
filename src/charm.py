"""Juju orchestration for the read-only Grafana MCP service."""

import logging
import subprocess
import time

from ops import ActiveStatus, BlockedStatus, CharmBase, MaintenanceStatus, WaitingStatus, main
from ops.model import ModelError, SecretNotFoundError

import workload

logger = logging.getLogger(__name__)


class GrafanaMcpVmCharm(CharmBase):
    """Keep one pinned MCP server installed and configured from Juju secrets."""

    def __init__(self, *args):
        """Observe lifecycle and secret changes through a single reconciliation path."""
        super().__init__(*args)
        self.framework.observe(self.on.install, self._reconcile)
        self.framework.observe(self.on.config_changed, self._reconcile)
        self.framework.observe(self.on.secret_changed, self._reconcile)
        self.framework.observe(self.on.update_status, self._check_status)
        self.framework.observe(self.on.remove, self._remove)

    def _token(self, reference: str) -> str:
        """Read the token field from a granted Juju secret by ID."""
        value = self.model.get_secret(id=reference).get_content(refresh=True).get("token", "")
        if not value:
            raise ValueError("secret has no token field")
        return value

    def _reconcile(self, _event) -> None:
        """Validate config, install the pinned binary, and restart with current secrets."""
        logger.debug("Reconciling Grafana MCP workload")
        grafana_url = str(self.config.get("grafana-url") or "")
        grafana_secret = str(self.config.get("grafana-token-secret") or "")
        caller_secret = str(self.config.get("caller-token-secret") or "")
        if not all((grafana_url, grafana_secret, caller_secret)):
            self.unit.status = BlockedStatus("Set Grafana URL and both Juju secret references")
            return
        try:
            grafana_token = self._token(grafana_secret)
            caller_token = self._token(caller_secret)
            address = str(self.config["listen-address"])
            port = int(self.config["listen-port"])
            allowed_host = str(self.config["allowed-host"])
            service = workload.render_service(address, port, allowed_host)
            self.unit.status = MaintenanceStatus("Configuring Grafana MCP")
            installed_version = (
                workload.VERSION_FILE.read_text().strip() if workload.VERSION_FILE.exists() else ""
            )
            upgrade = not workload.BINARY.exists() or installed_version != workload.VERSION
            if upgrade:
                workload.install_archive(
                    workload.download_release(), workload.ARCHIVE_SHA256, workload.BINARY
                )
            workload.ensure_user()
            workload.write_environment(
                workload.ENVIRONMENT, grafana_url, grafana_token, caller_token
            )
            workload.UNIT.parent.mkdir(parents=True, exist_ok=True)
            workload.UNIT.write_text(service)
            workload.run_systemctl("daemon-reload")
            workload.run_systemctl("enable", "--now", "grafana-mcp-vm.service")
            workload.run_systemctl("restart", "grafana-mcp-vm.service")
            healthy = self._check_status(_event)
            if upgrade and healthy:
                workload.VERSION_FILE.write_text(workload.VERSION)
            elif upgrade and workload.restore_previous(workload.BINARY):
                workload.run_systemctl("restart", "grafana-mcp-vm.service")
                self.unit.status = BlockedStatus("MCP upgrade failed; previous binary restored")
        except (SecretNotFoundError, ModelError):
            self.unit.status = BlockedStatus(
                "Grant both configured Juju secrets to this application"
            )
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            logger.warning("Grafana MCP configuration failed: %s", type(error).__name__)
            self.unit.status = BlockedStatus("Grafana MCP configuration failed; inspect unit logs")

    def _check_status(self, _event) -> bool:
        """Report active only when the local health endpoint responds."""
        logger.debug("Checking Grafana MCP health")
        address = str(self.config["listen-address"])
        port = int(self.config["listen-port"])
        for _ in range(5):
            try:
                if workload.is_healthy(address, port):
                    self.unit.status = ActiveStatus(f"MCP listening on {address}:{port}")
                    return True
            except OSError:
                pass
            time.sleep(1)
        self.unit.status = WaitingStatus("Waiting for Grafana MCP health endpoint")
        return False

    def _remove(self, _event) -> None:
        """Stop the service and erase its private token file before charm removal."""
        logger.debug("Removing Grafana MCP workload")
        workload.run_systemctl("disable", "--now", "grafana-mcp-vm.service")
        workload.ENVIRONMENT.unlink(missing_ok=True)
        workload.UNIT.unlink(missing_ok=True)
        workload.run_systemctl("daemon-reload")


if __name__ == "__main__":
    main(GrafanaMcpVmCharm)
