#!/usr/bin/env python3
"""Read-only health checks for a Canopy validator node."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SEVERITY = {"OK": 0, "WARNING": 1, "CRITICAL": 2}


class RpcError(RuntimeError):
    """Raised when an RPC endpoint cannot return valid JSON."""


@dataclass(frozen=True)
class Config:
    rpc_url: str = "http://127.0.0.1:50002"
    admin_url: str = "http://127.0.0.1:50003"
    admin_enabled: bool = True
    admin_user: str = ""
    admin_password: str = ""
    timeout: float = 4.0
    min_peers: int = 1
    stale_height_seconds: float = 600.0
    warning_percent: float = 80.0
    critical_percent: float = 90.0


RequestFunction = Callable[[str, str, str, float, str, str], Any]


def evaluate_height_progress(
    report: dict[str, Any],
    state: dict[str, Any] | None,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Compare the current height with the last observed height change."""

    height = report.get("node", {}).get("height")
    if isinstance(height, bool) or not isinstance(height, int):
        return state

    observed_at = now or datetime.now(timezone.utc)
    status = "OK"
    message = f"height baseline recorded at {height}"

    previous_height = state.get("height") if isinstance(state, dict) else None
    changed_at_raw = state.get("changed_at") if isinstance(state, dict) else None
    changed_at = None
    if isinstance(changed_at_raw, str):
        try:
            changed_at = datetime.fromisoformat(changed_at_raw)
            if changed_at.tzinfo is None:
                changed_at = changed_at.replace(tzinfo=timezone.utc)
        except ValueError:
            changed_at = None

    if isinstance(previous_height, int) and not isinstance(previous_height, bool):
        if height > previous_height:
            message = f"height advanced from {previous_height} to {height}"
        elif height < previous_height:
            status = "WARNING"
            message = f"height decreased from {previous_height} to {height}"
        elif changed_at is not None:
            unchanged_seconds = max(0.0, (observed_at - changed_at).total_seconds())
            if unchanged_seconds >= stale_after_seconds:
                status = "CRITICAL"
                message = (
                    f"height {height} has not advanced for "
                    f"{int(unchanged_seconds)} seconds"
                )
            else:
                message = (
                    f"height {height} unchanged for {int(unchanged_seconds)} seconds; "
                    f"limit is {int(stale_after_seconds)}"
                )

    if not isinstance(previous_height, int) or height != previous_height:
        new_state = {
            "height": height,
            "changed_at": observed_at.isoformat(),
        }
    else:
        new_state = {
            "height": height,
            "changed_at": (
                changed_at.isoformat() if changed_at is not None else observed_at.isoformat()
            ),
        }

    report["checks"].append(
        {"name": "height_progress", "status": status, "message": message}
    )
    report["status"] = max(
        report["checks"], key=lambda item: SEVERITY[item["status"]]
    )["status"]
    return new_state


def _join_url(base_url: str, route: str) -> str:
    return f"{base_url.rstrip('/')}/{route.lstrip('/')}"


def request_json(
    base_url: str,
    route: str,
    method: str,
    timeout: float,
    username: str = "",
    password: str = "",
) -> Any:
    """Call one Canopy RPC route and decode its JSON response."""

    headers = {"Accept": "application/json"}
    data = None
    if method == "POST":
        data = b"{}"
        headers["Content-Type"] = "application/json"

    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    request = Request(
        _join_url(base_url, route),
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RpcError(f"HTTP {exc.code} from {route}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RpcError(f"cannot reach {route}: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RpcError(f"invalid JSON from {route}") from exc


def _value_as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_height(value: Any) -> int:
    if isinstance(value, bool):
        raise RpcError("height response is not a number")
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        height = value.get("height")
        if isinstance(height, int) and not isinstance(height, bool):
            return height
    raise RpcError("height response has an unexpected shape")


def _status_for_percent(value: float, config: Config) -> str:
    if value >= config.critical_percent:
        return "CRITICAL"
    if value >= config.warning_percent:
        return "WARNING"
    return "OK"


def collect_report(
    config: Config,
    request: RequestFunction = request_json,
) -> dict[str, Any]:
    """Collect public and local admin RPC data without storing identities."""

    checks: list[dict[str, str]] = []
    node: dict[str, Any] = {}

    def add_check(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    try:
        version = request(config.rpc_url, "/v1/", "GET", config.timeout, "", "")
        node["version"] = version if isinstance(version, str) else str(version)
        add_check("version", "OK", f"node reports {node['version']}")
    except RpcError as exc:
        add_check("version", "CRITICAL", str(exc))

    try:
        raw_height = request(
            config.rpc_url,
            "/v1/query/height",
            "POST",
            config.timeout,
            "",
            "",
        )
        node["height"] = _extract_height(raw_height)
        add_check("height", "OK", f"next block height is {node['height']}")
    except RpcError as exc:
        add_check("height", "CRITICAL", str(exc))

    if config.admin_enabled:
        auth = (config.admin_user, config.admin_password)

        try:
            peer_info = request(
                config.admin_url,
                "/v1/admin/peer-info",
                "GET",
                config.timeout,
                *auth,
            )
            peers = int(peer_info.get("numPeers", 0))
            inbound = int(peer_info.get("numInbound", 0))
            outbound = int(peer_info.get("numOutbound", 0))
            node["peers"] = {
                "total": peers,
                "inbound": inbound,
                "outbound": outbound,
            }
            if peers == 0:
                add_check("peers", "CRITICAL", "node has no connected peers")
            elif peers < config.min_peers:
                add_check(
                    "peers",
                    "WARNING",
                    f"{peers} connected; expected at least {config.min_peers}",
                )
            else:
                add_check(
                    "peers",
                    "OK",
                    f"{peers} connected ({inbound} inbound, {outbound} outbound)",
                )
        except (RpcError, TypeError, ValueError, AttributeError) as exc:
            add_check("peers", "WARNING", f"peer data unavailable: {exc}")

        try:
            consensus = request(
                config.admin_url,
                "/v1/admin/consensus-info",
                "GET",
                config.timeout,
                *auth,
            )
            syncing = bool(consensus.get("isSyncing", False))
            view = consensus.get("view") or {}
            consensus_status = str(consensus.get("status", "unknown"))
            node["consensus"] = {
                "syncing": syncing,
                "status": consensus_status,
                "height": view.get("height"),
                "round": view.get("round"),
                "phase": view.get("phase"),
            }
            if syncing:
                add_check("consensus", "WARNING", "node is syncing")
            else:
                add_check("consensus", "OK", consensus_status)
        except (RpcError, TypeError, AttributeError) as exc:
            add_check("consensus", "WARNING", f"consensus data unavailable: {exc}")

        try:
            usage = request(
                config.admin_url,
                "/v1/admin/resource-usage",
                "GET",
                config.timeout,
                *auth,
            )
            process = usage.get("process") or {}
            system = usage.get("system") or {}
            resources = {
                "system_cpu_percent": _value_as_float(system.get("usedCPUPercent")),
                "system_memory_percent": _value_as_float(system.get("usedRAMPercent")),
                "system_disk_percent": _value_as_float(system.get("usedDiskPercent")),
                "process_cpu_percent": _value_as_float(process.get("usedCPUPercent")),
                "process_memory_percent": _value_as_float(process.get("usedMemoryPercent")),
            }
            node["resources"] = resources

            for key, label in (
                ("system_cpu_percent", "CPU"),
                ("system_memory_percent", "memory"),
                ("system_disk_percent", "disk"),
            ):
                value = resources[key]
                if value is None:
                    add_check(label.lower(), "WARNING", f"{label} metric unavailable")
                    continue
                status = _status_for_percent(value, config)
                add_check(label.lower(), status, f"{label} usage is {value:.1f}%")
        except (RpcError, TypeError, AttributeError) as exc:
            add_check("resources", "WARNING", f"resource data unavailable: {exc}")
    else:
        add_check("admin", "OK", "admin checks disabled")

    overall = max(checks, key=lambda item: SEVERITY[item["status"]])["status"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": overall,
        "node": node,
        "checks": checks,
    }


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without adding a dependency."""

    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only health checks for a Canopy validator",
    )
    parser.add_argument("--rpc-url", default=os.getenv("CANOPY_RPC_URL", Config.rpc_url))
    parser.add_argument(
        "--admin-url",
        default=os.getenv("CANOPY_ADMIN_URL", Config.admin_url),
    )
    parser.add_argument("--no-admin", action="store_true")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("CANOPY_TIMEOUT", "4")))
    parser.add_argument("--min-peers", type=int, default=int(os.getenv("CANOPY_MIN_PEERS", "1")))
    parser.add_argument(
        "--stale-height-seconds",
        type=float,
        default=float(os.getenv("CANOPY_STALE_HEIGHT_SECONDS", "600")),
        help="mark the node critical when height does not advance for this long",
    )
    parser.add_argument(
        "--warning-percent",
        type=float,
        default=float(os.getenv("CANOPY_WARNING_PERCENT", "80")),
    )
    parser.add_argument(
        "--critical-percent",
        type=float,
        default=float(os.getenv("CANOPY_CRITICAL_PERCENT", "90")),
    )
    parser.add_argument("--report-dir", default=os.getenv("CANOPY_REPORT_DIR", "reports"))
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    return parser


def _print_human(
    report: dict[str, Any],
    report_path: Path | None,
    report_error: str | None,
) -> None:
    print("Canopy validator observer")
    print(f"Status: {report['status']}")
    for check in report["checks"]:
        print(f"[{check['status']:<8}] {check['name']}: {check['message']}")
    if report_path:
        print(f"Report: {report_path}")
    elif report_error:
        print(f"Report: not saved ({report_error})")


def _save_report(report: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = directory / f"canopy-status-{timestamp}.json"
    content = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    timestamped.write_text(content, encoding="utf-8")
    (directory / "latest.json").write_text(content, encoding="utf-8")
    return timestamped


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _save_height_state(state: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    content = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    (directory / "height-state.json").write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(".env"))
    args = _build_parser().parse_args(argv)
    admin_enabled = _env_bool("CANOPY_ADMIN_ENABLED", True) and not args.no_admin

    config = Config(
        rpc_url=args.rpc_url,
        admin_url=args.admin_url,
        admin_enabled=admin_enabled,
        admin_user=os.getenv("CANOPY_ADMIN_USER", ""),
        admin_password=os.getenv("CANOPY_ADMIN_PASSWORD", ""),
        timeout=args.timeout,
        min_peers=args.min_peers,
        stale_height_seconds=args.stale_height_seconds,
        warning_percent=args.warning_percent,
        critical_percent=args.critical_percent,
    )

    if config.warning_percent >= config.critical_percent:
        print("warning threshold must be lower than critical threshold", file=sys.stderr)
        return 2
    if config.stale_height_seconds <= 0:
        print("stale height threshold must be positive", file=sys.stderr)
        return 2

    report_directory = Path(args.report_dir)
    report = collect_report(config)
    height_state = _read_json_object(report_directory / "height-state.json")
    next_height_state = evaluate_height_progress(
        report,
        height_state,
        config.stale_height_seconds,
    )
    report_path = None
    report_error = None
    if not args.no_report:
        try:
            report_path = _save_report(report, report_directory)
            if next_height_state is not None:
                _save_height_state(next_height_state, report_directory)
        except OSError as exc:
            report_error = str(exc)
            report["checks"].append(
                {
                    "name": "report",
                    "status": "WARNING",
                    "message": f"report could not be saved: {exc}",
                }
            )
            if report["status"] == "OK":
                report["status"] = "WARNING"

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human(report, report_path, report_error)
    return SEVERITY[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
