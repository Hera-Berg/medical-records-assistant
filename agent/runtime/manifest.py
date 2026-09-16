"""Every file the reader on this computer needs, pinned.

Each file is pinned three ways: a URL at a **fixed revision** (a Hugging Face
commit, a llama.cpp build tag — never ``main`` and never ``latest``), its
**exact size**, and its **sha256**. The size is what the download screen shows
before anything starts, so the number a person agrees to is the number that
arrives. The hash is what stands between a file and it being used.

The pins are code, not configuration. ``MODELS.md`` says artefacts are pinned
exactly and never by a floating tag; a pin a person can edit to anything would
turn verification into a formality. Changing one is a release, and because the
model identity string carries the weights hash, a new pin is a new identity —
documents read under the old weights stay distinguishable from those read
under the new.

The hashes were checked against the bytes, not only copied: every file below
was downloaded and hashed, and the result compared with the digest its host
publishes. The llama.cpp builds for macOS and Windows were hashed but never
**run** — see :data:`agent.runtime.platforms.VERIFIED`.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import platforms

#: The llama.cpp build every platform runs. One build everywhere, so a claim's
#: ``runtime.engine`` means the same code whichever machine read the document.
LLAMA_BUILD = "b10997"
LLAMA_COMMIT = "fccf7166f"
ENGINE = f"llama.cpp {LLAMA_BUILD}"
#: What ``/props`` reports as ``build_info``. Checked after every start: a
#: binary in the right place that is not the pinned build is not this reader.
BUILD_INFO = f"{LLAMA_BUILD}-{LLAMA_COMMIT}"

VISION_MODEL = "Qwen3.5-4B"
VISION_QUANT = "Q4_K_M"

_HF_QWEN = (
    "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/"
    "e87f176479d0855a907a41277aca2f8ee7a09523"
)
_HF_WHISPER = (
    "https://huggingface.co/Systran/faster-whisper-small/resolve/"
    "536b0662742c02347bc0e980a01041f333bce120"
)
_GH_LLAMA = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}"


@dataclass(frozen=True)
class RemoteFile:
    """One file, where it comes from, and exactly what it must be."""

    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Bundle:
    """Files that are used together, and what they are for."""

    id: str
    title: str
    licence: str
    files: tuple[RemoteFile, ...]
    #: For an archive, the executable's path inside what it unpacks to.
    binary: str | None = None

    @property
    def size(self) -> int:
        return sum(item.size for item in self.files)

    @property
    def is_archive(self) -> bool:
        return self.binary is not None


def _engine(platform: str, name: str, size: int, sha256: str, binary: str) -> Bundle:
    return Bundle(
        id=f"llama-server-{LLAMA_BUILD}-{platform}",
        title=f"llama.cpp {LLAMA_BUILD}, the program that runs the model",
        licence="MIT",
        files=(RemoteFile(name, f"{_GH_LLAMA}/{name}", size, sha256),),
        binary=binary,
    )


ENGINES: dict[str, Bundle] = {
    platforms.LINUX_X64: _engine(
        platforms.LINUX_X64,
        f"llama-{LLAMA_BUILD}-bin-ubuntu-x64.tar.gz",
        16844358,
        "1a0b042c434d443f046b3cf2f4add37ad851bccdce3525adf52c0204439eb494",
        f"llama-{LLAMA_BUILD}/llama-server",
    ),
    platforms.MACOS_ARM64: _engine(
        platforms.MACOS_ARM64,
        f"llama-{LLAMA_BUILD}-bin-macos-arm64.tar.gz",
        11152999,
        "89e45caad9c21351c7aff0dbbd5d303ded3a7a229ad28091e357ef2d0f27cf98",
        f"llama-{LLAMA_BUILD}/llama-server",
    ),
    platforms.MACOS_X64: _engine(
        platforms.MACOS_X64,
        f"llama-{LLAMA_BUILD}-bin-macos-x64.tar.gz",
        11200278,
        "4cca2906bf8b89374a1da3b781e1c5ae834561074e071febf480146ef1485e79",
        f"llama-{LLAMA_BUILD}/llama-server",
    ),
    platforms.WINDOWS_X64: _engine(
        platforms.WINDOWS_X64,
        f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip",
        18428728,
        "9670f79600780a9c67c5ae1ba665a00733bef6764f3a2c1e2341e2e2f3475b19",
        "llama-server.exe",
    ),
}

WEIGHTS = RemoteFile(
    "Qwen3.5-4B-Q4_K_M.gguf",
    f"{_HF_QWEN}/Qwen3.5-4B-Q4_K_M.gguf",
    2740937888,
    "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
)
#: The vision projector. Without it the weights load, answer text perfectly and
#: ignore every image — which is why it is a required file here and why the
#: vision probe runs after the first start.
PROJECTOR = RemoteFile(
    "mmproj-F16.gguf",
    f"{_HF_QWEN}/mmproj-F16.gguf",
    672423616,
    "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864",
)

VISION = Bundle(
    id="qwen3.5-4b-q4_k_m",
    title=f"{VISION_MODEL}, the model that reads documents",
    licence="Apache-2.0",
    files=(WEIGHTS, PROJECTOR),
)

SPEECH = Bundle(
    id="faster-whisper-small",
    title="Whisper small, the model that types up voice notes",
    licence="MIT",
    files=(
        RemoteFile(
            "model.bin",
            f"{_HF_WHISPER}/model.bin",
            483546902,
            "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        ),
        RemoteFile(
            "config.json",
            f"{_HF_WHISPER}/config.json",
            2370,
            "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
        ),
        RemoteFile(
            "tokenizer.json",
            f"{_HF_WHISPER}/tokenizer.json",
            2203239,
            "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        ),
        RemoteFile(
            "vocabulary.txt",
            f"{_HF_WHISPER}/vocabulary.txt",
            459861,
            "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
        ),
    ),
)

#: The identity the reader reports as ``model``. Carries the weights hash so the
#: string changes exactly when the bytes do — and with it the idempotency key,
#: so a re-pinned model is new work rather than silently the same work.
ALIAS = f"{VISION_MODEL.lower()}-{VISION_QUANT.lower()}@{WEIGHTS.sha256[:12]}"

#: Hosts a download may touch, including every redirect hop. Hugging Face serves
#: large files from its own CDN hosts and GitHub from its release-asset host;
#: nothing else is ever contacted.
ALLOWED_HOSTS = frozenset({"huggingface.co", "github.com"})
ALLOWED_SUFFIXES = (".hf.co", ".huggingface.co", ".githubusercontent.com")


def host_allowed(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    return host in ALLOWED_HOSTS or host.endswith(ALLOWED_SUFFIXES)


def engine_for(platform: str | None) -> Bundle | None:
    return ENGINES.get(platform) if platform else None


def reader_bundles(platform: str | None) -> tuple[Bundle, ...]:
    """What reading documents on this computer needs. Empty if unsupported."""
    engine = engine_for(platform)
    return (engine, VISION) if engine is not None else ()


def required(platform: str | None, reads_here: bool) -> tuple[Bundle, ...]:
    """Everything this machine should have, for the reader it chose.

    Speech is always here: voice notes are typed up on this machine whichever
    computer reads documents.
    """
    bundles: list[Bundle] = [SPEECH]
    if reads_here:
        bundles.extend(reader_bundles(platform))
    return tuple(bundles)


def hosts_contacted(bundles: tuple[Bundle, ...]) -> tuple[str, ...]:
    """The hosts named on the download screen, before anything is fetched."""
    names = []
    for bundle in bundles:
        for item in bundle.files:
            host = item.url.split("/")[2]
            if host not in names:
                names.append(host)
    return tuple(names)
