"""Write the release notes from the template and what the workflow actually found.

Every line about what was checked comes from a ``results.json`` written by
``packaging/lifecycle.py`` on that platform, in the run that built the file.
A check that failed says so and says why; one that did not run says that. The
notes are a draft for a person to read before publishing, and the thing they
most need to read is this table.

    python packaging/release_notes.py --version 1.0.0 --results DIR --assets DIR \
        --repository owner/name --out notes.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.runtime import manifest  # noqa: E402

PLATFORMS = {
    "macos-arm64": ("Mac (Apple Silicon)", manifest.platforms.MACOS_ARM64),
    "windows-x64": ("Windows", manifest.platforms.WINDOWS_X64),
}

CHECKS = {
    "first-run": "Starts, asks where to keep the record, creates it, serves it, quits; every bundled library loads",
    "reader-quit": "Downloads the reader through the app, checks every file, passes the startup check with a test picture, and leaves no reader running after Quit",
    "reader-killed": "Leaves no reader running when the app is killed instead of quit",
}


def gigabytes(size: int) -> str:
    return f"{size / 1000**3:.1f} GB"


def megabytes(size: int) -> str:
    return f"{size / 1000**2:.0f} MB"


def outcome(check: dict | None) -> str:
    if not check:
        return "Not run"
    if check.get("ok") is True:
        extra = check.get("reader_gone_after_s")
        return "Passed" + (f" (reader gone after {extra} s)" if extra is not None else "")
    if check.get("ok") is None:
        return f"Not run — {check.get('why', 'no reason recorded')}"
    why = check.get("why") or (check.get("report") or {}).get("error") or "see the workflow log"
    return f"**Failed** — {why}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--onnxruntime", required=True, help="the version the lock pins")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    found: dict[str, dict] = {}
    for key in PLATFORMS:
        path = args.results / key / "results.json"
        try:
            found[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            found[key] = {}

    names = {key: "Not built for this release — see the checks below." for key in PLATFORMS}
    sizes = {}
    for asset in sorted(args.assets.iterdir()):
        for key in PLATFORMS:
            if key in asset.name:
                names[key] = f"`{asset.name}`"
                sizes[key] = asset.stat().st_size

    header = "| Check | " + " | ".join(label for label, _ in PLATFORMS.values()) + " |"
    rule = "|---|" + "---|" * len(PLATFORMS)
    rows = [header, rule]
    rows.append(
        "| Download size | "
        + " | ".join(megabytes(sizes[key]) if key in sizes else "not built" for key in PLATFORMS)
        + " |"
    )
    for check, words in CHECKS.items():
        rows.append(
            f"| {words} | "
            + " | ".join(outcome((found[key].get("checks") or {}).get(check)) for key in PLATFORMS)
            + " |"
        )

    reader = manifest.required(manifest.platforms.MACOS_ARM64, True, manifest.DEFAULT_VISION_MODEL)
    unverified = "\n".join(
        [
            "Not checked by any machine, and so not claimed:",
            "",
            "- **The warnings described above.** A build machine never downloads its own",
            "  output through a browser, so it never sees them.",
            "- **The icon in the menu bar or tray.** It needs a screen and a person to",
            "  click it. The self-test presses the same Quit the menu item presses.",
            "- **Reading speed.** GitHub's build machines are shared virtual machines, so",
            "  how long a document took there says nothing about a real computer. The app",
            "  reports what your own computer measured.",
            "- **Reading accuracy.** The startup check proves the reader can see a test",
            "  picture. How well it reads prescriptions is measured separately, and the",
            "  model list in the app shows those numbers beside each model.",
        ]
    )
    not_built = (
        "There is **no download for Intel Macs**. The model that types up voice notes "
        f"needs onnxruntime, and onnxruntime {args.onnxruntime} is not published for "
        "Intel Macs. Building one anyway would mean shipping older libraries that none "
        "of the tests ran against, which is worse than not shipping it. There is no "
        "download for Linux either: `pip install 'health-agent[app]'` and "
        "`health-agent app` run the same app there."
    )

    template = (ROOT / "packaging" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    body = template.format(
        version=args.version,
        mac_arm64=names["macos-arm64"],
        windows_x64=names["windows-x64"],
        not_built=not_built,
        repository=args.repository,
        reader_download=gigabytes(sum(bundle.size for bundle in reader)),
        results="\n".join(rows),
        unverified=unverified,
    )
    args.out.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
