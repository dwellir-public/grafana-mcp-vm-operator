# Developing

This charm targets Ubuntu 24.04 on amd64. Install `uv`, `tox`, `charmcraft`, and a Juju 3.x client.

```bash
uv sync --group dev
tox -e format,lint,unit
charmcraft pack
```

The integration suite probes an explicitly selected, already deployed test application.
It does not create, refresh, or destroy a model. Set `JUJU_TEST_MODEL` before running
`tox -e integration`. Do not point it at a production workload model.

The release pin and archive hash live in `src/workload.py`. When changing the pin, verify the
new release checksum from Grafana's published checksums, update both constants, and run the
unit suite and integration suite. Keep both credentials in Juju secrets; do not put token values
in config, logs, or this repository.

To release, export a Charmcraft login for `grafana-mcp-vm` with revision and release
permissions limited to `latest/edge`. Store the exported value in the GitHub
`CHARMHUB_TOKEN` secret, then dispatch the release workflow from `main`. Confirm
the Charmhub revision and channel mapping before refreshing a deployed application.
