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

``demo`` seeds a scratch vault with an invented record and rebuilds it, so the
renderer can be read by hand and extraction can be run over something. It
refuses to touch a folder that is not empty, and it reaches no inference
endpoint unless ``--endpoint-from`` copies one out of a config you name — which
it then says, on the terminal and in the folder.

``rebuild`` regenerates ``wiki/`` from the log. It appends nothing and touches
neither ``raw/`` nor ``events/``, and it removes only files a previous rebuild
wrote — see ``agent.projection.writer``. ``--as-of`` pins the moment staleness
and the seven-day review window are measured from, which is what makes two
rebuilds of an unchanged log produce identical bytes.

``probe`` says which of three states the inference box is in — unreachable,
unauthorised, working — before a ninety-second job discovers it the slow way. It
reports ``auth: ok | failed | missing`` and never the key.

``extract`` queues unread artefacts and drains the queue. ``--artifact <hash>``
runs one named artefact instead, which is what checking a bad read consists of;
``--limit`` says how many run and never which. ``--resume`` un-parks a queue that
stopped because the key was rejected. ``--dry-run`` builds the prompt and prints
its hash without calling anything. **A run that does nothing says why** — an
empty pass with no explanation is indistinguishable from a broken command.

``--vault`` is accepted before or after the subcommand, in either position.

``set-key`` writes the inference credential to the OS keychain. It never writes
one to ``config.toml``, which lives in the vault and syncs with it.

``serve`` runs the HTTP layer and the interface on ``127.0.0.1`` and nothing
else. There is no authentication in front of it and there is not going to be
any — the port is the boundary — so a non-loopback host is refused rather than
warned about.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

from . import demo as demo_mod
from . import ingest as ingest_mod
from . import projection as projection_mod
from . import vault as vault_mod
from .extract import (
    evaluate as evaluate_mod,
    jobs as jobs_mod,
    probe as probe_mod,
    runner as runner_mod,
    session,
)
from .llm import credentials as credentials_mod
from .errors import HealthAgentError
from .vault import Vault

EXIT_OK = 0
EXIT_PROBLEMS = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def cmd_demo(args: argparse.Namespace, out: TextIO) -> int:
    """Seed a scratch vault with invented data and rebuild it."""
    report = demo_mod.seed(
        args.path,
        as_of=projection_mod.parse_as_of(args.as_of),
        endpoint_from=args.endpoint_from,
    )

    if args.json:
        json.dump(report.to_dict(), out, indent=2, sort_keys=True)
        print("", file=out)
        return EXIT_OK

    stats = report.rebuild.projection.stats()
    print(f"seeded    {report.root}", file=out)
    print(
        f"          {report.artifacts} artefacts in raw/, {report.events} events, "
        f"device {demo_mod.DEMO_DEVICE}",
        file=out,
    )
    if report.endpoint is None:
        print("endpoint  none — a demo vault reaches no model by default", file=out)
    else:
        # Named, every time, and never merely implied by the absence of a
        # warning. A vault of invented data that can reach a real box is a thing
        # the person who made it has to have been told about in so many words.
        print(f"endpoint  [models.vlm] copied from {report.endpoint.source}", file=out)
        print(
            "          that table and [models.vlm.auth] only — no credential, no "
            "sync_profile",
            file=out,
        )
        for name in report.endpoint.dropped:
            print(f"          not copied: {name} is not a scalar setting", file=out)
    print(
        f"rebuilt   {stats['entities']} entities, {stats['files']} files, "
        f"{stats['timeline_rows']} timeline rows",
        file=out,
    )

    queue = report.rebuild.projection.review_by_tier
    if queue:
        summary = ", ".join(
            f"{len(queue[tier])} {tier}" for tier in ("high", "medium", "low") if tier in queue
        )
        print(f"review    {summary} awaiting you", file=out)
    for anomaly in report.rebuild.projection.anomalies:
        print(f"note      {anomaly}", file=out)
    for problem in report.rebuild.problems:
        print(f"PROBLEM   {problem}", file=out)

    print(f"\nThis is invented data. See {report.root / demo_mod.MARKER_FILENAME}.", file=out)
    print(f"Start reading at {report.root / 'wiki'}.", file=out)
    return EXIT_PROBLEMS if report.rebuild.problems else EXIT_OK


def cmd_probe(args: argparse.Namespace, out: TextIO) -> int:
    """Say which of the three states the box is in, before a job finds out."""
    vault = Vault.open(args.vault)
    with session.open_client(vault) as client:
        report = probe_mod.run(client, skip_vision=args.skip_vision)

    if args.json:
        json.dump(report.to_dict(), out, indent=2, sort_keys=True)
        print("", file=out)
        return EXIT_OK if report.ok else EXIT_PROBLEMS

    print(f"endpoint  {report.state}", file=out)
    print(f"auth      {report.auth}", file=out)
    for check in report.checks:
        print(f"          {check.describe()}", file=out)
    for note in report.notes:
        print(f"note      {note}", file=out)
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def cmd_extract(args: argparse.Namespace, out: TextIO) -> int:
    """Queue unread artefacts and drain the queue.

    ``--artifact`` names what to run. Re-running one specific image is what
    checking a bad read actually consists of, and ``--limit`` cannot express it:
    it says how many, never which.
    """
    vault = Vault.open(args.vault)
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    # Under --json nothing prose-shaped may reach the stream: a "queued 3" line
    # above the object makes the whole output unparseable. What those lines say
    # goes into the object instead.
    def say(text: str) -> None:
        if not args.json:
            print(text, file=out)

    resumed: tuple = ()
    if args.resume:
        resumed = queue.resume()
        say(f"resumed   {len(resumed)} parked job(s)")
        if not resumed:
            say("          nothing was parked")

    for note in queue.malformed:
        say(f"note      {note}; the line is kept as it is, never rewritten")

    only: list[str] | None = None
    if args.artifact:
        # Resolved before anything else runs, so a typed hash that names nothing
        # is a message rather than a silent empty pass.
        only = [runner_mod.resolve_artifact(vault, token) for token in args.artifact]

    queued: list[str] = []

    def fill_queue() -> None:
        if only is not None:
            runner_mod.enqueue_artifacts(vault, queue, only)
            queued.extend(only)
            say(f"selected  {', '.join(only)}")
            return
        added = runner_mod.enqueue_unread(vault, queue)
        queued.extend(job.artifact for job in added)
        if added:
            say(f"queued    {len(added)} artefact(s) not yet read")

    if args.dry_run:
        fill_queue()
        # Nothing is sent and nothing is appended. Useful for confirming what a
        # prompt change will re-extract before it re-extracts it.
        ready = [
            job for job in queue.ready() if only is None or job.artifact in set(only)
        ]
        print(f"dry run   {len(ready)} job(s) would run", file=out)
        for job in ready:
            print(f"          {job.describe()}", file=out)
        if not ready:
            print(
                f"          nothing would run: "
                f"{runner_mod.explain_idle(vault, queue, _now(), only=only)}",
                file=out,
            )
        return EXIT_OK

    # The endpoint is resolved before the queue is filled. A vault with no
    # endpoint — a demo vault is deliberately one — should say so, not print
    # nine queued lines first and then say it, having written jobs for a run
    # that could never have happened.
    with session.open_client(vault) as client:
        fill_queue()
        report = runner_mod.drain(
            vault, runner_mod.Extractor(vault, client, locale=vault.config.locale),
            queue, limit=args.limit, only=only,
        )

    if args.json:
        json.dump(
            {
                "stats": report.stats(),
                "outcomes": [
                    {
                        "artifact": o.artifact,
                        "state": o.state,
                        "reading": o.reading,
                        "claims": o.claims,
                        "reason": o.reason,
                    }
                    for o in report.outcomes
                ],
                "parked": [job.artifact for job in report.parked],
                "parked_reason": report.parked_reason,
                "idle_reason": report.idle_reason,
                "queued": sorted(queued),
                "resumed": [job.artifact for job in resumed],
                "malformed": list(queue.malformed),
                "queue": queue.counts(),
            },
            out,
            indent=2,
            sort_keys=True,
        )
        print("", file=out)
        return EXIT_PROBLEMS if report.is_parked else EXIT_OK

    for outcome in report.outcomes:
        print(outcome.describe(), file=out)
        for note in outcome.notes:
            print(f"          {note}", file=out)

    stats = report.stats()
    print(
        f"appended  {stats['events_appended']} events, {stats['claims']} claims proposed",
        file=out,
    )
    if report.idle_reason:
        # A run that did nothing says why, every time. "appended 0 events, 0
        # claims proposed" with no lines above it is indistinguishable from a
        # broken command.
        print(f"nothing   {report.idle_reason}", file=out)
    if report.is_parked:
        # Deliberately not phrased as a failure to process. A rotated key and a
        # sleeping Mac are different problems and only one of them needs a person.
        print(f"PARKED    {report.parked_reason}", file=out)
        return EXIT_PROBLEMS
    depth = queue.depth()
    if depth:
        print(f"queued    {depth} still waiting — run again when the box is up", file=out)
    if stats["claims"]:
        print("\nNothing has reached the wiki. Run `health-agent rebuild`, then "
              "review the proposals.", file=out)
    return EXIT_OK


def cmd_set_key(args: argparse.Namespace, out: TextIO) -> int:
    """Write the inference credential to the OS keychain.

    Never to ``config.toml``: that file lives at the vault root and syncs to
    Dropbox, Drive or Nextcloud, so a key written there has been handed to a
    third party by definition.
    """
    try:
        import keyring
    except ImportError:
        print(
            "error: the `keyring` package is not installed, so there is no keychain "
            "to write to. Install it with `pip install 'health-agent[keychain]'`, or "
            "put the key in the environment variable named by "
            "[models.vlm.auth] api_key_env, or in a 0600 file at "
            f"{credentials_mod.credentials_path()}.",
            file=out,
        )
        return EXIT_PROBLEMS

    value = args.key or getpass.getpass("key for the inference box (not echoed): ")
    if not value.strip():
        # The same rule the resolver applies: an empty credential is a
        # misconfiguration to report, never a blank header to send.
        print("error: an empty key is not a credential. Nothing was written.", file=out)
        return EXIT_PROBLEMS

    keyring.set_password(
        credentials_mod.KEYRING_SERVICE, credentials_mod.KEYRING_ACCOUNT, value.strip()
    )
    print(
        f"stored    in the OS keychain under "
        f"{credentials_mod.KEYRING_SERVICE}/{credentials_mod.KEYRING_ACCOUNT}",
        file=out,
    )
    print("          run `health-agent probe` to check the box accepts it", file=out)
    return EXIT_OK


def cmd_serve(args: argparse.Namespace, out: TextIO) -> int:
    """Run the local server and the interface.

    Blocks until interrupted. Everything this serves is also a subcommand here,
    which is deliberate: a folder that outlives the app has to be usable without
    one, and a person debugging a broken interface needs a way in that does not
    go through the interface.
    """
    from . import server as server_mod  # noqa: PLC0415 - uvicorn is slow to import

    vault = Vault.open(args.vault)
    host = server_mod.runtime.check_host(args.host)
    port = args.port or vault.config.port

    build = server_mod.static.build_info()
    print(f"vault     {vault.root}", file=out)
    print(f"serving   http://{host}:{port}", file=out)
    if not build["present"]:
        print(
            "interface NOT BUILT — the API answers, but / has no page. Run "
            "`npm --prefix frontend ci && npm --prefix frontend run build`",
            file=out,
        )
    elif build["commit"]:
        print(f"interface built from {build['commit']} at {build['built']}", file=out)
    if args.no_worker:
        print(
            "worker    off — captures queue and stay queued; run "
            "`health-agent extract` to read them",
            file=out,
        )
    print("\nNothing here is reachable from another machine. Ctrl-C to stop.", file=out)
    # Flushed before uvicorn takes the process, because it never gives it back.
    # Python line-buffers a terminal but block-buffers a pipe, so without this
    # everything above is invisible whenever the output is redirected or
    # captured — which is how a service manager runs it, and the case where a
    # person most needs to be told which vault and which port.
    out.flush()

    server_mod.serve(vault, host=host, port=port, worker=not args.no_worker)
    return EXIT_OK


def cmd_eval(args: argparse.Namespace, out: TextIO) -> int:
    """Score the model against the golden corpus. Run before accepting a swap.

    Needs the box, so it is a command rather than a test: nothing in the
    ordinary suite may depend on a machine being awake.
    """
    from tests.fixtures import corpus  # noqa: PLC0415 - only needed here

    vault = Vault.open(args.vault)
    fixtures = corpus.for_phase(4)
    with session.open_client(vault) as client:
        report = evaluate_mod.run_corpus(client, fixtures, locale=vault.config.locale)

    if args.json:
        json.dump(report.to_dict(), out, indent=2, sort_keys=True)
        print("", file=out)
        return EXIT_OK if report.ok else EXIT_PROBLEMS

    for line in report.describe():
        print(line, file=out)
    skipped = [f.name for f in corpus.FIXTURES if f.phase > 4]
    if skipped:
        print(
            f"\nnot run   {', '.join(skipped)} — these need the speech model, "
            f"which is phase 6",
            file=out,
        )
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def server_host_default() -> str:
    """The only host ``serve`` binds without being argued with."""
    from .server.runtime import DEFAULT_HOST  # noqa: PLC0415

    return DEFAULT_HOST


_VAULT_HELP = (
    "vault root; otherwise HEALTH_VAULT, otherwise the pointer file at "
    "~/.config/health-agent/vault. Accepted before or after the subcommand"
)


def _add_vault(parser: argparse.ArgumentParser) -> None:
    """Let ``--vault`` be typed after the subcommand as well as before it.

    ``health-agent extract --vault X`` is the order people actually type, and it
    used to fail with argparse's bare "unrecognized arguments" — which does not
    say that the flag exists, only that it is not welcome there.

    ``SUPPRESS`` as the default is what makes this safe: without it the
    subparser would write its own ``None`` over a value the top-level parser had
    already taken from the earlier position, and ``--vault X extract`` would
    silently fall back to ``HEALTH_VAULT``.
    """
    parser.add_argument("--vault", default=argparse.SUPPRESS, help=_VAULT_HELP)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="health-agent",
        description="Patient-held health record agent.",
    )
    parser.add_argument("--vault", default=None, help=_VAULT_HELP)
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
    _add_vault(check)
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
    _add_vault(ingest)
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
    _add_vault(rebuild)
    rebuild.set_defaults(func=cmd_rebuild)

    demo = subparsers.add_parser(
        "demo",
        help=(
            "seed an empty folder with an invented record and rebuild it, for "
            "reading the renderer's output by hand"
        ),
    )
    demo.add_argument("path", help="a folder that does not exist, or is empty")
    demo.add_argument(
        "--as-of",
        default=None,
        dest="as_of",
        help=(
            "the moment the scenario is anchored to; every document date is an "
            "offset back from it. Defaults to now, which is what keeps the demo "
            "looking the same whenever it is run."
        ),
    )
    demo.add_argument(
        "--endpoint-from",
        default=None,
        dest="endpoint_from",
        metavar="CONFIG",
        help=(
            "copy [models.vlm] and [models.vlm.auth] out of this config.toml (or "
            "the vault root holding one) so extraction can be run against the demo. "
            "Nothing else is copied — no credential, no sync_profile. Off by "
            "default: a demo vault reaches no model"
        ),
    )
    demo.add_argument("--json", action="store_true", help="machine-readable output")
    _add_vault(demo)
    demo.set_defaults(func=cmd_demo)

    probe = subparsers.add_parser(
        "probe",
        help=(
            "check the inference box: is it reachable, does it accept the key, and "
            "does it actually read images"
        ),
    )
    probe.add_argument(
        "--skip-vision",
        action="store_true",
        help=(
            "skip the image check. Only for a box you have already verified: a "
            "server that silently discards images answers text perfectly and "
            "ignores every prescription photo"
        ),
    )
    probe.add_argument("--json", action="store_true", help="machine-readable output")
    _add_vault(probe)
    probe.set_defaults(func=cmd_probe)

    extract = subparsers.add_parser(
        "extract",
        help="read queued artefacts with the model and propose claims from them",
    )
    extract.add_argument(
        "--artifact",
        action="append",
        metavar="HASH",
        default=None,
        help=(
            "read this artefact and nothing else, by the short hash in its raw/ "
            "filename or its wiki footnote; repeatable. Without it every artefact "
            "the log has no extraction for is queued. --limit says how many run, "
            "never which"
        ),
    )
    extract.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after this many artefacts",
    )
    extract.add_argument(
        "--resume",
        action="store_true",
        help=(
            "un-park a queue that stopped because the key was rejected; run it "
            "after setting a new key"
        ),
    )
    extract.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="say what would run without contacting the box or appending anything",
    )
    extract.add_argument("--json", action="store_true", help="machine-readable output")
    _add_vault(extract)
    extract.set_defaults(func=cmd_extract)

    set_key = subparsers.add_parser(
        "set-key",
        help="store the inference credential in the OS keychain (never in the vault)",
    )
    set_key.add_argument(
        "--key",
        default=None,
        help=(
            "the key. Omit it to be prompted without echo, which keeps it out of "
            "your shell history"
        ),
    )
    _add_vault(set_key)
    set_key.set_defaults(func=cmd_set_key)

    serve = subparsers.add_parser(
        "serve",
        help="run the local interface and API on 127.0.0.1",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="port to bind; defaults to the one in config.toml",
    )
    serve.add_argument(
        "--host",
        default=server_host_default(),
        help=(
            "host to bind. Loopback only, and not negotiable: there is no "
            "authentication here, so the port is the whole boundary around the "
            "record. Use your own VPN to reach it from elsewhere"
        ),
    )
    serve.add_argument(
        "--no-worker",
        action="store_true",
        dest="no_worker",
        help=(
            "do not read queued artefacts in the background. Captures still "
            "queue; `health-agent extract` drains them"
        ),
    )
    _add_vault(serve)
    serve.set_defaults(func=cmd_serve)

    evaluate = subparsers.add_parser(
        "eval",
        help=(
            "score the configured model against the golden corpus; run this "
            "before accepting a model or prompt change"
        ),
    )
    evaluate.add_argument("--json", action="store_true", help="machine-readable output")
    _add_vault(evaluate)
    evaluate.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    stream = out or sys.stdout
    try:
        return args.func(args, stream)
    except HealthAgentError as exc:
        # Every refusal in this program is written to be read — an unresolvable
        # vault, a device identity from another machine, a demo pointed at a real
        # folder. A traceback in front of that message hides the sentence that
        # tells the user what to do.
        print(f"error: {exc}", file=stream)
        return EXIT_PROBLEMS


if __name__ == "__main__":
    raise SystemExit(main())
