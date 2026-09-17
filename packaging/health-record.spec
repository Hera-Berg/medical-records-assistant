# PyInstaller spec for the desktop app. Built by .github/workflows/release.yml,
# one runner per platform: PyInstaller cannot cross-build, and cannot make a
# universal2 macOS app when ctranslate2, onnxruntime and av ship per-arch wheels.
#
#   python packaging/make_icon.py build/icon.png
#   pyinstaller --noconfirm packaging/health-record.spec
#
# One folder, not one file: a one-file build unpacks about 250 MB to a temporary
# folder on every launch and is flagged by antivirus far more often.
#
# What is NOT in it, on purpose: llama-server and every model's weights. They
# are downloaded on first use through agent/runtime/manifest.py, pinned by size
# and sha256, after the person agrees to the exact number of bytes.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - defined by PyInstaller
NAME = "Health Record"

datas = []
binaries = []
hiddenimports = []

# The package itself: every module, including the ones imported lazily inside
# functions, and its data — the built interface and the measurements file.
hiddenimports += collect_submodules("agent")
datas += collect_data_files("agent", excludes=["**/__pycache__/**"])

# Speech: faster-whisper's silence filter model, and the native libraries of
# ctranslate2, onnxruntime and PyAV.
datas += collect_data_files("faster_whisper")
for native in ("ctranslate2", "onnxruntime", "av", "tokenizers", "pypdfium2_raw"):
    binaries += collect_dynamic_libs(native)
    datas += collect_data_files(native)
hiddenimports += collect_submodules("av")

# Chosen at runtime by name, so invisible to the import scanner.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("keyring.backends")
if sys.platform == "darwin":
    hiddenimports += ["pystray._darwin"]
elif sys.platform == "win32":
    hiddenimports += ["pystray._win32"]
else:
    hiddenimports += ["pystray._appindicator", "pystray._gtk", "pystray._xorg"]

analysis = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # hf_xet accelerates Hugging Face transfers through huggingface_hub; the app
    # loads speech weights only from files already verified on disk.
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "hf_xet"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)  # noqa: F821

icon = str(ROOT / "build" / "icon.png")

exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    # No console window: this is a tray app. stdout and stderr go to app.log.
    console=False,
    upx=False,
    icon=icon,
    codesign_identity=None,
    entitlements_file=None,
)

collected = COLLECT(  # noqa: F821
    exe,
    analysis.binaries,
    analysis.datas,
    upx=False,
    name=NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        collected,
        name=f"{NAME}.app",
        icon=icon,
        bundle_identifier="io.github.health-record",
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": __import__("os").environ.get("HEALTH_RECORD_VERSION", "0.0.0"),
            # A menu-bar app: no Dock icon and no window of its own.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
