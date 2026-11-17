# Charm architecture

## Workload

`src/charm.py` handles Juju events and status. `src/workload.py` handles the release archive,
systemd files, service user, and health request. The charm downloads the pinned official release
on first configuration or when the pinned version changes. It verifies the archive checksum before
replacing `/opt/grafana-mcp-vm/bin/mcp-grafana`. The prior executable is retained as
`mcp-grafana.previous` and restored when a new binary fails its health check.

## Configuration and secrets

`grafana-url` is public configuration. `grafana-token-secret` references a Grafana service-account
token with read access to the required data sources and IRM alert groups. `caller-token-secret`
references the bearer token that an MCP client presents to this service. Each secret has a `token`
field. The charm reads secrets on config and secret-change events and writes them to
`/etc/grafana-mcp-vm/environment` with mode `0600`. A secret rotation restarts the service.

The server uses Streamable HTTP, `--disable-write`, and a fixed list of read tool areas. Loki and
Prometheus query guardrails are enforced. The default listener is loopback; a Tailscale IPv4
listener is an explicit config choice. The charm opens no public Juju port. The local `/healthz`
endpoint does not carry credentials; MCP requests require the caller bearer token.
When `allowed-host` is set, service rendering passes the exact DNS name and its `:443`
form with all three loopback Host values to `--allowed-hosts`. The renderer rejects
wildcards and argument-shaped values. This changes Host validation, not the listener,
caller authentication, or tool permissions.

## Topology and upgrades

The charm supports one unit and has no relations, peers, or persistent data. A multi-location
deployment needs independent instances and client-side failover. A charm refresh keeps the
installed binary when its pin is unchanged. A changed pin triggers a checksum-verified
replacement and health check;
failure restores the prior binary and blocks the unit for operator review.
