"""The reader's files: the manifest, the store, the download, the per-machine choice.

Nothing here touches the network. Downloads run against ``httpx.MockTransport``,
which also makes it possible to assert the one thing that matters most about
this module: which hosts were asked for anything at all.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import httpx
import pytest

from agent.errors import ConfigError, DownloadError
from agent.runtime import choice, download, manifest, platforms
from agent.runtime.manifest import Bundle, RemoteFile
from agent.runtime.store import Store


# --- the manifest ------------------------------------------------------------


def test_every_pinned_file_is_https_on_an_allowed_host_at_a_fixed_revision():
    bundles = (manifest.VISION, manifest.SPEECH, *manifest.ENGINES.values())
    for bundle in bundles:
        for item in bundle.files:
            host = httpx.URL(item.url).host
            assert item.url.startswith("https://"), item.url
            assert manifest.host_allowed(host), item.url
            assert "/main/" not in item.url and "latest" not in item.url
            assert len(item.sha256) == 64 and item.size > 0


def test_every_platform_has_an_engine_and_only_linux_claims_to_be_verified():
    assert set(manifest.ENGINES) == set(platforms.ALL)
    assert platforms.VERIFIED == {platforms.LINUX_X64}


def test_the_alias_changes_exactly_when_the_weights_do():
    assert manifest.WEIGHTS.sha256[:12] in manifest.ALIAS
    assert manifest.ALIAS.startswith("qwen3.5-4b-q4_k_m@")


def test_speech_is_always_required_and_the_reader_only_when_reading_here():
    here = manifest.required(platforms.LINUX_X64, reads_here=True)
    there = manifest.required(platforms.LINUX_X64, reads_here=False)
    assert manifest.SPEECH in here and manifest.SPEECH in there
    assert manifest.VISION in here and manifest.VISION not in there
    assert manifest.required(None, reads_here=True) == (manifest.SPEECH,)


@pytest.mark.parametrize(
    "host, allowed",
    [
        ("huggingface.co", True),
        ("cas-bridge.xethub.hf.co", True),
        ("release-assets.githubusercontent.com", True),
        ("github.com", True),
        ("evil-huggingface.co", False),
        ("huggingface.co.example.com", False),
        ("api.openai.com", False),
        (None, False),
    ],
)
def test_host_allowlist(host, allowed):
    assert manifest.host_allowed(host) is allowed


@pytest.mark.parametrize(
    "system, machine, expected",
    [
        ("Linux", "x86_64", platforms.LINUX_X64),
        ("Darwin", "arm64", platforms.MACOS_ARM64),
        ("Darwin", "x86_64", platforms.MACOS_X64),
        ("Windows", "AMD64", platforms.WINDOWS_X64),
        ("Linux", "aarch64", None),
    ],
)
def test_platform_keys(system, machine, expected):
    assert platforms.current(system, machine) == expected


# --- the store ---------------------------------------------------------------


def _file(name: str, data: bytes, host: str = "huggingface.co") -> RemoteFile:
    return RemoteFile(name, f"https://{host}/pinned/{name}", len(data), hashlib.sha256(data).hexdigest())


def test_a_data_directory_inside_the_vault_is_refused(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    with pytest.raises(DownloadError) as caught:
        Store(root=vault_root / ".models", vault_root=vault_root)
    assert caught.value.reason == "inside-vault"


def test_a_hand_placed_file_is_verified_like_a_download(tmp_path):
    data = b"weights" * 100
    item = _file("w.gguf", data)
    bundle = Bundle("b", "test", "MIT", (item,))
    store = Store(root=tmp_path / "data")
    path = store.path_for(bundle, item)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert store.is_verified(bundle, item)
    assert store.missing((bundle,)) == []

    # Same size, different bytes, and a changed mtime: the cache must not vouch.
    path.write_bytes(b"x" * len(data))
    assert not store.is_verified(bundle, item)


def test_an_archive_that_climbs_out_of_itself_is_not_unpacked(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("../escape")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"hi"))
    data = buffer.getvalue()
    item = _file("engine.tar.gz", data, host="github.com")
    bundle = Bundle("engine", "test", "MIT", (item,), binary="llama-server")
    store = Store(root=tmp_path / "data")
    path = store.path_for(bundle, item)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    with pytest.raises(DownloadError) as caught:
        store.unpack(bundle)
    assert caught.value.reason == "archive-layout"
    assert not (tmp_path / "data" / "escape").exists()
    assert store.binary(bundle) is None


def test_an_archive_unpacks_with_its_binary_executable(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("llama-b1/llama-server")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"#!/x"))
        link = tarfile.TarInfo("llama-b1/libx.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "libx.so.0"
        archive.addfile(link)
    data = buffer.getvalue()
    item = _file("engine.tar.gz", data, host="github.com")
    bundle = Bundle("engine", "test", "MIT", (item,), binary="llama-b1/llama-server")
    store = Store(root=tmp_path / "data")
    path = store.path_for(bundle, item)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert not store.is_ready((bundle,))
    store.prepare((bundle,))
    binary = store.binary(bundle)
    assert binary is not None and binary.stat().st_mode & 0o100
    assert store.is_ready((bundle,))


# --- the download ------------------------------------------------------------


class Server:
    """A mock transport serving pinned bytes, with ranges, redirects and a log."""

    def __init__(self, files: dict[str, bytes], redirect_to: str | None = None,
                 ignore_range: bool = False, corrupt: bool = False):
        self.files = files
        self.redirect_to = redirect_to
        self.ignore_range = ignore_range
        self.corrupt = corrupt
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        name = request.url.path.rsplit("/", 1)[-1]
        if self.redirect_to and request.url.host == "huggingface.co":
            return httpx.Response(302, headers={"location": f"https://{self.redirect_to}/x/{name}"})
        data = self.files[name]
        if self.corrupt:
            data = b"\0" * len(data)
        header = request.headers.get("range")
        if header and not self.ignore_range:
            start = int(header.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=data[start:])
        return httpx.Response(200, content=data)

    @property
    def hosts(self) -> set[str]:
        return {request.url.host for request in self.requests}


def _bundle(*files: tuple[str, bytes]) -> tuple[Bundle, dict[str, bytes]]:
    items = tuple(_file(name, data) for name, data in files)
    return Bundle("test-bundle", "test", "MIT", items), dict(files)


def test_download_follows_a_redirect_to_an_allowed_cdn_and_verifies(tmp_path):
    bundle, files = _bundle(("a.bin", b"a" * 5000), ("b.bin", b"b" * 3000))
    server = Server(files, redirect_to="cas-bridge.xethub.hf.co")
    store = Store(root=tmp_path / "data")
    downloader = download.Downloader(store, lambda: (bundle,), transport=httpx.MockTransport(server))
    downloader.run()
    assert store.is_ready((bundle,))
    assert server.hosts == {"huggingface.co", "cas-bridge.xethub.hf.co"}
    progress = downloader.progress()
    assert progress.state == download.COMPLETE
    assert progress.done_bytes == progress.total_bytes == 8000


def test_a_redirect_to_a_host_off_the_list_is_never_requested(tmp_path):
    bundle, files = _bundle(("a.bin", b"a" * 100))
    server = Server(files, redirect_to="collector.example.com")
    store = Store(root=tmp_path / "data")
    downloader = download.Downloader(store, lambda: (bundle,), transport=httpx.MockTransport(server))
    with pytest.raises(DownloadError) as caught:
        downloader.run()
    assert caught.value.reason == "host-not-allowed"
    assert "collector.example.com" not in server.hosts


def test_nothing_but_the_file_request_is_sent(tmp_path):
    bundle, files = _bundle(("a.bin", b"a" * 100))
    server = Server(files)
    store = Store(root=tmp_path / "data")
    download.Downloader(store, lambda: (bundle,), transport=httpx.MockTransport(server)).run()
    (request,) = server.requests
    assert request.method == "GET"
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert "health" not in request.headers.get("user-agent", "").lower()


def test_a_download_resumes_from_what_arrived(tmp_path):
    data = bytes(range(256)) * 40
    bundle, files = _bundle(("a.bin", data))
    store = Store(root=tmp_path / "data")
    (item,) = bundle.files
    part = store.part_for(bundle, item)
    part.parent.mkdir(parents=True)
    part.write_bytes(data[:4000])

    server = Server(files)
    download.Downloader(store, lambda: (bundle,), transport=httpx.MockTransport(server)).run()
    assert server.requests[0].headers["range"] == "bytes=4000-"
    assert store.path_for(bundle, item).read_bytes() == data


def test_a_server_that_ignores_the_range_restarts_rather_than_appending(tmp_path):
    data = bytes(range(256)) * 40
    bundle, files = _bundle(("a.bin", data))
    store = Store(root=tmp_path / "data")
    (item,) = bundle.files
    part = store.part_for(bundle, item)
    part.parent.mkdir(parents=True)
    part.write_bytes(data[:4000])

    server = Server(files, ignore_range=True)
    download.Downloader(store, lambda: (bundle,), transport=httpx.MockTransport(server)).run()
    assert store.path_for(bundle, item).read_bytes() == data


def test_wrong_bytes_are_deleted_and_never_used(tmp_path):
    bundle, files = _bundle(("a.bin", b"a" * 1000))
    server = Server(files, corrupt=True)
    store = Store(root=tmp_path / "data")
    downloader = download.Downloader(store, lambda: (bundle,), transport=httpx.MockTransport(server))
    with pytest.raises(DownloadError) as caught:
        downloader.run()
    assert caught.value.reason == "hash-mismatch"
    (item,) = bundle.files
    assert not store.part_for(bundle, item).exists()
    assert not store.path_for(bundle, item).exists()
    assert len(server.requests) == 1, "no retry loop"


def test_no_space_is_a_refusal_before_anything_is_fetched(tmp_path, monkeypatch):
    bundle, files = _bundle(("a.bin", b"a" * 1000))
    server = Server(files)
    monkeypatch.setattr(platforms, "free_disk_bytes", lambda path: 10)
    downloader = download.Downloader(Store(root=tmp_path / "data"), lambda: (bundle,),
                                     transport=httpx.MockTransport(server))
    with pytest.raises(DownloadError) as caught:
        downloader.run()
    assert caught.value.reason == "no-space"
    assert server.requests == []


def test_cancelling_keeps_what_arrived(tmp_path):
    data = b"z" * (3 * 1024 * 1024)
    bundle, files = _bundle(("a.bin", data))
    store = Store(root=tmp_path / "data")
    downloader = download.Downloader(store, lambda: (bundle,))

    def slow(request: httpx.Request) -> httpx.Response:
        downloader.cancel()
        return httpx.Response(200, content=data)

    downloader._transport = httpx.MockTransport(slow)
    with pytest.raises(DownloadError) as caught:
        downloader.run()
    assert caught.value.reason == "cancelled"
    (item,) = bundle.files
    assert store.part_for(bundle, item).exists()
    assert not store.path_for(bundle, item).exists()


# --- which computer reads documents --------------------------------------------


class _FakeVault:
    def __init__(self, raw, is_demo=False):
        self.config = type("C", (), {"raw": raw})()
        self.is_demo = is_demo


def test_a_vault_already_reading_on_a_box_starts_on_another_computer():
    vault = _FakeVault({"models": {"vlm": {"base_url": "http://127.0.0.1:1/v1"}}})
    assert choice.load(vault).reads_on == choice.ANOTHER_COMPUTER


def test_a_new_vault_starts_on_this_computer():
    assert choice.load(_FakeVault({})).reads_on == choice.THIS_COMPUTER


def test_the_default_is_written_once_so_a_synced_table_cannot_flip_it(isolated_env):
    fresh = _FakeVault({})
    settled = choice.settle(fresh)
    assert settled.reads_on == choice.THIS_COMPUTER and settled.source == "file"

    later = _FakeVault({"models": {"vlm": {"base_url": "http://127.0.0.1:1/v1"}}})
    assert choice.load(later).reads_on == choice.THIS_COMPUTER
    assert choice.path().is_relative_to(isolated_env), "beside the device identity"


def test_a_demo_vault_never_settles_a_choice():
    choice.settle(_FakeVault({}, is_demo=True))
    assert not choice.path().exists()


def test_the_choice_file_is_private_and_readable_without_the_app():
    choice.save(choice.ANOTHER_COMPUTER, 30)
    assert choice.path().stat().st_mode & 0o077 == 0
    assert json.loads(choice.path().read_text()) == {
        "reads_on": "another-computer", "sleep_after_minutes": 30,
        "model": manifest.DEFAULT_VISION_MODEL,
    }


def test_the_recommended_model_is_preselected_and_a_choice_is_remembered():
    assert choice.load(_FakeVault({})).model == manifest.QWEN_4B.id
    choice.save(choice.THIS_COMPUTER, 15, manifest.QWEN_9B.id)
    assert choice.load(_FakeVault({})).model == manifest.QWEN_9B.id


def test_a_model_this_version_does_not_offer_is_named_not_swapped():
    choice.path().parent.mkdir(parents=True, exist_ok=True)
    choice.path().write_text(json.dumps({"reads_on": "this-computer", "model": "qwen9000-q1"}))
    with pytest.raises(ConfigError) as caught:
        choice.load(_FakeVault({}))
    assert "qwen9000-q1" in str(caught.value)


def test_every_offered_model_is_pinned_and_one_is_recommended():
    assert sum(1 for model in manifest.VISION_MODELS if model.recommended) == 1
    aliases = {model.alias for model in manifest.VISION_MODELS}
    assert len(aliases) == len(manifest.VISION_MODELS)
    for model in manifest.VISION_MODELS:
        assert model.weights.sha256[:12] in model.alias
        assert model.ram_needed_bytes > 0
        for item in model.bundle.files:
            assert manifest.host_allowed(httpx.URL(item.url).host)
            assert len(item.sha256) == 64


@pytest.mark.parametrize("minutes", [-1, 24 * 60 + 1, True, 2.5, "15"])
def test_sleep_minutes_are_validated(minutes):
    with pytest.raises(ConfigError):
        choice.save(choice.THIS_COMPUTER, minutes)
