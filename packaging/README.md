# Building the desktop app

The downloaded app is built by `.github/workflows/release.yml` on a tag
`vX.Y.Z` that matches `pyproject.toml`. This folder is everything that workflow
uses, and each piece runs locally too.

| File | What it is |
|---|---|
| `requirements-app.txt` | The hash-pinned lock the app is frozen from, and the tests run against on every platform. |
| `requirements-build.txt`, `requirements-test.txt` | PyInstaller and pytest, pinned the same way. |
| `health-record.spec` | PyInstaller: one folder, no console, no llama-server and no weights. |
| `entry.py` | The app's entry point: the same launcher `health-agent app` runs. |
| `make_icon.py` | Draws the icon; PyInstaller turns it into `.icns` and `.ico`. |
| `make_dmg.sh` | macOS: ad-hoc signature, checked, then a disk image. |
| `installer.iss` | Windows: an Inno Setup installer, per person, no administrator. |
| `lifecycle.py` | Runs the frozen app's self-tests and checks from outside that no reader survives Quit or a kill. |
| `release_notes.py`, `RELEASE_NOTES.md` | The draft release's notes, written from what the checks found. |

## Freezing it on your own machine

```
pip install --require-hashes -r packaging/requirements-app.txt
pip install --require-hashes -r packaging/requirements-build.txt
pip install --no-deps -e .
python packaging/make_icon.py build/icon.png
pyinstaller --noconfirm --distpath build/dist --workpath build/work packaging/health-record.spec
python packaging/lifecycle.py --app "build/dist/Health Record/Health Record" --work /tmp/lifecycle --out /tmp/lifecycle/results.json
```

Add `--reader` to the last line to download the reader (about 4 GB) and check
it starts, passes the startup probe, and stops with the app.

## Changing a dependency

Edit `pyproject.toml`, then regenerate the lock the same way it was made:

```
uv pip compile pyproject.toml --extra documents --extra keychain --extra app \
    --universal --generate-hashes --python-version 3.14 --no-header \
    -o packaging/requirements-app.txt
```

The test workflow installs that file on Linux, macOS and Windows, so a
dependency that does not work somewhere fails there before it is frozen.
