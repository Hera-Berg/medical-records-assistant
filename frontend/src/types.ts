/**
 * The shapes the API sends.
 *
 * Written out rather than inferred, because several of them encode a rule and
 * the type is where that rule is easiest to state. `captured_ts` is
 * `string | null` and not optional: an unknown timestamp is an explicit null,
 * never an absent field that some caller might fill in from a neighbour.
 */

export type Tier =
  | "prescriber-issued"
  | "lab-issued"
  | "device-recorded"
  | "patient-reported"
  | "inferred"
  | "artefact";

export type Status = "active" | "stale" | "stopped" | "conflicted";

export type DateKind = "occurred" | "document" | "captured" | "recorded";

export interface FuzzyDate {
  iso: string;
  precision: "day" | "month" | "year";
  uncertainty_days: number;
  exact: boolean;
  render: string;
  band: { start: string; end: string };
  coercions: string[];
}

export interface Citation {
  key: string;
  text: string;
  target: string | null;
  resolved: boolean;
  artifact: string | null;
  url: string | null;
}

export interface Claim {
  event_id: string;
  ts: string;
  device: string;
  kind: string;
  is_correction: boolean;
  subject: string;
  /** What the label itself said, beside the id it was filed under. */
  subject_literal: string;
  salt: string | null;
  predicate: string;
  value: { literal: string; key: string; fields: Record<string, string> };
  evidence_tier: Tier;
  consequence: "high" | "medium" | "low";
  declared_consequence: string | null;
  confidence: number | null;
  occurred_at: FuzzyDate | null;
  /** Kept verbatim where a source said *when* in words. Never resolved here. */
  occurred_span: string | null;
  artifact_ts: string | null;
  captured_ts: string | null;
  ingested_ts: string | null;
  artifact: string | null;
  citation: Citation;
}

export interface Slot {
  subject_id: string;
  predicate: string;
  consequence: string;
  resolution: string;
  review_state: string;
  conflicted: boolean;
  has_value: boolean;
  winner: Claim | null;
  readings: Claim[];
  superseded: Claim[];
  contradicted_by: Claim[];
  endorsed: string[];
  /** Count and sources only. A gated value is never sent — see the API. */
  pending: { count: number; citations: Citation[] };
}

export interface ReviewItem {
  kind: string;
  consequence: "high" | "medium" | "low";
  subject_id: string;
  predicate: string;
  summary: string;
  citations: Citation[];
}

/**
 * One queue entry as the inbox needs it, values included.
 *
 * The only shape in this file that carries a *proposed* value. Everywhere else
 * a gated value is withheld, because a value shown beside a current one reads
 * as current; here the diff is the point of the screen, and a queue that asked
 * for a tap without showing what it agrees to would be asking someone to sign
 * an unread page.
 *
 * A withdrawal is the exception within the exception: it carries no claims at
 * all, so `proposed` is null and nothing on screen can reproduce what was
 * retracted.
 */
export interface InboxItem {
  id: string;
  kind: string;
  consequence: "high" | "medium" | "low";
  subject_id: string;
  name: string;
  predicate: string;
  predicate_label: string;
  summary: string;
  /** What may be done to this item. The server refuses anything else. */
  actions: string[];
  /** How many claims one tap decides. Two documents agreeing are one item. */
  sources_folded: number;
  targets: string[];
  proposed: Claim | null;
  /** Both sides of a disagreement. Never one picked, never averaged. */
  readings: Claim[];
  current: Claim | null;
  resolution: string | null;
  citations: Citation[];
  /** For a stop: whether confirming takes the medication off the list. */
  stop: { transitions: boolean; evidence_tier: Tier | null } | null;
  dateable: {
    span: string | null;
    reference: string;
    candidates: {
      label: string;
      reason: string;
      rendered: string;
      occurred_at: { value: string; precision: string; uncertainty_days: number };
    }[];
  } | null;
}

export interface ReviewQueue {
  counts: { total: number; by_tier: Record<string, number>; by_kind: Record<string, number> };
  tiers: { high: InboxItem[]; medium: InboxItem[]; low: InboxItem[] };
  actionable: boolean;
  anomalies: { count: number; items: string[] };
  as_of: string;
  /** Present on the answer to a decision, including one that came too late. */
  decided?: {
    id: string;
    action?: string;
    kind?: string;
    subject_id?: string;
    name?: string;
    events?: string[];
    claims_decided?: number;
    message: string;
  };
}

export interface EntitySummary {
  id: string;
  kind: "med" | "allergy" | "problem" | "person";
  slug: string;
  name: string;
  status: Status;
  stale: boolean;
  conflicted: boolean;
  is_stub: boolean;
  merged_into: string | null;
  evidence_tier: Tier | null;
  dose: string | null;
  last_confirmed: string | null;
  /** "8 months ago", computed against the projection's as_of, not the browser's clock. */
  last_confirmed_ago: string | null;
  expected_exhaustion: string | null;
  started: string | null;
  stop_reported: string | null;
  stop_reported_tier: string | null;
  review_count: number;
  anomaly_count: number;
  sources: string[];
  path: string;
}

export interface EntityDetail extends EntitySummary {
  slots: Slot[];
  conflicts: { predicate: string; readings: Claim[] }[];
  review: ReviewItem[];
  anomalies: { text: string; citation: Citation | null }[];
  salt_names: {
    literal: string;
    salt: string;
    subject_id: string;
    citation: Citation;
  }[];
  merged_from: string[];
  dispense: {
    quantity: string | null;
    frequency: string | null;
    repeats: string | null;
    dose_units: string | null;
    days_supply: number | null;
    unreadable: string | null;
    /** Whether a source actually stated a supply, as opposed to only a dose. */
    has_spans: boolean;
  } | null;
  stop_report: { tier: string; when: FuzzyDate | null; citation: Citation } | null;
  markdown: string | null;
  timeline: TimelineRow[];
  as_of: string;
}

export interface TimelineRow {
  event_id: string;
  subject_id: string | null;
  marker: Tier;
  text: string;
  date: FuzzyDate;
  date_kind: DateKind;
  date_label: string;
  heading: string;
  month: string;
  citation: Citation;
  /** Present on artefact rows only. A claim is not waiting to be read. */
  reading: Reading | null;
}

export interface MedicationRow {
  id: string;
  name: string;
  status: Status;
  dose: string | null;
  evidence_tier: Tier | null;
  last_confirmed: string | null;
  last_confirmed_ago: string | null;
  expected_exhaustion: string | null;
  stale: boolean;
  conflicted: boolean;
  stop_reported: string | null;
  stop_reported_tier: string | null;
  sources: string[];
  /** Every distinct dose the sources state, when they disagree. Empty otherwise. */
  dose_readings: string[];
}

export type EndpointState =
  | "working"
  | "unreachable"
  | "unauthorised"
  | "misconfigured"
  | "vision-not-working"
  | "not-configured"
  | "unknown";

export interface Health {
  ok: boolean;
  vault: {
    root: string;
    writable: boolean;
    sync_profile: string;
    demo: boolean;
    device: { id: string | null; label?: string; appendable: boolean; reason: string | null };
  };
  endpoint: {
    state: EndpointState;
    /** `ok | failed | missing`, and nothing more. Never the key. */
    auth: "ok" | "failed" | "missing";
    reason: string;
    message: string;
    model: string | null;
    checked_ts: string | null;
  };
  queue: {
    depth: number;
    parked: boolean;
    blocked_auth: number;
    counts: Record<string, number>;
    malformed: string[];
  };
  record: { events: number; artifacts: number; entities: number; as_of: string };
  review: { total: number; by_tier: { high: number; medium: number; low: number } };
  anomalies: { count: number; items: string[] };
  problems: string[];
}

export interface WikiIndex {
  kinds: Record<string, EntitySummary[]>;
  current_medications: MedicationRow[];
  review: { total: number; by_tier: Record<string, number>; by_kind: Record<string, number> };
  anomalies: number;
  as_of: string;
}

export interface Timeline {
  total: number;
  offset: number;
  limit: number;
  rows: TimelineRow[];
  months: string[];
  as_of: string;
}

/** One word and its seconds. What lets a citation point at four seconds of audio. */
export interface TranscriptWord {
  start: number;
  end: number;
  word: string;
  probability: number;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  words: TranscriptWord[];
}

/**
 * What the speech model made of a recording.
 *
 * `dropped` is a count and a set of reasons, never text. Discarded segments are
 * the model's hallucinations over silence — they are kept in the event log as
 * provenance, and putting them on a screen beside a real transcript would print
 * invented sentences next to true ones.
 */
export interface Transcript {
  event: string;
  ts: string;
  model: string | null;
  model_rev: string | null;
  text: string;
  language: string | null;
  duration_s: number | null;
  segments: TranscriptSegment[];
  dropped: number;
  dropped_reasons: Record<string, number>;
  hotwords: string[];
  supersedes: string | null;
}

/**
 * Where an artefact has got to, and what happens to it next.
 *
 * `text` is what a screen shows and may carry a live reason — "the computer
 * that reads your files is asleep". `recorded_text` is the shorter sentence
 * written into `wiki/`, which cannot mention the queue: the queue is a
 * disposable cache and a generated file that named it would stop rebuilding to
 * the same bytes. Both are carried so the two can be seen to agree.
 */
export interface Reading {
  state: "not-read" | "transcribed" | "read" | "nothing-found" | "unreadable";
  text: string;
  recorded_text: string;
  claims: number;
  awaiting: number;
  finished: boolean;
  /** True where nothing in this build will read it further. Phase 6: transcripts. */
  deferred: boolean;
  job?: string | null;
}

/** Where an artefact has got to in the queue. Null once nothing is tracking it. */
export interface JobState {
  state: string;
  reason: string | null;
  attempts: number;
}

export interface ArtifactMeta {
  short: string;
  digest: string;
  path: string;
  mime: string;
  source: string | null;
  ingested_ts: string | null;
  captured_ts: string | null;
  artifact_ts: string | null;
  reseen_count: number;
  url: string;
  present: boolean;
  bytes: number | null;
  renders_inline: boolean;
  sidecar?: Record<string, unknown>;
  citation: Citation;
  /** Null until the speech model has run. Present and empty means it ran and
   *  found no speech, which is a different thing and is said differently. */
  transcript: Transcript | null;
  is_recording: boolean;
  job: JobState | null;
  reading: Reading;
}

/** One storage-profile option, with what choosing it would actually change. */
export interface SyncOption {
  value: string;
  label: string;
  effects: string[];
  warning: string | null;
  current: boolean;
}

/**
 * Where a key was found, and never what it is.
 *
 * `source` is a *place* — "keychain", "environment (HEALTH_VLM_TOKEN)". There
 * is no field here for the key, no field for a prefix of it and no field for
 * its length, and there is no route that would fill one in.
 */
export interface KeyState {
  state: "configured" | "not set" | "unusable";
  source: string | null;
  detail: string | null;
}

export interface EndpointSettings {
  explanation: string;
  configured: boolean;
  base_url: string;
  model: string;
  auth: { header: string; scheme: string; api_key_env: string | null };
  defaults: { header: string; scheme: string };
  key: KeyState;
  key_explanation: string;
  last_known: Health["endpoint"];
  /** Set when `[models.vlm]` is present but will not load. */
  problem: string | null;
}

/**
 * One step of the connection test.
 *
 * `detail` is written by the server from a fixed table and selected by a code.
 * Nothing the inference box said is ever interpolated into it — the same rule
 * `/api/health` follows, because httpx puts request headers into some error
 * representations and a short key would pass through the redaction filter.
 */
export interface EndpointStep {
  name: string;
  title: string;
  state: "ok" | "failed" | "not-checked";
  detail: string;
}

export interface EndpointCheck {
  state: EndpointState;
  ok: boolean;
  auth: "ok" | "failed" | "missing";
  model_reported: string | null;
  message: string;
  steps: EndpointStep[];
  settings?: Settings;
}

export interface EndpointModels {
  models: string[];
  reached: boolean;
  state: EndpointState;
  message: string;
}

export interface Settings {
  explanation: string;
  sync_profile: {
    current: string;
    options: SyncOption[];
    warning: string | null;
  };
  config: {
    path: string;
    writable: boolean;
    conflict_forks: string[];
  };
  endpoint: EndpointSettings;
  vault: { root: string; demo: boolean };
  changed?: string;
  conflicts?: string[];
  /** On the answer to storing a key: where it went, and how much work resumed. */
  stored?: string;
  resumed?: number;
}

export interface CaptureResult {
  filename: string | null;
  status: "stored" | "reseen" | "restored" | "failed";
  short?: string;
  path?: string;
  mime?: string;
  bytes?: number;
  error?: string;
  url?: string;
}

export interface CaptureResponse {
  accepted: number;
  failed: number;
  queued: string[];
  queue_depth: number;
  results: CaptureResult[];
  note: string;
}

/**
 * The folder, as the browser screen sees it.
 *
 * `kind` is what a path *is* — the log, an original, a sidecar, something
 * derived, something of yours, the setup file — and it is what decides whether
 * the delete button exists. `refusal` carries the sentence to show when it does
 * not, so the screen never has to invent a reason for the server's answer.
 */
export type FileKind = "log" | "raw" | "sidecar" | "derived" | "yours" | "config";

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  kind: FileKind;
  what: string;
  bytes: number | null;
  modified: string | null;
  children: number | null;
  text: boolean;
  /** The short hash, when this file is an artefact the record cites. */
  artifact: string | null;
  claims: number | null;
  deletable: boolean;
  refusal: string | null;
}

export interface FileListing {
  path: string;
  root: string;
  folder: string | null;
  crumbs: { label: string; path: string }[];
  /** Set when the path is a file rather than a folder. */
  entry: FileEntry | null;
  entries: FileEntry[];
}

export interface FileContent {
  path: string;
  text: string;
  truncated: boolean;
  bytes: number;
  shown: number;
}

/**
 * The consultation sheet.
 *
 * Every field here is computed by the server. Nothing on this screen decides
 * what appears on the sheet, in what order, or whether it fits on a page —
 * that is all code in `agent/summary/`, and a second selection rule living in
 * the browser is how the printed sheet and the screen would come to disagree
 * about what the patient is taking.
 */
export interface SummarySource {
  tier: Tier | null;
  tier_word: string;
  when: string | null;
  /** "Prescription · 4 June 2026" — the citation as a clinician can use it. */
  text: string;
  artifact: string | null;
  rel: string | null;
  citation: Citation;
}

export interface SummaryLine {
  label: string;
  value: string;
  value_text: string;
  source_text: string;
  note: string | null;
  /** "Needs confirming", "Sources disagree", "Not confirmed by me". A word, always. */
  state: string | null;
  subject_id: string | null;
  /** The other reading, where two sources disagree and neither was chosen. */
  alternatives: string[];
  claims: string[];
  sources: SummarySource[];
}

export interface SummarySection {
  key: string;
  heading: string;
  subnote: string;
  lines: SummaryLine[];
  omitted: number;
  omitted_note: string | null;
  empty_note: string;
}

export interface SummaryWaiting {
  total: number;
  high: number;
  kinds: string[];
  sentence: string;
}

export interface Summary {
  id: string;
  prepared: string;
  prepared_words: string;
  title: string;
  dateline: string;
  standfirst: string;
  demo: boolean;
  demo_warning: string | null;
  label: string;
  question: string;
  since: string | null;
  since_ts: string | null;
  since_reason: string;
  sections: SummarySection[];
  waiting: SummaryWaiting;
  cited: string[];
  sources: string[];
  /** True only when medications or allergies took more than one page on their
   *  own. Nothing is ever dropped from either to prevent it. */
  overflowed: boolean;
  height_mm: number;
  print_url: string;
  markdown?: string;
  event?: string;
  exports?: { markdown?: string; html?: string };
  message?: string;
}

/** What `GET /api/summary/{id}` returns for a sheet withdrawn by a rejection. */
export interface SummaryResponse extends Partial<Summary> {
  id: string;
  summary?: null;
  withdrawn?: number;
  message?: string;
  exports?: { markdown?: string; html?: string };
}

export interface SummaryRow {
  id: string;
  ts: string;
  prepared: string | null;
  prepared_words: string;
  label: string;
  question: string;
  since: string | null;
  since_source: string | null;
  exports: { markdown?: string; html?: string };
  withdrawn: number;
  print_url: string;
}

export interface SummaryList {
  summaries: SummaryRow[];
  as_of: string;
}
