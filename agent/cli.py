"""``health-agent`` command line.

Phases 1 to 3 have no HTTP layer, so this is the only way to exercise the vault
by hand. Subcommands from the start because this grows into ``rebuild``,
``doctor`` and ``set-key``.

``check`` writes nothing. ``check --fix`` performs exactly two writes: creating
the vault directories, and issuing this machine's device identity. It never
writes ``config.toml`` — it prints a template for the user to save, because a
config file this program invented is one nobody has read.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, TextIO

from . import vault as vault_mod

EXIT_OK = 0
EXIT_PROBLEMS = 1


def _render(report: dict[str, Any], out: TextIO) -> None:
    def line(label: str, text: str) -> None:
        print(f"{label:<10}{text}", file=out)

    vault_info = report.get("vault", {})
    if vault_info.get("status") == "unresolved":
        line("vault", "NOT FOUND")
        print(f"\n{vault_info['error']}", file=out)
        return

    line("vault", f"{vault_info['root']}  (from {vault_info['source']})")
    for name in vault_info.get("created", []):
        line("", f"created {name}")
    if vault_info.get("pointer"):
        line("", f"pointer {vault_info['pointer']}")
    if vault_info.get("missing_dirs"):
        line("", f"MISSING dirs: {', '.join(vault_info['missing_dirs'])}")
    if vault_info.get("writable") is False:
        line("", "NOT WRITABLE")

    config_info = report.get("config")
    if config_info:
        status = config_info.get("status")
        if status == "ok":
            line(
                "config",
                f"{config_info['sync_profile']}  port {config_info['port']}  "
                f"locale {config_info['locale']}",
            )
        else:
            line("config", status.upper())
            print(f"\n{config_info['error']}\n", file=out)

    device_info = report.get("device")
    if device_info:
        status = device_info.get("status")
        if status == "ok":
            suffix = " (issued now)" if device_info.get("issued") else ""
            line(
                "device",
                f"{device_info['id']}  label {device_info['label']}  "
                f"from {device_info['source']}{suffix}",
            )
        else:
            line("device", (status or "error").upper())

    log_info = report.get("log")
    if log_info:
        span = ""
        if log_info["first_ts"]:
            span = f"  {log_info['first_ts']} .. {log_info['last_ts']}"
        line(
            "log",
            f"{log_info['event_count']} events across "
            f"{len(log_info['shards'])} shards{span}",
        )
        for shard in log_info["shards"]:
            detail = f"  {shard['detail']}" if shard["detail"] else ""
            bad = f"  {shard['malformed']} malformed" if shard["malformed"] else ""
            line("", f"{shard['name']}  {shard['state']}  {shard['events']} events{bad}{detail}")

    conflicts = report.get("conflicts")
    if conflicts:
        line("conflicts", f"{len(conflicts)} sync fork(s)")

    notes = report.get("notes", [])
    if notes:
        print("", file=out)
        for note in notes:
            print(f"note: {note}", file=out)

    problems = report.get("problems", [])
    print("", file=out)
    if problems:
        for problem in problems:
            print(f"problem: {problem}", file=out)
        print(f"\n{len(problems)} problem(s).", file=out)
    else:
        print("No problems.", file=out)


def cmd_check(args: argparse.Namespace, out: TextIO) -> int:
    report = vault_mod.diagnose(args.vault, fix=args.fix)
    if args.json:
        json.dump(report, out, indent=2, sort_keys=True)
        print("", file=out)
    else:
        _render(report, out)
    return EXIT_OK if report.get("ok") else EXIT_PROBLEMS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="health-agent",
        description="Patient-held health record agent.",
    )
    parser.add_argument(
        "--vault",
        default=None,
        help=(
            "vault root; otherwise HEALTH_VAULT, otherwise the pointer file at "
            "~/.config/health-agent/vault"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check",
        help="report the state of the vault, config, device identity and event log",
    )
    check.add_argument(
        "--fix",
        action="store_true",
        help=(
            "create missing vault directories and issue this machine's device "
            "identity; never writes config.toml"
        ),
    )
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args, out or sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
