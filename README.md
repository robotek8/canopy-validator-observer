# Canopy Validator Observer

A small read-only monitoring tool for my Canopy validator.

It collects a concise health snapshot from the node's public and local admin RPC endpoints. The
tool does not submit transactions, read the validator keystore, or store validator and peer
identities.

This is a personal community project, not official Canopy software.

## What It Checks

- Node version and next block height
- Connected, inbound, and outbound peer counts
- Sync and consensus status
- System CPU, memory, and disk usage
- Process CPU and memory usage

The result is classified as `OK`, `WARNING`, or `CRITICAL`. Exit codes are `0`, `1`, and `2`
respectively, which makes the tool suitable for cron jobs or other monitoring systems.

## Run Next to the Official Node Container

The current official Canopy deployment exposes RPC ports inside the node container but does not
publish them directly on the host. The observer can safely share that container's network namespace.

```bash
cp .env.example .env
docker compose -f compose.observer.yml run --rm observer
```

The Compose file runs the observer as `0:0` by default so it can write to the host-mounted
`reports/` directory when launched by root. Set `OBSERVER_UID` and `OBSERVER_GID` in `.env` if the
project is operated by another host user.

The official container is named `node` by default. If yours has another name, change only this line
in `.env`:

```dotenv
CANOPY_NODE_CONTAINER=node
```

Do not put an externally accessible admin URL in a public repository.

## Run Directly

If RPC ports are already reachable from the host:

```bash
cp .env.example .env
python3 canopy_observer.py
```

For public RPC checks without admin access:

```bash
python3 canopy_observer.py --no-admin --rpc-url https://rpc.example.com
```

## Example Output

```text
Canopy validator observer
Status: OK
[OK      ] version: node reports 1.2.3
[OK      ] height: next block height is 12345
[OK      ] peers: 4 connected (1 inbound, 3 outbound)
[OK      ] consensus: voting on proposal
[OK      ] cpu: CPU usage is 20.0%
[OK      ] memory: memory usage is 40.0%
[OK      ] disk: disk usage is 50.0%
Report: reports/canopy-status-20260829T120000Z.json
```

Reports are written to `reports/` and ignored by Git. They contain health values only, not RPC URLs,
credentials, validator addresses, public keys, or peer lists.

If a report cannot be written, the observer now prints the health result and a warning instead of
terminating with a traceback.

## Live Validation

The observer was tested on August 29, 2026 against my running full validator:

- Canopy version: `v0.1.22+beta`
- Next block height at the time of the check: `2,116,327`
- Connected peers: nine total, five inbound, and four outbound
- Consensus status: `waiting for proposal`
- System usage: 8.4% CPU, 13.6% memory, and 11.1% disk
- Overall observer status: `OK`

The generated report was saved successfully. Node addresses, credentials, validator keys, and peer
identities are intentionally excluded from this record.

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `CANOPY_RPC_URL` | `http://127.0.0.1:50002` | Public RPC base URL |
| `CANOPY_ADMIN_URL` | `http://127.0.0.1:50003` | Local admin RPC base URL |
| `CANOPY_ADMIN_ENABLED` | `true` | Enables admin health checks |
| `CANOPY_TIMEOUT` | `4` | Request timeout in seconds |
| `CANOPY_MIN_PEERS` | `1` | Minimum peer count before a warning |
| `CANOPY_WARNING_PERCENT` | `80` | Resource warning threshold |
| `CANOPY_CRITICAL_PERCENT` | `90` | Resource critical threshold |

`CANOPY_ADMIN_USER` and `CANOPY_ADMIN_PASSWORD` are optional and should remain only in `.env`.

## Tests

The tests use fixed local responses and never contact a live validator.

```bash
python3 -m unittest discover -s tests -v
```

## Official References

- [Canopy Node](https://github.com/canopy-network/node)
- [Canopy RPC Reference](https://github.com/canopy-network/canopy/blob/main/cmd/rpc/README.md)
- [Canopy Editorial and Style Guide](https://canopy-network.gitbook.io/docs/contributor-hub/editorial-and-style-guide)

## License

MIT
