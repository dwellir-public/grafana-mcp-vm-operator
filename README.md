# grafana-mcp-vm

Ubuntu machine charm for the official [Grafana MCP server](https://github.com/grafana/mcp-grafana).
The charm runs one read-only server. It binds to `127.0.0.1:8000` by default;
the only other accepted listener is a Tailscale IPv4 address.

The charm pins Grafana MCP v2.2.0 and verifies the upstream archive's SHA-256 before installing
it. It uses a dedicated `grafana-mcp` system user and keeps the Grafana and MCP caller tokens in
Juju secrets. The generated systemd environment file is mode `0600` and is never included in
status text or process arguments.

## Configure

Create two Juju secrets with a `token` field and grant them to the `grafana-mcp-vm` application.
Set `grafana-url`, `grafana-token-secret`, and `caller-token-secret` to the stack URL and secret IDs.
See [DEVELOPING.md](DEVELOPING.md) for build and validation commands.

An MCP client should use `http://127.0.0.1:8000/mcp` when deployed on the same
machine and present the caller token as a bearer token. No public Juju port is opened.

If an operator deliberately exposes the MCP endpoint through a public HTTPS proxy, set
`allowed-host` to its exact DNS hostname, for example
`mcp.example.com`. The charm then adds that hostname and its explicit
`:443` form to the three local Host values. It rejects wildcards and whitespace. The
bearer token and read-only tool restrictions remain in force. Keep `allowed-host` empty
for a local-only deployment. The public `/healthz` endpoint is unauthenticated; do not
use it to infer MCP access.

For a local-only deployment, leave `allowed-host` empty and keep the listener on loopback.

## Build and release

Pull requests and pushes to `main` run the locked lint and unit checks. The manual
`Release charm to Charmhub and GitHub` workflow builds the Ubuntu 24.04 amd64
artifact, uploads it to Charmhub `latest/edge`, and attaches the same artifact
to a GitHub prerelease. It requires a repository secret named `CHARMHUB_TOKEN`
containing a Charmcraft exported login scoped to this charm and the edge channel.
See [DEVELOPING.md](DEVELOPING.md) for local checks.
