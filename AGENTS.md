# grafana-mcp-vm

Keep this charm read-only and single-unit. Never log or commit Grafana
or MCP caller tokens. Preserve the fixed release checksum, loopback default, and authenticated
MCP transport. Run `tox -e lint,unit` and `charmcraft pack` before proposing deployment.
