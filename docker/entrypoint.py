"""Start the record inside the container: open or create it, then serve.

The desktop app asks for a folder in the browser on first run. Here the folder
is already chosen — it is whatever is mounted at ``/data/vault`` — so the same
decision the app's setup makes is made here, by the same code
(:mod:`agent.app.setup`), with nothing to choose:

- a folder holding ``config.toml`` is **joined**, and nothing in it is written;
- an empty folder is **created** into a new record;
- a folder with other things in it is **refused**, and the container stops
  saying why, rather than scattering a record among someone's files.

Which of those happens is decided by what is in the folder, not by whether
this container has run before. Creating or joining also issues this
container's device identity (kept in the ``health-machine`` volume) and marks
the reader question as pending for the Welcome screen; a start against a
record this container already knows only checks the identity is still there.

**It starts as root, briefly.** When the folder named for the record does not
exist yet, Docker creates it — owned by root, which the record's owner then
cannot write to. The one thing done as root is to give back what can only be
Docker's own doing: an *empty* mount owned by root, and this container's own
``health-machine`` volume. A folder with anything in it is never re-owned,
because that could be someone's synced folder. Then the process drops to the
owner's uid and gid for good, before any record code runs.

Then it becomes the server, by ``exec``, so the init process's SIGTERM on
``docker compose down`` reaches uvicorn directly and the reader is shut down
with it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HOME = Path("/home/health")


def _drop_privileges(target: Path) -> None:
    if os.getuid() != 0:
        return
    uid = int(os.environ["HEALTH_UID"])
    gid = int(os.environ["HEALTH_GID"])
    if target.is_dir() and target.stat().st_uid == 0 and not any(target.iterdir()):
        os.chown(target, uid, gid)
    for root, dirs, files in os.walk(HOME):
        for name in [root, *(os.path.join(root, n) for n in dirs + files)]:
            if os.lstat(name).st_uid != uid:
                os.lchown(name, uid, gid)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    if os.getuid() == 0:
        raise SystemExit("health-record: refusing to run as root")


def main() -> int:
    target = Path(os.environ.get("HEALTH_VAULT", "/data/vault"))
    _drop_privileges(target)

    # Imported only now, as the record's owner: nothing of the record's own code
    # runs as root.
    from agent import config as config_mod  # noqa: PLC0415
    from agent import vault as vault_mod  # noqa: PLC0415
    from agent.app import setup  # noqa: PLC0415

    # Decided by what is in the folder, never by whether this container has
    # run before: HEALTH_VAULT_DIR can be pointed somewhere new between runs.
    is_record = (target / config_mod.CONFIG_FILENAME).is_file()
    if is_record and vault_mod.pointer_path().exists():
        setup.ensure_identity()
    else:
        plan = setup.examine(str(target.parent), target.name, locations=[])
        if not plan.ok:
            print(f"health-record: {plan.refusal}", file=sys.stderr)
            print(
                "health-record: point HEALTH_VAULT_DIR at an empty folder, or at an "
                "existing record, and start again.",
                file=sys.stderr,
            )
            return 2
        setup.carry_out(plan)
        verb = "created a new record" if plan.action == setup.CREATE else "opened the record"
        print(f"health-record: {verb} in the mounted folder", flush=True)

    print("health-record: open http://127.0.0.1:7777 in your browser", flush=True)
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "agent.cli", "serve", "--vault", str(target)],
    )
    return 0  # not reached


if __name__ == "__main__":
    sys.exit(main())
