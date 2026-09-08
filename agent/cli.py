"""``health-agent`` command line.

Phases 1 to 3 have no HTTP layer, so this is the only way to exercise the vault
by hand. Subcommands from the start because this grows into ``rebuild``,
``doctor`` and ``set-key``.

``check`` writes nothing. ``check --fix`` performs exactly two writes: creating
the vault directories, and issuing this machine's device identity. It never
writes ``config.toml`` — it prints a template for the user to save, because a
config file this program invented is one nobody has read.

``ingest`` copies a file into ``raw/`` and appends one event. It never moves or
alters the file it was given.

``rebuild`` regenerates ``wiki/`` from the log. It appends nothing and touches
neither ``raw/`` nor ``events/``, and it removes only files a previous rebuild
wrote — see ``agent.projection.writer``. ``--as-of`` pins the moment staleness
and the seven-day review window are measured from, which is what makes two
rebuilds of an unchanged log produce identical bytes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, TextIO

from . import ingest as ingest_mod
from . import projection as projection_mod
from . import vault as vault_mod
from .errors import HealthAgentError
from .vault import Vault

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

    raw_info = report.get("raw")
    if raw_info:
        depth = "re-hashed" if raw_info["deep"] else "not re-hashed"
        line(
            "raw",
            f"{raw_info['artifacts']} artefacts  {raw_info['bytes']} bytes  ({depth})",
        )

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
    report = vault_mod.diagnose(args.vault, fix=args.fix, deep=args.deep)
    if args.json:
        json.dump(report, out, indent=2, sort_keys=True)
        print("", file=out)
    else:
        _render(report, out)
    return EXIT_OK if report.get("ok") else EXIT_PROBLEMS


def cmd_ingest(args: argparse.Namespace, out: TextIO) -> int:
    """Copy files into the vault. The originals are left where they are."""
    vault = Vault.open(args.vault)
    results: list[dict[str, Any]] = []
    failures = 0

    for path in args.paths:
        context = ingest_mod.CaptureContext(source=args.source, note=args.note)
        try:
            result = ingest_mod.ingest_path(vault, path, context)
        except HealthAgentError as exc:
            failures += 1
            results.append({"input": str(path), "status": "failed", "error": str(exc)})
            if not args.json:
                print(f"failed   {path}\n         {exc}", file=out)
            continue
        results.append(
            {
                "input": str(path),
                "status": result.status,
                "hash": result.digest,
                "path": result.rel,
                "bytes": result.size,
                "mime": result.mime,
                "event": result.event.id,
            }
        )
        if not args.json:
            print(result.describe(), file=out)

    if args.json:
        json.dump(results, out, indent=2, sort_keys=True)
        print("", file=out)
    return EXIT_PROBLEMS if failures else EXIT_OK


def cmd_rebuild(args: argparse.Namespace, out: TextIO) -> int:
    """Replay the event log into wiki/."""
    vault = Vault.open(args.vault)
    report = projection_mod.rebuild(vault, as_of=args.as_of)

    if args.json:
        json.dump(report.to_dict(), out, indent=2, sort_keys=True)
        print("", file=out)
        return EXIT_PROBLEMS if report.problems else EXIT_OK

    stats = report.projection.stats()
    print(
        f"rebuilt   {stats['entities']} entities, {stats['files']} files, "
        f"{stats['timeline_rows']} timeline rows",
        file=out,
    )
    print(
        f"wrote     {len(report.write.written)} changed, "
        f"{len(report.write.unchanged)} unchanged, {len(report.write.removed)} removed",
        file=out,
    )

    queue = report.projection.review_by_tier
    if queue:
        summary = ", ".join(
            f"{len(queue[tier])} {tier}" for tier in ("high", "medium", "low") if tier in queue
        )
        print(f"review    {summary} awaiting you", file=out)
        for item in report.projection.review:
            print(f"          [{item.consequence}] {item.summary}", file=out)
    else:
        print("review    nothing awaiting you", file=out)

    if stats["conflicts"]:
        print(f"conflicts {stats['conflicts']} unresolved, shown in the wiki", file=out)

    for anomaly in report.projection.anomalies:
        print(f"note      {anomaly}", file=out)
    for problem in report.problems:
        print(f"PROBLEM   {problem}", file=out)

    return EXIT_PROBLEMS if report.problems else EXIT_OK


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
    check.add_argument(
        "--deep",
        action="store_true",
        help=(
            "re-hash every stored artefact to confirm the bytes still match the "
            "record; reads the whole raw store"
        ),
    )
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.set_defaults(func=cmd_check)

    ingest = subparsers.add_parser(
        "ingest",
        help="copy files into raw/ and record them; the originals are not moved",
    )
    ingest.add_argument("paths", nargs="+", help="files to ingest")
    ingest.add_argument(
        "--source",
        default="cli",
        choices=sorted(ingest_mod.CAPTURE_SOURCES),
        help="where these bytes came from (recorded as capture context)",
    )
    ingest.add_argument(
        "--note",
        default=None,
        help="a short note about this capture, stored with it",
    )
    ingest.add_argument("--json", action="store_true", help="machine-readable output")
    ingest.set_defaults(func=cmd_ingest)

    rebuild = subparsers.add_parser(
        "rebuild",
        help="regenerate wiki/ from the event log; writes nothing to events/ or raw/",
    )
    rebuild.add_argument(
        "--as-of",
        default=None,
        dest="as_of",
        help=(
            "the moment to measure staleness and the review window from, as "
            "YYYY-MM-DDTHH:MM:SSZ; defaults to now. Pinning it makes the output "
            "reproducible."
        ),
    )
    rebuild.add_argument("--json", action="store_true", help="machine-readable output")
    rebuild.set_defaults(func=cmd_rebuild)
    return parser


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args, out or sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
