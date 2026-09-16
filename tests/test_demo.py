"""The demo vault: what it seeds, and the guardrails around seeding it.

The command exists so that the renderer's output can be read by hand before
phase 4 puts a model behind it, and so phases 7 and 8 have a record to build an
inbox and a consultation summary against. These tests hold it to that: every
shape the scenario is meant to demonstrate is asserted to be present, because a
demo that silently stops demonstrating a case is worse than no demo — someone
reads it, sees nothing wrong, and concludes the case works.

The guardrails get their own tests because the failure they prevent is seeding
invented medications into a folder holding a real record.
"""

from __future__ import annotations

import re

import pytest

from agent import demo as demo_mod
from agent.errors import HealthAgentError
from agent.projection import entities as entities_mod

#: Footnote targets look like ``→ `raw/2026/09/…` `` in the rendered pages.
_TARGET = re.compile(r"→ `([^`]+)`")


@pytest.fixture
def seeded(tmp_path):
    return demo_mod.seed(tmp_path / "demo-vault")


def test_it_seeds_a_readable_vault(seeded):
    root = seeded.root
    assert (root / demo_mod.MARKER_FILENAME).is_file()
    assert "invented" in (root / demo_mod.MARKER_FILENAME).read_text(encoding="utf-8")
    assert (root / "config.toml").is_file()
    assert seeded.artifacts == len(demo_mod.stream.ARTIFACTS)
    assert seeded.events > 20

    shards = sorted(p.name for p in (root / "events").glob("*.jsonl"))
    assert shards, "the log has to be on disk, not only in memory"
    assert all(demo_mod.DEMO_DEVICE in name for name in shards), (
        "the shard filename is one of the three places the vault says it is demo data"
    )


def test_every_citation_resolves_to_a_file_that_exists(seeded):
    """The point of writing real bytes into raw/ rather than faking the events.

    A citation pointing at a path that is not there is exactly what makes a
    hand-read of the folder useless, and it is invisible from inside the
    projection.
    """
    root = seeded.root
    targets = set()
    for page in (root / "wiki").rglob("*.md"):
        targets.update(_TARGET.findall(page.read_text(encoding="utf-8")))

    assert targets
    missing = sorted(target for target in targets if not (root / target).exists())
    assert missing == []


def test_it_covers_the_shapes_that_stress_the_renderer(seeded):
    entities = seeded.rebuild.projection.entities

    assert entities["med:perindopril"].status == entities_mod.ACTIVE
    assert entities["med:metformin"].status == entities_mod.STALE
    assert entities["med:amitriptyline"].status == entities_mod.STOPPED
    assert entities["med:atorvastatin"].status == entities_mod.CONFLICTED

    # Active *and* carrying a patient-reported stop: the discrepancy, not a status.
    sertraline = entities["med:sertraline"]
    assert sertraline.status == entities_mod.ACTIVE
    assert sertraline.stop_report is not None
    assert sertraline.stop_report.tier == "patient-reported"

    # A correction, so the history section has something in it.
    levothyroxine = entities["med:levothyroxine"]
    assert levothyroxine.slots["dose"].winner.value.literal == "50mcg daily"
    assert [c.value.literal for c in levothyroxine.slots["dose"].superseded] == [
        "5Omcg daily"
    ]

    assert "allergy:penicillin" in entities
    assert "problem:hypertension" in entities
    assert "person:dr-nguyen" in entities


def test_it_leaves_something_in_the_review_queue_and_the_report(seeded):
    """Phases 7 and 8 need a queue that is not empty, and one item per tier."""
    projection = seeded.rebuild.projection
    tiers = projection.review_by_tier
    assert "high" in tiers and "medium" in tiers

    # Subject-less, so it belongs in the report rather than the wiki.
    assert any("names no target claim" in note for note in projection.anomalies)
    assert all(note.subject_id is None or note.subject_id for note in projection.anomalies)


def test_the_rejected_reading_reaches_no_page(seeded):
    """Seeded rather than only unit-tested, because reading the folder by hand is
    how someone would notice it had leaked."""
    for page in (seeded.root / "wiki").rglob("*.md"):
        assert "alcohol" not in page.read_text(encoding="utf-8").lower()
    assert "problem:alcohol-dependence" not in seeded.rebuild.projection.entities


def test_the_timeline_spans_several_months(seeded):
    months = sorted(p.stem for p in (seeded.root / "wiki" / "timeline").glob("*.md"))
    assert len(months) >= 3


def test_ingest_time_is_now_and_document_dates_are_not(seeded):
    """The four timestamps, kept apart in the one place people will read them.

    The bytes really did enter the vault today, so ``ingested_ts`` says today.
    Backdating it to make the demo look tidier would be exactly the substitution
    the four-timestamp rule exists to prevent.
    """
    ingested = [
        event
        for event in seeded.rebuild.projection.reconciliation.admissions
        if event.claim.artifact
    ]
    assert ingested
    for admission in ingested:
        claim = admission.claim
        assert claim.ingested_ts is not None
        if claim.artifact_ts is not None:
            assert claim.artifact_ts < claim.ingested_ts, (
                "a document predates the day its photograph was filed"
            )


def test_it_refuses_a_folder_that_is_not_empty(tmp_path):
    root = tmp_path / "real-vault"
    root.mkdir()
    (root / "config.toml").write_text("port = 7777\n", encoding="utf-8")

    with pytest.raises(HealthAgentError) as raised:
        demo_mod.seed(root)
    assert "not empty" in str(raised.value)
    # And it wrote nothing on the way to refusing.
    assert sorted(p.name for p in root.iterdir()) == ["config.toml"]


def test_it_refuses_a_path_that_is_a_file(tmp_path):
    target = tmp_path / "not-a-folder"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(HealthAgentError):
        demo_mod.seed(target)


def test_seeding_does_not_mint_this_machines_device_identity(tmp_path, isolated_env):
    """The demo runs on someone's laptop. It must not touch their real identity."""
    demo_mod.seed(tmp_path / "demo-vault")
    assert not (isolated_env / "device").exists()


def test_the_cli_reports_a_refusal_as_a_message_not_a_traceback(tmp_path, capsys):
    """Every refusal in this program is written to be read.

    A traceback in front of the sentence that says what to do hides it.
    """
    from agent import cli

    root = tmp_path / "occupied"
    root.mkdir()
    (root / "config.toml").write_text("port = 7777\n", encoding="utf-8")

    code = cli.main(["demo", str(root)])
    out = capsys.readouterr().out

    assert code == cli.EXIT_PROBLEMS
    assert out.startswith("error: ")
    assert "only ever seeds an empty folder" in out


def test_the_cli_seeds_and_points_at_the_marker(tmp_path, capsys):
    from agent import cli

    root = tmp_path / "demo-vault"
    code = cli.main(["demo", str(root)])
    out = capsys.readouterr().out

    assert code == cli.EXIT_OK
    assert "This is invented data" in out
    assert str(root / "wiki") in out


# --- the artefacts are real files ------------------------------------------
#
# They used to be format headers followed by padding. Citations resolved, which
# was all they were for, and the consequence was that nothing downstream of
# ingest could be exercised against a demo vault at all: nine artefacts, every
# one of them unreadable.


def test_every_artefact_is_a_file_its_own_reader_can_open(seeded):
    """The whole point of rendering real documents rather than headers.

    Asserted through `prepare_artifact`, the same call extraction makes, rather
    than by opening the files here — a file Pillow can open and the pipeline
    rejects is not a fixed demo.
    """
    from agent.extract import images
    from agent.projection import citations

    vault = _vault(seeded)
    artifacts = citations.index_artifacts(list(vault.read().events))
    assert len(artifacts) == len(demo_mod.stream.ARTIFACTS)

    unreadable = {}
    for short, artifact in artifacts.items():
        path = vault.raw.resolve_recorded(artifact.rel)
        document = images.prepare_artifact(path, artifact.mime, long_edge=1280)
        if not document.is_readable:
            unreadable[short] = (artifact.mime, document.unreadable)
            continue
        assert document.pages, f"{short} produced no pages"

    # Exactly one: the recording, which the speech model reads and the vision
    # model correctly declines. Anything else here is a broken document.
    assert [mime for mime, _ in unreadable.values()] == ["audio/wav"]


def test_the_pdfs_carry_a_text_layer_for_the_deterministic_reader(seeded):
    """Dual-path extraction needs something to cross-check against.

    Pathology reports from patient portals almost always have a text layer, and
    the demo's do, so the `cross-verified` path is reachable without tesseract
    being installed.
    """
    pytest.importorskip("pdfplumber")
    from agent.extract import text
    from agent.projection import citations

    vault = _vault(seeded)
    pdfs = [
        artifact
        for artifact in citations.index_artifacts(list(vault.read().events)).values()
        if artifact.mime == "application/pdf"
    ]
    assert pdfs, "the scenario cites PDFs"
    for artifact in pdfs:
        read = text.read(vault.raw.resolve_recorded(artifact.rel), artifact.mime)
        assert read.method == text.METHOD_TEXT_LAYER
        assert "ROSEWOOD" in read.text


def test_the_documents_say_what_the_seeded_claims_say_they_say(seeded):
    """A demo whose documents and event stream drift apart is worse than one
    with no documents: it teaches the reader a wrong answer and looks right."""
    pytest.importorskip("pdfplumber")
    from agent.extract import text
    from agent.projection import citations

    vault = _vault(seeded)
    artifacts = citations.index_artifacts(list(vault.read().events))
    entity = seeded.rebuild.projection.entities["med:sertraline"]
    source = next(
        artifacts[short] for short in entity.sources if artifacts[short].mime == "application/pdf"
    )
    read = text.read(vault.raw.resolve_recorded(source.rel), source.mime)

    assert "Sertraline 50 mg" in read.text
    assert "30 tablets, 1 repeat" in read.text


def test_the_voice_note_is_audio_something_can_decode(seeded):
    """Not speech — nothing here synthesises a voice — but a real container.

    A tone rather than silence, so "the audio never decoded" and "the audio
    decoded and was empty" cannot look the same to whoever runs phase 6 against
    this vault.
    """
    import wave

    from agent.projection import citations

    vault = _vault(seeded)
    recording = next(
        artifact
        for artifact in citations.index_artifacts(list(vault.read().events)).values()
        if artifact.mime.startswith("audio/")
    )
    with wave.open(str(vault.raw.resolve_recorded(recording.rel)), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == demo_mod.documents.WAV_SAMPLE_RATE
        assert handle.getnframes() > 0
        frames = handle.readframes(handle.getnframes())
    assert frames.strip(b"\x00"), "silence would be indistinguishable from a failed decode"


def test_the_marker_says_the_audio_is_not_speech(seeded):
    """An honest gap can be worked around; a silent one reads as a bug."""
    marker = (seeded.root / demo_mod.MARKER_FILENAME).read_text(encoding="utf-8")
    assert "tone rather than speech" in marker


# --- a demo vault carries no inference endpoint ----------------------------


def test_the_demo_config_names_no_endpoint(seeded):
    """It used to carry the template's placeholder MagicDNS host, so `extract`
    against a demo vault failed with a DNS error indistinguishable from the
    user's own box being asleep — a wrong answer to "is my endpoint working",
    produced by a folder of invented data."""
    import tomllib

    data = tomllib.loads((seeded.root / "config.toml").read_text(encoding="utf-8"))

    assert "vlm" not in data.get("models", {})
    assert "tailnet.ts.net" not in (seeded.root / "config.toml").read_text(encoding="utf-8")
    # Speech is local, so it needs no endpoint and stays configured.
    assert data["models"]["asr"]["name"] == "faster-whisper-small"


def test_extract_against_a_demo_vault_says_demo_vaults_have_no_endpoint(seeded, capsys):
    """On a machine that reads on another computer, a demo still carries no box."""
    from agent import cli
    from agent.runtime import choice

    choice.save(choice.ANOTHER_COMPUTER)

    code = cli.main(["extract", "--vault", str(seeded.root)])
    out = capsys.readouterr().out

    assert code == cli.EXIT_PROBLEMS
    assert "no inference endpoint configured" in out
    assert "demo vaults don't carry one" in out
    assert "Point --vault at your real vault" in out


def test_a_refused_demo_vault_gets_no_queue_written(seeded):
    """The endpoint is checked before the queue is filled. Writing nine job
    lines into a vault and then refusing to run them is work recorded for a run
    that could never have happened."""
    from agent import cli

    cli.main(["extract", "--vault", str(seeded.root)])
    assert not (seeded.root / ".agent" / "jobs.jsonl").exists()


def test_a_demo_vault_reads_on_this_computer_like_any_vault(seeded, capsys):
    """No demo exception. The reader's files are the machine's, and a demo plus
    a local model is the one way to watch extraction work end to end without a
    personal document anywhere. Here nothing is downloaded, so it says that —
    and fetches nothing, because a download is only ever started by asking."""
    from agent import cli
    from agent.runtime import states

    code = cli.main(["extract", "--vault", str(seeded.root)])
    out = capsys.readouterr().out

    assert code == cli.EXIT_PROBLEMS
    assert states.MESSAGES["not-downloaded"] in out
    assert "demo vaults don't carry one" not in out


def _vault(seeded):
    from agent.vault import Vault

    return Vault.open(seeded.root)


# --- --endpoint-from -------------------------------------------------------
#
# A demo vault reaches no model by default. `--endpoint-from` copies one across
# so extraction can be exercised against the demo, and copies *only*
# [models.vlm] and [models.vlm.auth]: a scratch folder must not inherit a
# sync_profile, a port, a locale, or a speech model from somebody's real vault.

REAL_CONFIG = """\
sync_profile = "dropbox"
port = 8123
locale = "en-au"

[models.vlm]
base_url   = "https://macbook-pro.tailb017fc.ts.net/v1"
model      = "Jundot/Qwen3.8-Flash-Next-oQ4e-mtp"
ctx        = 8192
temperature       = 0.0
thinking          = false

[models.vlm.auth]
api_key_env = "HEALTH_VLM_TOKEN"
header      = "X-API-Key"
scheme      = "Token"

[models.asr]
name         = "faster-whisper-medium"
compute_type = "float16"
"""


@pytest.fixture
def real_config(tmp_path):
    path = tmp_path / "real" / "config.toml"
    path.parent.mkdir()
    path.write_text(REAL_CONFIG, encoding="utf-8")
    return path


def _seeded_config(root):
    import tomllib

    return tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))


def test_only_the_two_endpoint_tables_are_copied(tmp_path, real_config):
    """Everything else in the source file stays in the source file."""
    report = demo_mod.seed(tmp_path / "demo", endpoint_from=real_config)
    data = _seeded_config(report.root)

    assert data["models"]["vlm"]["model"] == "Jundot/Qwen3.8-Flash-Next-oQ4e-mtp"
    assert data["models"]["vlm"]["base_url"].endswith("ts.net/v1")
    assert data["models"]["vlm"]["ctx"] == 8192
    assert data["models"]["vlm"]["auth"]["header"] == "X-API-Key"

    # A scratch folder is not on anybody's Dropbox, and a demo vault claiming to
    # be would have the scan reporting sync forks that cannot exist.
    assert data["sync_profile"] == "local"
    assert data["port"] == 7777
    assert data["locale"] == "en"
    # Speech is the demo's own: it runs locally and needs nothing from the source.
    assert data["models"]["asr"]["name"] == "faster-whisper-small"


def test_the_copied_endpoint_is_one_the_inference_layer_can_open(tmp_path, real_config):
    """The copy is validated before it is written, so a demo vault is never
    seeded with a table that fails to load later — where the error would be
    about the demo rather than about the file it came from."""
    from agent.extract import session
    from agent.vault import Vault

    report = demo_mod.seed(tmp_path / "demo", endpoint_from=real_config)
    settings = session.settings_for(Vault.open(report.root))

    assert settings.vlm.model == "Jundot/Qwen3.8-Flash-Next-oQ4e-mtp"
    assert settings.vlm.auth.header == "X-API-Key"
    assert settings.vlm.ctx == 8192


def test_the_default_is_still_no_endpoint(seeded):
    assert seeded.endpoint is None
    assert "vlm" not in _seeded_config(seeded.root).get("models", {})


def test_the_seeded_config_names_the_file_it_copied_from(tmp_path, real_config):
    """The run that made the vault scrolls away; the vault stays."""
    report = demo_mod.seed(tmp_path / "demo", endpoint_from=real_config)
    config = (report.root / "config.toml").read_text(encoding="utf-8")

    assert str(real_config) in config
    assert "copied by `demo --endpoint-from`" in config


def test_the_marker_says_the_demo_vault_can_reach_a_real_box(tmp_path, real_config):
    """A folder of invented data that talks to a real endpoint is a thing the
    person who made it has to have been told, in the folder and not only once."""
    report = demo_mod.seed(tmp_path / "demo", endpoint_from=real_config)
    marker = (report.root / demo_mod.MARKER_FILENAME).read_text(encoding="utf-8")

    assert "This vault has a real inference endpoint" in marker
    assert str(real_config) in marker


def test_a_credential_in_the_copied_table_refuses_before_anything_is_written(
    tmp_path, real_config
):
    real_config.write_text(
        REAL_CONFIG.replace(
            'api_key_env = "HEALTH_VLM_TOKEN"', 'api_key = "sk-live-0123456789"'
        ),
        encoding="utf-8",
    )
    root = tmp_path / "demo"

    with pytest.raises(HealthAgentError) as raised:
        demo_mod.seed(root, endpoint_from=real_config)

    assert "models.vlm.auth.api_key" in str(raised.value)
    assert "in the very table this would copy" in str(raised.value)
    assert not root.exists(), "a half-seeded folder is one somebody has to reason about"


def test_a_credential_elsewhere_in_the_source_refuses_too(tmp_path, real_config):
    """Scoping the copy already stops the key reaching the demo vault. Refusing
    anyway is consistent with `config.parse`, which will not load that file at
    all — and this is the moment someone is looking at it."""
    real_config.write_text(
        REAL_CONFIG + '\n[extra]\napi_token = "sk-live-9876543210"\n', encoding="utf-8"
    )
    root = tmp_path / "demo"

    with pytest.raises(HealthAgentError) as raised:
        demo_mod.seed(root, endpoint_from=real_config)

    assert "outside the table this would copy" in str(raised.value)
    assert "already disclosed" in str(raised.value)
    assert not root.exists()


def test_a_source_with_no_endpoint_says_so_and_names_the_file(tmp_path):
    source = tmp_path / "plain.toml"
    source.write_text("port = 7777\n", encoding="utf-8")
    root = tmp_path / "demo"

    with pytest.raises(HealthAgentError) as raised:
        demo_mod.seed(root, endpoint_from=source)

    assert "has no [models.vlm] table" in str(raised.value)
    assert str(source) in str(raised.value)
    assert not root.exists()


def test_a_source_that_is_not_there_says_what_the_flag_takes(tmp_path):
    with pytest.raises(HealthAgentError) as raised:
        demo_mod.seed(tmp_path / "demo", endpoint_from=tmp_path / "nowhere.toml")

    assert "--endpoint-from takes the path to a config.toml" in str(raised.value)


def test_a_vault_root_resolves_to_the_config_inside_it(tmp_path, real_config):
    """`--endpoint-from ~/health` is what people will type."""
    report = demo_mod.seed(tmp_path / "demo", endpoint_from=real_config.parent)

    assert report.endpoint is not None
    assert report.endpoint.source == real_config


def test_a_non_scalar_under_the_endpoint_table_is_dropped_and_reported(
    tmp_path, real_config
):
    """Silently dropping part of a copied endpoint would make the demo differ
    from the real one in a way nobody was told about."""
    real_config.write_text(
        REAL_CONFIG.replace("ctx        = 8192", 'ctx = 8192\nhosts = ["a", "b"]'),
        encoding="utf-8",
    )
    report = demo_mod.seed(tmp_path / "demo", endpoint_from=real_config)

    assert report.endpoint.dropped == ("models.vlm.hosts",)
    assert "hosts" not in _seeded_config(report.root)["models"]["vlm"]


def test_the_cli_names_the_source_it_copied_from(tmp_path, real_config, capsys):
    from agent import cli

    code = cli.main(
        ["demo", str(tmp_path / "demo"), "--endpoint-from", str(real_config)]
    )
    out = capsys.readouterr().out

    assert code == cli.EXIT_OK
    assert f"endpoint  [models.vlm] copied from {real_config}" in out
    assert "no credential, no sync_profile" in out


def test_the_cli_says_so_when_there_is_no_endpoint(tmp_path, capsys):
    from agent import cli

    cli.main(["demo", str(tmp_path / "demo")])
    out = capsys.readouterr().out

    assert "reads on  Read on this computer — this machine's choice" in out
    assert "nothing is fetched by making a demo" in out


def test_a_table_that_would_not_load_is_refused_at_the_source(tmp_path, real_config):
    """Validated before it is written. A demo vault seeded with a broken
    endpoint fails later, where the message is about the demo rather than about
    the file the table came from."""
    real_config.write_text(
        REAL_CONFIG.replace("ctx        = 8192", "ctx        = 99999999"),
        encoding="utf-8",
    )
    root = tmp_path / "demo"

    with pytest.raises(HealthAgentError) as raised:
        demo_mod.seed(root, endpoint_from=real_config)

    assert "does not load" in str(raised.value)
    assert str(real_config) in str(raised.value)
    assert not root.exists()


def test_the_demo_demonstrates_the_salt_variant_case(seeded):
    """The labels the demo renders carry salt names — PERINDOPRIL ARGININE,
    METFORMIN HYDROCHLORIDE, LEVOTHYROXINE SODIUM — so the claims seeded against
    them do too, and reading the folder by hand shows one entry per drug rather
    than one per label."""
    entities = seeded.rebuild.projection.entities

    for base in ("med:perindopril", "med:metformin", "med:levothyroxine"):
        assert base in entities, f"{base} is the entry the salt variants file under"
        assert entities[base].salt_names, f"{base} does not disclose its label wording"
    for variant in (
        "med:perindopril-arginine",
        "med:metformin-hydrochloride",
        "med:levothyroxine-sodium",
    ):
        assert variant not in entities, "a table alias gets no page of its own"

    page = (seeded.root / "wiki" / "medications" / "perindopril.md").read_text("utf-8")
    assert "also_labelled: [Perindopril Arginine]" in page
    assert "## Names on sources" in page
