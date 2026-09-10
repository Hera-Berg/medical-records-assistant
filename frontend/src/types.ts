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
}

export interface MedicationRow {
  id: string;
  name: string;
  status: Status;
  dose: string | null;
  evidence_tier: Tier | null;
  last_confirmed: string | null;
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
