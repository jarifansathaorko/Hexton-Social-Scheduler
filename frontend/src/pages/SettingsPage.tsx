import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { Settings, SettingsUpdate } from "../api/types";
import { CompliancePanel } from "../components/CompliancePanel";
import {
  useConnections,
  useDashboard,
  usePolicyDetail,
  usePolicyStatus,
  useSettings,
  useSheetsHealth,
  useUpdateSettings,
  useWorkerStatus,
} from "../hooks/api";
import { formatWhen } from "../lib/state";

/**
 * Settings & Diagnostics.
 *
 * Two rules shape this screen.
 *
 * First, **every control writes somewhere real.** The five scheduling fields below map
 * one-to-one onto `PUT /api/settings`; anything the backend does not expose — a global
 * default visibility, an upload delay, a retry policy, a global metadata-transform
 * switch — is listed as a fact rather than drawn as a toggle, because a switch with no
 * endpoint behind it lies about what this build can do. Where the setting genuinely
 * exists but is per-job (visibility, metadata transforms), that is stated outright.
 *
 * Second, **no token is ever rendered or exported.** None is ever sent: a connection is
 * described by its status, its scopes and its identities, and the material that makes
 * it work stays in Windows Credential Manager. The diagnostics export applies the same
 * rule again on the way out.
 */
export function SettingsPage() {
  const settings = useSettings();
  const worker = useWorkerStatus();
  const connections = useConnections();
  const sheets = useSheetsHealth();

  if (settings.isLoading) return <p className="text-ink-muted">Loading…</p>;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings &amp; Diagnostics</h1>
          <p className="mt-1 text-sm text-ink-muted">Everything here is local to this PC.</p>
        </div>
        <div className="flex gap-2">
          <Link className="btn" to="/channels">
            Connections &amp; channels
          </Link>
          <Link className="btn" to="/reports">
            Google Sheets reporting
          </Link>
        </div>
      </header>

      <SchedulingForm settings={settings.data} />

      <RuntimeFacts settings={settings.data} workerAlive={worker.data?.alive ?? false} workerOwner={worker.data?.owner ?? null} />

      <WorkerSection
        alive={worker.data?.alive ?? false}
        owner={worker.data?.owner ?? null}
        heartbeat={worker.data?.last_heartbeat_at ?? null}
        lease={worker.data?.lease_seconds ?? null}
        poll={worker.data?.poll_seconds ?? null}
      />

      <NotConfigurable />

      <SheetsSummary configured={sheets.data?.configured ?? false} lastSyncAt={sheets.data?.last_sync_at ?? null} />

      <CompliancePanel connections={connections.data ?? []} />

      <DiagnosticsExport />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scheduling
// ---------------------------------------------------------------------------

const FIELDS: { name: Exclude<keyof SettingsUpdate, "display_timezone">; label: string; unit: string; hint: string }[] = [
  { name: "min_lead_minutes", label: "Pre-upload lead time", unit: "minutes", hint: "How far ahead of publication a job must be queued before the worker will accept it." },
  { name: "warning_lead_minutes", label: "Warning band", unit: "minutes", hint: "A job inside this window is flagged as urgent." },
  { name: "missed_grace_minutes", label: "Missed-window grace", unit: "minutes", hint: "How late a publication may run before the job is marked as a missed window." },
  { name: "retention_days", label: "Local data retention", unit: "days", hint: "How long job records are kept before the retention sweep removes them." },
];

function SchedulingForm({ settings }: { settings: Settings | undefined }) {
  const update = useUpdateSettings();
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const valueOf = (name: string): string => {
    const fromDraft = draft[name];
    if (fromDraft !== undefined) return fromDraft;
    if (!settings) return "";
    return name === "display_timezone"
      ? settings.display_timezone
      : String(settings[name as keyof Settings] ?? "");
  };

  const dirty = Object.keys(draft).length > 0;

  const save = async () => {
    setMessage(null);
    const payload: SettingsUpdate = {};
    if (draft.display_timezone !== undefined) payload.display_timezone = draft.display_timezone.trim();
    for (const field of FIELDS) {
      if (draft[field.name] === undefined) continue;
      const parsed = Number(draft[field.name]);
      if (!Number.isFinite(parsed) || parsed < 0) {
        setMessage({ kind: "error", text: `${field.label} must be a non-negative number.` });
        return;
      }
      payload[field.name] = Math.round(parsed);
    }
    try {
      await update.mutateAsync(payload);
      setDraft({});
      setMessage({
        kind: "ok",
        text: "Saved. The worker reads these at startup, so lease and poll changes take effect on the next restart; the rest apply immediately.",
      });
    } catch (exc) {
      setMessage({ kind: "error", text: exc instanceof ApiError ? exc.message : "Those settings could not be saved." });
    }
  };

  return (
    <section className="card p-5" aria-label="Scheduling settings">
      <h2 className="text-base font-semibold">Scheduling</h2>
      <p className="mt-1 text-sm text-ink-muted">
        The four fields the backend actually accepts, plus the display time zone used for
        every time shown in this application.
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="tz">
            Time zone
          </label>
          <input
            id="tz"
            className="input"
            placeholder="Asia/Dhaka"
            value={valueOf("display_timezone")}
            onChange={(e) => setDraft((d) => ({ ...d, display_timezone: e.target.value }))}
          />
          <p className="mt-1 text-xs text-ink-faint">
            An IANA name such as <code className="font-mono">Asia/Dhaka</code>. Invalid
            zones are refused by the server.
          </p>
        </div>

        {FIELDS.map((field) => (
          <div key={field.name}>
            <label className="label" htmlFor={field.name}>
              {field.label} ({field.unit})
            </label>
            <input
              id={field.name}
              type="number"
              min={0}
              className="input"
              value={valueOf(field.name)}
              onChange={(e) => setDraft((d) => ({ ...d, [field.name]: e.target.value }))}
            />
            <p className="mt-1 text-xs text-ink-faint">{field.hint}</p>
          </div>
        ))}
      </div>

      {message && (
        <p
          role="status"
          className={`mt-4 rounded-md border px-3 py-2 text-sm ${
            message.kind === "ok"
              ? "border-cue-success/40 bg-cue-success/10 text-cue-success"
              : "border-cue-warning/50 bg-cue-warning/10 text-cue-warning"
          }`}
        >
          {message.text}
        </p>
      )}

      <div className="mt-4 flex gap-2">
        <button className="btn btn-primary" disabled={!dirty || update.isPending} onClick={() => void save()}>
          Save scheduling settings
        </button>
        <button className="btn" disabled={!dirty} onClick={() => setDraft({})}>
          Discard changes
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Read-only facts
// ---------------------------------------------------------------------------

function RuntimeFacts({
  settings,
  workerAlive,
  workerOwner,
}: {
  settings: Settings | undefined;
  workerAlive: boolean;
  workerOwner: string | null;
}) {
  return (
    <section className="card p-5" aria-label="Runtime">
      <h2 className="text-base font-semibold">Runtime</h2>
      <dl className="mt-3 grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
        <Fact label="Data directory" value={settings?.data_dir ?? "—"} mono />
        <Fact label="Connector" value={settings?.connector_kind ?? "—"} />
        <Fact label="Credential store" value={settings?.credential_store_kind ?? "—"} />
        <Fact
          label="Quota remaining today"
          value={`${settings?.quota_remaining ?? "—"} / ${settings?.quota_daily_units ?? "—"}`}
        />
        <Fact
          label="Quota resets"
          value={`${formatWhen(settings?.quota_resets_at ?? null)} (midnight Pacific)`}
        />
        <Fact label="Lease length" value={`${settings?.lease_seconds ?? "—"} seconds`} />
        <Fact label="Worker poll interval" value={`${settings?.worker_poll_seconds ?? "—"} seconds`} />
        <Fact
          label="Worker"
          value={workerAlive ? `Running (${workerOwner ?? "unknown owner"})` : "Not detected"}
          tone={workerAlive ? "ok" : "warning"}
        />
      </dl>
      <p className="mt-3 text-xs text-ink-faint">
        Lease length and poll interval are process-lifetime settings the worker reads at
        startup, so changing them takes effect on the next restart rather than silently
        invalidating a lease that is already running.
      </p>
    </section>
  );
}

function WorkerSection({
  alive,
  owner,
  heartbeat,
  lease,
  poll,
}: {
  alive: boolean;
  owner: string | null;
  heartbeat: string | null;
  lease: number | null;
  poll: number | null;
}) {
  return (
    <section className="card p-5" aria-label="Worker">
      <h2 className="text-base font-semibold">Worker startup &amp; health</h2>
      <p className="mt-1 text-sm text-ink-muted">
        The worker is a separate process on purpose. The browser never executes a
        schedule: closing this tab, sleeping the machine or losing the window does not
        stop a job that is already queued.
      </p>
      <div
        className={`mt-3 rounded-md border px-3 py-2 text-sm ${
          alive
            ? "border-cue-success/40 bg-cue-success/10 text-cue-success"
            : "border-cue-attention/50 bg-cue-attention/10 text-cue-attention"
        }`}
      >
        {alive
          ? `Running — heartbeat from ${owner ?? "an unknown process"}`
          : "Not running — queued jobs are parked, not progressing"}
      </div>
      <dl className="mt-3 grid gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
        <Fact label="Last heartbeat" value={formatWhen(heartbeat)} />
        <Fact label="Lease / poll" value={`${lease ?? "—"}s / ${poll ?? "—"}s`} />
      </dl>
      <p className="mt-3 text-xs text-ink-faint">
        Start it with <code className="font-mono">hexton serve</code>. Health is read from
        the worker's own durable heartbeat, never inferred from how fresh this page looks.
      </p>
    </section>
  );
}

/**
 * Settings the brief asks for that this build does not expose.
 *
 * Listed rather than faked. Each line says where the capability actually lives — on
 * the job, or nowhere yet — so the absence reads as a decision instead of an omission.
 */
function NotConfigurable() {
  const items: [string, string][] = [
    [
      "Default visibility",
      "Per-job only. Every job carries its own privacy field and Private is the default; there is no global default to change, so no switch is offered here.",
    ],
    [
      "Upload delay & retry policy",
      "Retry attempts and backoff are set per job by the worker's own policy. The effective attempt count for any job is shown on its details panel, not adjustable here.",
    ],
    [
      "Metadata transformations",
      "Consent is granted per job, never globally. The checkbox on each job states exactly what will change and every change is written to that job's history.",
    ],
    [
      "Automatic thumbnails",
      "Not implemented in this MVP. The revised PRDs do not approve it, so no control is offered.",
    ],
    [
      "Drag-to-reschedule",
      "Not implemented. Rescheduling goes through the editor so the new time is validated against the lead time before it is accepted.",
    ],
  ];
  return (
    <section className="card p-5" aria-label="Not configurable">
      <h2 className="text-base font-semibold">Not configurable here</h2>
      <p className="mt-1 text-sm text-ink-muted">
        Recorded so the absence of a control reads as a decision rather than an
        oversight.
      </p>
      <dl className="mt-3 grid gap-3 sm:grid-cols-2">
        {items.map(([label, explanation]) => (
          <div key={label} className="rounded-md border border-edge bg-canvas p-3">
            <dt className="text-xs font-medium uppercase tracking-wide text-ink-faint">{label}</dt>
            <dd className="mt-1 text-sm text-ink-muted">{explanation}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function SheetsSummary({ configured, lastSyncAt }: { configured: boolean; lastSyncAt: string | null }) {
  return (
    <section className="card p-5" aria-label="Google Sheets reporting status">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">Google Sheets reporting</h2>
          <p className="mt-1 text-sm text-ink-muted">
            {configured
              ? lastSyncAt
                ? `Configured · last successful sync ${formatWhen(lastSyncAt)}`
                : "Configured · no sync has completed yet"
              : "Not configured."}
          </p>
        </div>
        <Link className="btn" to="/reports">
          Open reporting
        </Link>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

const SENSITIVE_KEY = /token|secret|password|cookie|authorization|csrf|credential/i;
/**
 * The only key allowed through despite looking like a credential. It holds a store
 * *name* (`credman`, `dpapi_file`) and is one of the most useful facts in a bug
 * report; everything else that matches is dropped, because defaulting to redaction is
 * the only safe direction when a field name is ambiguous.
 */
const SAFE_KEYS = new Set(["credential_store_kind"]);
const USER_PATH = /(\\Users\\|\/home\/|\/Users\/)[^\\/]+/gi;

/** Mask a value that could identify a person or a Google resource without hiding its shape. */
function redact(value: unknown, key = ""): unknown {
  if (SENSITIVE_KEY.test(key) && !SAFE_KEYS.has(key)) return "[redacted]";
  if (typeof value === "string") return value.replace(USER_PATH, "$1<redacted>");
  if (Array.isArray(value)) return value.map((entry) => redact(entry, key));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([child, entry]) => [child, redact(entry, child)]),
    );
  }
  return value;
}

/**
 * Diagnostics export.
 *
 * Assembled entirely from data this browser has already been given, so there is no new
 * endpoint to get wrong and no server-side file to secure. Redaction runs over the
 * finished object rather than over the inputs, which means a field added to one of
 * these responses later is masked by default instead of being forgotten: anything
 * whose *name* looks like a credential is dropped, and Windows profile paths are
 * stripped of the account name.
 *
 * The frontend never receives a token, refresh token or cookie value, so the honest
 * statement of what is removed is: nothing was here to begin with, and the export is
 * additionally scrubbed on the way out.
 */
function DiagnosticsExport() {
  const settings = useSettings();
  const worker = useWorkerStatus();
  const dashboard = useDashboard();
  const sheets = useSheetsHealth();
  const policy = usePolicyStatus();
  const policyDetail = usePolicyDetail();
  const connections = useConnections();
  const [saved, setSaved] = useState(false);

  const payload = useMemo(() => {
    const connectionsView = (connections.data ?? []).map((connection) => ({
      id: connection.id,
      public_id: connection.public_id,
      provider: connection.provider,
      label: connection.label,
      status: connection.status,
      scopes: connection.scopes,
      granted_at: connection.granted_at,
      token_expires_at: connection.token_expires_at,
      revoked_at: connection.revoked_at,
      authorized_data_deleted_at: connection.authorized_data_deleted_at,
      channels: connection.channels.map((channel) => ({
        id: channel.id,
        channel_id: channel.channel_id,
        title: channel.title,
        handle: channel.handle,
        is_brand_candidate: channel.is_brand_candidate,
        verification_source: channel.verification_source,
        verified_at: channel.verified_at,
        operator_confirmed_at: channel.operator_confirmed_at,
      })),
    }));

    return redact({
      generated_at: new Date().toISOString(),
      note:
        "Local diagnostics. Tokens, cookies and CSRF material are excluded by construction " +
        "because this application never sends them to the browser, and the object is scrubbed again here.",
      settings: settings.data,
      worker: worker.data,
      dashboard: dashboard.data
        ? {
            total: dashboard.data.total,
            by_state: dashboard.data.by_state,
            quota_remaining: dashboard.data.quota_remaining,
            worker_alive: dashboard.data.worker_alive,
            // Row payloads are omitted: titles and descriptions are operator content,
            // not diagnostics.
            counts_only: true,
          }
        : null,
      reporting: sheets.data,
      policy: { ...policy.data, commitments: policyDetail.data?.commitments ?? [] },
      connections: connectionsView,
      by_state_counts: dashboard.data?.by_state ?? null,
    });
  }, [settings.data, worker.data, dashboard.data, sheets.data, policy.data, policyDetail.data, connections.data]);

  const download = () => {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `hexton-diagnostics-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setSaved(true);
  };

  return (
    <section className="card p-5" aria-label="Diagnostics export">
      <h2 className="text-base font-semibold">Diagnostics export</h2>
      <p className="mt-1 text-sm text-ink-muted">
        A JSON snapshot of settings, worker health, counts, reporting state and
        connection metadata — enough for a bug report without anything that could
        authorize a request.
      </p>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-ink-muted">
        <li>Anything whose name looks like a credential is replaced with [redacted].</li>
        <li>Windows account names are stripped from file paths.</li>
        <li>No job titles, descriptions or media contents are included.</li>
        <li>No cookie, CSRF token or Authorization header is ever present.</li>
      </ul>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn btn-primary" onClick={download}>
          Download diagnostics
        </button>
        {saved && (
          <span role="status" className="text-xs text-cue-success">
            Saved to your downloads.
          </span>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------

function Fact({
  label,
  value,
  mono,
  tone,
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "ok" | "warning";
}) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs uppercase tracking-wide text-ink-faint">{label}</dt>
      <dd
        className={
          mono
            ? "break-all font-mono text-xs"
            : tone === "ok"
              ? "text-cue-success"
              : tone === "warning"
                ? "text-cue-attention"
                : "text-ink"
        }
      >
        {value}
      </dd>
    </div>
  );
}
