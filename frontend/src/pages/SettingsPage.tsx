import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { Settings, SettingsUpdate } from "../api/types";
import { CompliancePanel } from "../components/CompliancePanel";
import { IconRefreshCw } from "../components/Icons";
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
 * Settings & Diagnostics Page.
 *
 * Implements Panels 7 & 8 of the visual specification:
 * - Tabbed navigation bar: All, Scheduling, Connections, Reporting, Advanced
 * - 2-column form for scheduling preferences
 * - Real runtime facts and worker heartbeat telemetry
 * - Quota remaining gauge with progress bar
 * - Sanitize-on-export diagnostics without secrets or credentials
 * - Explicit "Not Configurable" section detailing deliberate MVP boundaries
 */
export function SettingsPage() {
  const settings = useSettings();
  const worker = useWorkerStatus();
  const connections = useConnections();
  const sheets = useSheetsHealth();
  const [activeTab, setActiveTab] = useState<"all" | "scheduling" | "connections" | "reporting" | "advanced">("all");

  if (settings.isLoading) {
    return (
      <div className="py-20 text-center">
        <p className="text-sm text-[var(--hs-text-muted)] animate-pulse">Loading local PC settings…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Page Header */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Settings &amp; Diagnostics</h1>
          <p className="mt-1 text-sm text-[var(--hs-text-secondary)]">Everything here is local to this PC.</p>
        </div>
        <div className="flex items-center gap-2">
          <Link className="btn btn-secondary text-xs" to="/channels">
            Connections &amp; Channels
          </Link>
          <Link className="btn btn-secondary text-xs" to="/reports">
            Google Sheets Reporting
          </Link>
        </div>
      </header>

      {/* Tab Navigation matching Panels 7 & 8 */}
      <div className="flex items-center gap-2 border-b border-[var(--hs-border)] pb-2 overflow-x-auto">
        {(
          [
            { id: "all", label: "All Settings" },
            { id: "scheduling", label: "Scheduling" },
            { id: "connections", label: "Connections" },
            { id: "reporting", label: "Reporting" },
            { id: "advanced", label: "Advanced" },
          ] as const
        ).map((tab) => (
          <button
            key={tab.id}
            className={`px-4 py-2 text-xs font-semibold rounded-xl transition-all ${
              activeTab === tab.id
                ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)] shadow-sm"
                : "text-[var(--hs-text-secondary)] hover:text-white hover:bg-[var(--hs-surface-raised)]"
            }`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Scheduling Settings Section (Panel 7) */}
      {(activeTab === "all" || activeTab === "scheduling") && (
        <SchedulingForm settings={settings.data} />
      )}

      {/* Advanced / Worker Telemetry Section (Panel 8) */}
      {(activeTab === "all" || activeTab === "advanced") && (
        <>
          <WorkerTelemetrySection
            alive={worker.data?.alive ?? false}
            owner={worker.data?.owner ?? null}
            heartbeat={worker.data?.last_heartbeat_at ?? null}
            lease={worker.data?.lease_seconds ?? null}
            poll={worker.data?.poll_seconds ?? null}
            quotaRemaining={settings.data?.quota_remaining ?? 10000}
            quotaDaily={settings.data?.quota_daily_units ?? 10000}
            dataDir={settings.data?.data_dir ?? ""}
          />

          <RuntimeFacts
            settings={settings.data}
            workerAlive={worker.data?.alive ?? false}
            workerOwner={worker.data?.owner ?? null}
          />

          <NotConfigurable />

          <DiagnosticsExport />
        </>
      )}

      {/* Connections Section */}
      {(activeTab === "all" || activeTab === "connections") && (
        <ConnectionsSummary connectionsCount={(connections.data ?? []).length} />
      )}

      {/* Reporting Section */}
      {(activeTab === "all" || activeTab === "reporting") && (
        <SheetsSummary
          configured={sheets.data?.configured ?? false}
          lastSyncAt={sheets.data?.last_sync_at ?? null}
        />
      )}

      {/* Privacy Policy & Compliance Gating */}
      <CompliancePanel connections={connections.data ?? []} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scheduling Form (Panel 7)
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
    <section className="hs-card p-6" aria-label="Scheduling settings">
      <div className="border-b border-[var(--hs-border)]/60 pb-3 mb-5">
        <h2 className="text-base font-bold text-white tracking-tight">Scheduling Settings</h2>
        <p className="mt-0.5 text-xs text-[var(--hs-text-muted)]">
          The four fields the backend actually accepts, plus the display time zone used for every time shown in this application.
        </p>
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="time_zone">
            Time Zone
          </label>
          <input
            id="time_zone"
            className="input text-xs"
            placeholder="Asia/Dhaka"
            value={valueOf("display_timezone")}
            onChange={(e) => setDraft((d) => ({ ...d, display_timezone: e.target.value }))}
          />
          <p className="mt-1 text-[11px] text-[var(--hs-text-muted)]">
            An IANA time zone name such as <code className="font-mono text-white">Asia/Dhaka</code>.
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
              className="input text-xs"
              value={valueOf(field.name)}
              onChange={(e) => setDraft((d) => ({ ...d, [field.name]: e.target.value }))}
            />
            <p className="mt-1 text-[11px] text-[var(--hs-text-muted)]">{field.hint}</p>
          </div>
        ))}
      </div>

      {message && (
        <p
          role="status"
          className={`mt-4 rounded-lg border px-3.5 py-2 text-xs font-medium ${
            message.kind === "ok"
              ? "border-[var(--hs-success)]/40 bg-[var(--hs-success)]/10 text-[var(--hs-success)]"
              : "border-[var(--hs-danger)]/40 bg-[var(--hs-danger)]/10 text-[var(--hs-danger)]"
          }`}
        >
          {message.text}
        </p>
      )}

      <div className="mt-6 pt-4 border-t border-[var(--hs-border)]/60 flex items-center gap-3">
        <button
          className="btn btn-primary py-2 px-4 rounded-lg text-xs font-semibold shadow-md"
          disabled={!dirty || update.isPending}
          onClick={() => void save()}
        >
          Save scheduling settings
        </button>
        <button
          className="btn btn-secondary py-2 px-3 rounded-lg text-xs font-medium"
          disabled={!dirty}
          onClick={() => setDraft({})}
        >
          Reset to Default
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Worker Telemetry Panel (Panel 8)
// ---------------------------------------------------------------------------

function WorkerTelemetrySection({
  alive,
  owner,
  heartbeat,
  lease,
  poll,
  quotaRemaining,
  quotaDaily,
  dataDir,
}: {
  alive: boolean;
  owner: string | null;
  heartbeat: string | null;
  lease: number | null;
  poll: number | null;
  quotaRemaining: number;
  quotaDaily: number;
  dataDir: string;
}) {
  const quotaPercent = Math.max(0, Math.min(100, Math.round((quotaRemaining / (quotaDaily || 10000)) * 100)));

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      {/* Left: Worker Status Card */}
      <section className="hs-card p-5 flex flex-col justify-between" aria-label="Worker">
        <div>
          <div className="flex items-center justify-between border-b border-[var(--hs-border)]/60 pb-3">
            <h2 className="text-base font-bold text-white tracking-tight">Worker Status</h2>
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                alive
                  ? "bg-[var(--hs-success)]/10 text-[var(--hs-success)] border border-[var(--hs-success)]/30"
                  : "bg-[var(--hs-warning)]/10 text-[var(--hs-warning)] border border-[var(--hs-warning)]/30"
              }`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${alive ? "bg-[var(--hs-success)] shadow-[0_0_8px_rgba(52,211,153,0.8)]" : "bg-[var(--hs-warning)]"}`} />
              {alive ? "Running" : "Stopped"}
            </span>
          </div>

          <p className="mt-3 text-xs text-[var(--hs-text-secondary)]">
            {alive
              ? `Running — heartbeat from ${owner ?? "an unknown process"}`
              : "Not running — queued jobs are parked, not progressing"}
          </p>

          <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
            <div className="rounded-lg bg-[var(--hs-surface-raised)] p-2.5 border border-[var(--hs-border)]">
              <span className="text-[10px] uppercase font-medium text-[var(--hs-text-muted)] block">Last Heartbeat</span>
              <span className="text-xs font-semibold text-white block mt-0.5">{formatWhen(heartbeat)}</span>
            </div>
            <div className="rounded-lg bg-[var(--hs-surface-raised)] p-2.5 border border-[var(--hs-border)]">
              <span className="text-[10px] uppercase font-medium text-[var(--hs-text-muted)] block">Lease / Poll</span>
              <span className="text-xs font-mono font-semibold text-white block mt-0.5">{lease ?? "—"}s / {poll ?? "—"}s</span>
            </div>
          </div>

          {/* Quota Gauge */}
          <div className="mt-4 rounded-lg bg-[var(--hs-surface-raised)] p-3 border border-[var(--hs-border)]">
            <div className="flex items-center justify-between text-xs mb-1.5">
              <span className="text-[var(--hs-text-muted)]">Quota Remaining Today</span>
              <span className="font-mono font-semibold text-white">{quotaRemaining} / {quotaDaily}</span>
            </div>
            <div className="h-2 w-full rounded-full bg-[var(--hs-surface-soft)] overflow-hidden">
              <div
                className="h-full rounded-full bg-[var(--hs-violet)] transition-all duration-500"
                style={{ width: `${quotaPercent}%` }}
              />
            </div>
          </div>
        </div>

        <div className="mt-4 pt-3 border-t border-[var(--hs-border)]/60 flex items-center gap-2">
          <button
            type="button"
            className="btn btn-primary text-xs py-1.5 px-3"
            onClick={() => window.location.reload()}
          >
            <IconRefreshCw size={13} /> Refresh Status
          </button>
        </div>
      </section>

      {/* Right: Application Directory Card */}
      <section className="hs-card p-5 flex flex-col justify-between" aria-label="Application Facts">
        <div>
          <div className="border-b border-[var(--hs-border)]/60 pb-3 mb-4">
            <h2 className="text-base font-bold text-white tracking-tight">Application Environment</h2>
            <p className="mt-0.5 text-xs text-[var(--hs-text-muted)]">Local storage and Windows runtime configuration.</p>
          </div>

          <div className="flex flex-col gap-3">
            <div>
              <span className="label text-[11px]">Data Directory</span>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  readOnly
                  className="input text-xs font-mono bg-[var(--hs-canvas)] cursor-default select-all"
                  value={dataDir}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-lg bg-[var(--hs-surface-raised)] p-2.5 border border-[var(--hs-border)]">
                <span className="text-[10px] uppercase font-medium text-[var(--hs-text-muted)] block">Connector</span>
                <span className="text-xs font-mono font-semibold text-white block mt-0.5">fake</span>
              </div>
              <div className="rounded-lg bg-[var(--hs-surface-raised)] p-2.5 border border-[var(--hs-border)]">
                <span className="text-[10px] uppercase font-medium text-[var(--hs-text-muted)] block">Credential Storage</span>
                <span className="text-xs font-mono font-semibold text-white block mt-0.5">plaintext</span>
              </div>
            </div>
          </div>
        </div>

        <p className="mt-4 pt-3 border-t border-[var(--hs-border)]/60 text-[11px] text-[var(--hs-text-muted)]">
          Health is read from the worker's own durable heartbeat, never inferred from browser state.
        </p>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Runtime Facts
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
    <section className="hs-card p-6" aria-label="Runtime">
      <h2 className="text-base font-bold text-white tracking-tight">Runtime Facts</h2>
      <dl className="mt-3 grid gap-x-8 gap-y-2.5 text-xs sm:grid-cols-2">
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
      <p className="mt-3 text-[11px] text-[var(--hs-text-muted)] border-t border-[var(--hs-border)]/40 pt-2">
        Lease length and poll interval are process-lifetime settings the worker reads at
        startup, so changing them takes effect on the next restart rather than silently
        invalidating a lease that is already running.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Not Configurable (Required by PRD and e2e test)
// ---------------------------------------------------------------------------

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
    <section className="hs-card p-6" aria-label="Not configurable">
      <div className="border-b border-[var(--hs-border)]/60 pb-3 mb-4">
        <h2 className="text-base font-bold text-white tracking-tight">Not configurable here</h2>
        <p className="mt-0.5 text-xs text-[var(--hs-text-muted)]">
          Recorded so the absence of a control reads as a deliberate decision rather than an oversight.
        </p>
      </div>
      <dl className="grid gap-3 sm:grid-cols-2">
        {items.map(([label, explanation]) => (
          <div key={label} className="rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] p-3.5">
            <dt className="text-xs font-bold text-white tracking-tight">{label}</dt>
            <dd className="mt-1 text-xs text-[var(--hs-text-secondary)]">{explanation}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function ConnectionsSummary({ connectionsCount }: { connectionsCount: number }) {
  return (
    <section className="hs-card p-6" aria-label="Connections status">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-white tracking-tight">Connections &amp; Channels</h2>
          <p className="mt-1 text-xs text-[var(--hs-text-muted)]">
            {connectionsCount} connection{connectionsCount === 1 ? "" : "s"} authorized.
          </p>
        </div>
        <Link className="btn btn-primary text-xs" to="/channels">
          Manage YouTube Channels
        </Link>
      </div>
    </section>
  );
}

function SheetsSummary({ configured, lastSyncAt }: { configured: boolean; lastSyncAt: string | null }) {
  return (
    <section className="hs-card p-6" aria-label="Google Sheets reporting status">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-white tracking-tight">Google Sheets Reporting</h2>
          <p className="mt-1 text-xs text-[var(--hs-text-muted)]">
            {configured
              ? lastSyncAt
                ? `Configured · last successful sync ${formatWhen(lastSyncAt)}`
                : "Configured · no sync has completed yet"
              : "Not configured."}
          </p>
        </div>
        <Link className="btn btn-primary text-xs" to="/reports">
          Open Reporting
        </Link>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Diagnostics Export
// ---------------------------------------------------------------------------

const SENSITIVE_KEY = /token|secret|password|cookie|authorization|csrf|credential/i;
const SAFE_KEYS = new Set(["credential_store_kind"]);
const USER_PATH = /(\\Users\\|\/home\/|\/Users\/)[^\\/]+/gi;

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
          }
        : null,
      sheets: sheets.data,
      policy: {
        accepted: policy.data?.accepted ?? false,
        version: policyDetail.data?.version ?? null,
      },
      connections: connectionsView,
    });
  }, [settings.data, worker.data, dashboard.data, sheets.data, policy.data, policyDetail.data, connections.data]);

  const download = () => {
    const text = JSON.stringify(payload, null, 2);
    const blob = new Blob([text], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `hexscheduler-diagnostics-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setSaved(true);
  };

  return (
    <section className="hs-card p-6" aria-label="Diagnostics">
      <div className="border-b border-[var(--hs-border)]/60 pb-3 mb-4">
        <h2 className="text-base font-bold text-white tracking-tight">Diagnostics Snapshot</h2>
        <p className="mt-0.5 text-xs text-[var(--hs-text-muted)]">
          Export an un-credentialled JSON snapshot for troubleshooting.
        </p>
      </div>
      <p className="text-xs text-[var(--hs-text-secondary)]">
        Includes current job counts, worker health, sheets status, and authorization states.
        Credentials and Windows profile names are stripped on generation.
      </p>
      <div className="mt-4 flex items-center gap-3">
        <button
          className="btn btn-secondary text-xs py-2 px-3.5"
          onClick={download}
        >
          Download diagnostics
        </button>
        {saved && <span className="text-xs font-semibold text-[var(--hs-success)]">✓ Exported to your downloads folder</span>}
      </div>
    </section>
  );
}

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
  const toneClass =
    tone === "ok"
      ? "text-[var(--hs-success)]"
      : tone === "warning"
        ? "text-[var(--hs-warning)]"
        : "text-white";
  return (
    <div className="flex justify-between gap-3 border-b border-[var(--hs-border)]/40 pb-1.5 last:border-0">
      <dt className="text-[var(--hs-text-muted)] font-medium">{label}</dt>
      <dd className={`text-right truncate max-w-sm ${mono ? "font-mono" : ""} ${toneClass}`}>
        {value}
      </dd>
    </div>
  );
}
