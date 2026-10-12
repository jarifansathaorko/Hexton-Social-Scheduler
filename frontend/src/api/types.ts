/**
 * Types mirroring the backend's Pydantic contracts.
 *
 * These are hand-written rather than generated so that the shape the UI depends on is
 * visible and reviewable. The backend's `hexton/api/schemas.py` is the source of
 * truth; a drift shows up as a type error here at build time rather than as a blank
 * cell at run time.
 */

export type JobState =
  | "JOB-DRAFT"
  | "JOB-READY"
  | "JOB-QUEUED"
  | "JOB-UPLOADING"
  | "JOB-UPLOADED"
  | "JOB-SCHEDULED"
  | "JOB-PUBLISHED"
  | "JOB-FAILED_RETRY"
  | "JOB-BLOCKED"
  | "JOB-MISSED_WINDOW"
  | "JOB-NEEDS_REVIEW"
  | "JOB-CANCELLED"
  | "JOB-FAILED";

export type RemoteState =
  | "remote.unknown"
  | "remote.not_uploaded"
  | "remote.uploading"
  | "remote.uploaded_processing"
  | "remote.scheduled"
  | "remote.public"
  | "remote.other_privacy"
  | "remote.failed";

export type MentionState =
  | "not_requested"
  | "text_only"
  | "manual_required"
  | "pending_manual"
  | "verified"
  | "failed";

// ---------------------------------------------------------------------------
// Compliance (COMP-01..COMP-10)
// ---------------------------------------------------------------------------

export interface PolicyStatus {
  accepted: boolean;
  version: string;
  accepted_at: string | null;
  accepted_by: string;
}

export interface PolicyDetail {
  version: string;
  commitments: string[];
  links: Record<string, string>;
  retention: {
    client_revocation_days: number;
    google_revocation_days: number;
    note: string;
    policy: Record<string, unknown>;
  };
}

/**
 * The answer to "why did this not work?".
 *
 * `retry_worthwhile` drives whether the UI offers "Try again". It is true only for
 * `remote_failed`, because retrying a missing credential cannot help — and offering
 * the button there sends the operator to reauthorize for what is actually a network
 * blip, which is the conflation `COMP-10` exists to prevent.
 */
export interface Diagnostic {
  code: "not_connected" | "disabled" | "policy_not_accepted" | "credential_unusable" | "remote_failed" | "ok";
  remedy: string;
  retry_worthwhile: boolean;
  detail: string;
}

/**
 * What a disable or a revoke actually did.
 *
 * `did_not` is rendered as prominently as `did`. The whole point of `COMP-07` is that
 * an operator can tell the two operations apart *from the result*, without having to
 * remember which one they pressed.
 */
export interface ConnectionAction {
  id: number;
  status: string;
  did: string[];
  did_not: string[];
  explanation: string;
  authorized_data_retention_days: number | null;
}

// ---------------------------------------------------------------------------
// Sheets reporting (FEAT-010, AC-MVP-008)
// ---------------------------------------------------------------------------

export interface SheetsConfig {
  spreadsheet_id: string | null;
  worksheet: string | null;
  connection_id: number | null;
  enabled: boolean;
  report_timezone: string;
  cutoff_time: string;
  include_daily_summary: boolean;
  include_optional_columns: string[];
}

/** The reporting-health panel. Deliberately local-only: it never contacts Google. */
export interface SheetsHealth {
  configured: boolean;
  enabled: boolean;
  spreadsheet_id: string | null;
  worksheet: string | null;
  report_timezone: string | null;
  cutoff_time: string | null;
  include_daily_summary: boolean;
  include_optional_columns: string[];
  required_columns: string[];
  optional_columns: string[];
  verified_at: string | null;
  last_test_write_at: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  outbox_pending: number;
  outbox_synced: number;
  outbox_failed: number;
  outbox_ambiguous: number;
  outbox_total: number;
  foreign_keys: string[];
  blocking_problems: string[];
  schema_version: string;
  reconciliation_limits: string[];
  policy_version: string;
}

export interface SheetsSync {
  ok: boolean;
  attempted: number;
  appended: number;
  already_present: number;
  adopted_from_destination: number;
  ambiguous_resolved: number;
  ambiguous_unresolved: number;
  foreign_keys: string[];
  failures: [string, string][];
  read_after_ambiguous_write: boolean;
  summary: string;
}

export interface SheetsOperation {
  ok: boolean;
  message: string;
}

export interface OutboxRow {
  id: number;
  kind: "upload" | "daily_summary";
  sync_key: string;
  status: string;
  attempts: number;
  last_error: string | null;
  last_synced_at: string | null;
}

export interface SheetsLimits {
  schema_version: string;
  limits: string[];
  note: string;
}

/**
 * The creator-mention workflow panel (`/api/jobs/{id}/mention`).
 *
 * `attestation_only` is the field that must never be hidden. `DF-19` records that the
 * `videos` resource has no mention entity, so "verified" here means *a person said so*,
 * not that any API check passed — and a UI that dropped the qualifier would let an
 * operator believe otherwise.
 */
export interface Mention {
  job_id: number;
  state: MentionState;
  label: string;
  requested: boolean;
  handles: string[];
  intended_creator: string;
  attestation_only: boolean;
  confirmed_at: string | null;
  reason: string;
  steps: { index: number; instruction: string }[];
  prohibitions: string[];
  rewrite_warning: string | null;
}

export interface Issue {
  code: string;
  message: string;
  severity: "error" | "warning";
  blocking?: boolean;
}

export interface MediaIssue {
  code: string;
  message: string;
  blocking: boolean;
}

export interface MediaCheck {
  path: string;
  exists: boolean;
  readable: boolean;
  size_bytes: number;
  sha256: string | null;
  container: string | null;
  brand: string | null;
  duration_seconds: number | null;
  content_type: string;
  issues: MediaIssue[];
  checked_at: string;
}

export interface Media {
  id: number;
  path: string;
  original_name: string;
  sha256: string;
  size_bytes: number;
  duration_seconds: number | null;
  container: string | null;
  is_valid: boolean;
  validated_at: string | null;
  verified_at: string | null;
}

export interface Channel {
  id: number;
  channel_id: string;
  title: string;
  handle: string | null;
  is_brand_candidate: boolean;
  subscriber_count: number | null;
  verification_source: string;
  verified_at: string | null;
  operator_confirmed_at: string | null;
}

export interface Connection {
  id: number;
  public_id: string;
  provider: string;
  label: string;
  status: string;
  scopes: string[];
  granted_at: string | null;
  token_expires_at: string | null;
  last_token_check_at: string | null;
  last_token_check_ok: boolean | null;
  revoked_at: string | null;
  authorized_data_deleted_at: string | null;
  channels: Channel[];
}

export interface Job {
  id: number;
  public_id: string;
  state: JobState;
  display_label: string;
  display_cue: string;
  primary_action: string;
  can_retry: boolean;
  can_cancel: boolean;

  title: string;
  description: string;
  tags: string[];
  hashtags: string[];
  category_id: string;
  privacy: string;
  mention_requested: boolean;
  mention_state: MentionState;
  mention_reason: string | null;
  mention_handles: string[];
  mention_confirmed: boolean;

  media_id: number;
  media_path: string;
  media_name: string;
  media_size_bytes: number;
  media_duration_seconds: number | null;
  media_container: string | null;

  connection_id: number | null;
  channel_identity_id: number | null;
  channel_title: string | null;
  channel_id: string | null;

  publish_at_utc: string | null;
  display_timezone: string;
  publish_at_display: string | null;
  uploaded_at: string | null;
  uploaded_at_display: string | null;

  remote_state: RemoteState;
  remote_privacy_status: string | null;
  remote_processing_status: string | null;
  poll_result: string | null;
  last_polled_at: string | null;
  awaiting_verification: boolean;

  progress_percent: number;
  video_id: string | null;
  video_url: string | null;

  failure_kind: string | null;
  failure_message: string | null;
  attempt: number;
  max_attempts: number;
  next_attempt_at: string | null;
  next_attempt_display: string | null;
  insert_may_have_succeeded: boolean;

  created_at: string;
  updated_at: string;
  cancelled_at: string | null;
}

export interface JobEvent {
  id: number;
  occurred_at: string;
  kind: string;
  message: string;
  payload: Record<string, unknown> | null;
}

export interface Observation {
  id: number;
  observed_at: string;
  video_id: string | null;
  remote_state: RemoteState;
  privacy_status: string | null;
  upload_status: string | null;
  processing_status: string | null;
  publish_at: string | null;
}

export interface UploadSession {
  id: number;
  phase: string;
  bytes_uploaded: number;
  total_bytes: number;
  attempt: number;
  insert_may_have_succeeded: boolean;
  created_at: string;
}

export interface JobDetail {
  job: Job;
  events: JobEvent[];
  observations: Observation[];
  upload_sessions: UploadSession[];
  allowed_transitions: string[];
}

export interface JobList {
  items: Job[];
  total: number;
  states: Record<string, number>;
}

export interface JobInput {
  media_id: number;
  title: string;
  description: string;
  tags: string[];
  hashtags: string[];
  category_id: string;
  privacy: "private" | "unlisted" | "public";
  connection_id: number | null;
  channel_identity_id: number | null;
  publish_at_utc: string | null;
  display_timezone: string | null;
  min_lead_minutes: number | null;
  mention_requested: boolean;
  consent_transforms: boolean;
}

export interface ValidateResult {
  ok: boolean;
  issues: Issue[];
  effective_publish_at_utc: string | null;
  schedule_issues: Issue[];
}

export interface Settings {
  display_timezone: string;
  min_lead_minutes: number;
  warning_lead_minutes: number;
  missed_grace_minutes: number;
  lease_seconds: number;
  worker_poll_seconds: number;
  retention_days: number;
  connector_kind: string;
  credential_store_kind: string;
  data_dir: string;
  quota_daily_units: number;
  quota_used_today: number;
  quota_remaining: number;
  quota_resets_at: string;
}

export interface WorkerStatus {
  owner: string | null;
  last_heartbeat_at: string | null;
  alive: boolean;
  lease_seconds: number;
  poll_seconds: number;
}

export interface Dashboard {
  total: number;
  by_state: Record<string, number>;
  upcoming: Job[];
  needs_attention: Job[];
  uploading: Job[];
  quota_remaining: number;
  worker_alive: boolean;
}

export interface CancelResult {
  local_cancelled: boolean;
  remote_attempted: boolean;
  remote_succeeded: boolean | null;
  message: string;
  job: Job;
}

export interface AuthorizeResult {
  authorization_url: string;
  state: string;
  redirect_uri: string;
  scopes: string[];
  note: string;
  enabled: boolean;
  blocked_reason: string | null;
}
