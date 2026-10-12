import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { Connection, OutboxRow, SheetsConfig, SheetsHealth } from "../api/types";
import {
  usePolicyStatus,
  useSaveSheetsConfig,
  useSheetsConfig,
  useSheetsHealth,
  useSheetsLimits,
  useSheetsOutbox,
  useSyncSheets,
  useTestWriteSheets,
  useVerifySheets,
  useRetryOutboxRecord,
} from "../hooks/api";
import { formatWhen } from "../lib/state";

/**
 * Google Sheets reporting (`FEAT-010`, `AC-MVP-008`, `02-technical-prd.md` §8).
 *
 * The panel is organised around the three questions an operator actually has:
 * *is it connected*, *did the last sync go through*, and *is anything stuck*.
 *
 * Four properties it exists to make visible rather than to hide:
 *
 * 1. **Disable and revoke stay distinct here too.** Reporting has its own connection,
 *    and turning reporting off must never silently take the authorization with it.
 * 2. **Reconciliation limits are shown, not buried.** Hexton appends and never edits,
 *    so a delivered row is the operator's to correct. That is a limitation of the
 *    mechanism and belongs on screen next to the sync button.
 * 3. **An unconfirmed write is shown as unconfirmed.** `outbox_ambiguous` is rendered
 *    distinctly from `outbox_failed`, because one means "we do not know" and the other
 *    means "it did not go through" — and only the second is safe to retry blindly.
 * 4. **Column toggles cannot hide the reconciliation fields.** The required columns are
 *    listed from the server and are not toggleable, so a tidier-looking sheet cannot
 *    lose the Sync Key the whole dedup protocol depends on.
 */
export function SheetsPanel({ connections }: { connections: Connection[] }) {
  const accepted = usePolicyStatus();
  const config = useSheetsConfig();
  const health = useSheetsHealth();
  const outbox = useSheetsOutbox();
  const limits = useSheetsLimits();

  if ((accepted.data?.accepted ?? false) === false) {
    return (
      <section className="card p-5" aria-label="Google Sheets reporting">
        <h2 className="text-base font-semibold">Google Sheets reporting</h2>
        <p className="mt-2 text-sm text-ink-muted">
          Accept the privacy policy above first. Reporting writes to a spreadsheet under
          your Google authorization, so Hexton will not offer it until that agreement is
          on record.
        </p>
      </section>
    );
  }

  return (
    <section className="card p-5" aria-label="Google Sheets reporting">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold">Google Sheets reporting</h2>
        {health.data && (
          <span className="text-xs text-ink-muted">
            Schema {health.data.schema_version}
            {health.data.last_sync_at && ` · last sync ${formatWhen(health.data.last_sync_at)}`}
          </span>
        )}
      </header>

      {config.isLoading || !config.data ? (
        <p className="mt-3 text-sm text-ink-muted">Loading the destination…</p>
      ) : (
        <ConfigForm
          config={config.data}
          health={health.data}
          connections={connections}
        />
      )}

      {health.data && <HealthPanel health={health.data} />}

      <div className="mt-5 border-t border-edge pt-4">
        <SyncControls />
      </div>

      {outbox.data && outbox.data.rows.length > 0 && <Outbox rows={outbox.data.rows} />}

      {limits.data && (
        <details className="mt-5 border-t border-edge pt-4">
          <summary className="cursor-pointer text-sm font-medium text-ink">
            What reconciliation cannot do ({limits.data.limits.length} limits)
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-ink-muted">
            {limits.data.limits.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-ink-faint">{limits.data.note}</p>
        </details>
      )}
    </section>
  );
}

function ConfigForm({
  config,
  health,
  connections,
}: {
  config: SheetsConfig;
  health: SheetsHealth | undefined;
  connections: Connection[];
}) {
  const save = useSaveSheetsConfig();
  const [draft, setDraft] = useState<SheetsConfig>(config);
  const [reference, setReference] = useState("");

  // Adopt the server's copy whenever it changes underneath the draft. Without this the
  // form would keep a stale draft after a save and quietly offer to "save" old values.
  useEffect(() => setDraft(config), [config]);

  const optional = health?.optional_columns ?? [];
  const required = health?.required_columns ?? [];
  const sheetsConnections = connections.filter((c) => c.provider === "sheets");

  return (
    <form
      className="mt-4 flex flex-col gap-3 border-t border-edge pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(draft);
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
          Spreadsheet URL or ID
          <input
            className="input text-sm"
            value={reference}
            placeholder="https://docs.google.com/spreadsheets/d/… or the ID"
            onChange={(event) => setReference(event.target.value)}
          />
          <span className="text-[11px] font-normal text-ink-faint">
            Pasted URLs are reduced to the spreadsheet ID before anything is stored.
          </span>
        </label>

        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
          Worksheet tab
          <input
            className="input text-sm"
            value={draft.worksheet ?? ""}
            onChange={(event) => setDraft({ ...draft, worksheet: event.target.value })}
          />
        </label>

        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
          Report timezone
          <input
            className="input text-sm"
            value={draft.report_timezone}
            onChange={(event) => setDraft({ ...draft, report_timezone: event.target.value })}
          />
        </label>

        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
          Day cut-over (local time)
          <input
            className="input text-sm"
            placeholder="03:00"
            value={draft.cutoff_time}
            onChange={(event) => setDraft({ ...draft, cutoff_time: event.target.value })}
          />
          <span className="text-[11px] font-normal text-ink-faint">
            A job scheduled before this time belongs to the previous report day.
          </span>
        </label>

        <label className="flex flex-col gap-1 text-xs font-medium text-ink">
          Authorization
          <select
            className="input text-sm"
            value={draft.connection_id ?? ""}
            onChange={(event) =>
              setDraft({ ...draft, connection_id: event.target.value ? Number(event.target.value) : null })
            }
          >
            <option value="">Not selected</option>
            {sheetsConnections.map((connection) => (
              <option key={connection.id} value={connection.id}>
                {connection.label} ({connection.status})
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 self-end pb-2 text-xs font-medium text-ink">
          <input
            type="checkbox"
            checked={draft.include_daily_summary}
            onChange={(event) =>
              setDraft({ ...draft, include_daily_summary: event.target.checked })
            }
          />
          Write a daily completion summary
        </label>
      </div>

      {optional.length > 0 && (
        <fieldset className="border-t border-edge pt-3">
          <legend className="text-xs font-medium text-ink">Optional columns</legend>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {optional.map((column) => (
              <label key={column} className="flex items-center gap-1.5 text-xs text-ink-muted">
                <input
                  type="checkbox"
                  checked={draft.include_optional_columns.includes(column)}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      include_optional_columns: event.target.checked
                        ? [...draft.include_optional_columns, column]
                        : draft.include_optional_columns.filter((c) => c !== column),
                    })
                  }
                />
                {column}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      {required.length > 0 && (
        <p className="rounded-sm bg-canvas/60 px-2 py-1.5 text-[11px] text-ink-faint">
          Always written, and not switchable off: {required.join(", ")}. The Sync Key is
          the first column because reading it alone is what lets Hexton reconcile without
          touching any other cell — hiding it would break duplicate prevention, so the
          option is not offered.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save destination"}
        </button>
        <span className="text-xs text-ink-faint">
          {health?.configured
            ? `Configured for ${health.spreadsheet_id} → ${health.worksheet}`
            : "No destination configured yet."}
        </span>
      </div>

      {save.error && (
        <p className="text-xs text-cue-attention" role="alert">
          {save.error instanceof ApiError ? save.error.message : "That did not save."}
        </p>
      )}
      {save.data && <p className="text-xs text-ok">Saved.</p>}
      {reference.trim() && (
        <p className="text-[11px] text-ink-faint">
          Enter the ID into the destination field, or clear this box — the reference is
          not stored as typed.
        </p>
      )}
    </form>
  );
}

function HealthPanel({ health }: { health: SheetsHealth }) {
  return (
    <dl className="mt-4 grid gap-x-8 gap-y-1.5 border-t border-edge pt-4 text-sm sm:grid-cols-2">
      <Row label="Status" value={health.configured ? (health.enabled ? "Enabled" : "Disabled") : "Not configured"} />
      <Row label="Verified access" value={formatWhen(health.verified_at)} />
      <Row label="Test write" value={formatWhen(health.last_test_write_at)} />
      <Row
        label="Last sync"
        value={
          health.last_sync_at
            ? `${formatWhen(health.last_sync_at)} · ${health.last_sync_status ?? "unknown"}`
            : "Never"
        }
      />
      <Row label="Rows delivered" value={String(health.outbox_synced)} />
      <Row label="Rows waiting" value={String(health.outbox_pending)} />
      <Row
        label="Rows that did not go through"
        value={String(health.outbox_failed)}
        tone={health.outbox_failed > 0 ? "warning" : undefined}
      />
      <Row
        label="Rows whose outcome is unconfirmed"
        value={String(health.outbox_ambiguous)}
        tone={health.outbox_ambiguous > 0 ? "warning" : undefined}
      />

      {health.last_sync_error && (
        <p className="sm:col-span-2 rounded-sm bg-cue-attention/5 px-2 py-1.5 text-xs text-cue-attention">
          {health.last_sync_error}
        </p>
      )}

      {health.foreign_keys.length > 0 && (
        <p className="sm:col-span-2 text-xs text-ink-muted">
          {health.foreign_keys.length} Sync Key(s) in the sheet that Hexton has no record
          of. They are reported and left alone — usually a sheet that predates this
          installation. Keys Hexton wrote before a lost response or a restart are
          adopted instead, and counted above.
        </p>
      )}

      {health.blocking_problems.length > 0 && (
        <ul className="sm:col-span-2 list-disc space-y-1 pl-5 text-xs text-cue-attention">
          {health.blocking_problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </dl>
  );
}

function SyncControls() {
  const verify = useVerifySheets();
  const testWrite = useTestWriteSheets();
  const sync = useSyncSheets();
  const pending = verify.isPending || testWrite.isPending || sync.isPending;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={pending} onClick={() => verify.mutate()}>
          {verify.isPending ? "Checking…" : "Verify access"}
        </button>
        <button className="btn" disabled={pending} onClick={() => testWrite.mutate()}>
          {testWrite.isPending ? "Writing…" : "Write a test row"}
        </button>
        <button className="btn" disabled={pending} onClick={() => sync.mutate()}>
          {sync.isPending ? "Syncing…" : "Sync now"}
        </button>
      </div>

      {(verify.error || testWrite.error || sync.error) && (
        <p className="mt-2 text-xs text-cue-attention" role="alert">
          {(verify.error ?? testWrite.error ?? sync.error) instanceof ApiError
            ? ((verify.error ?? testWrite.error ?? sync.error) as ApiError).message
            : "That did not go through."}
        </p>
      )}

      {verify.data && <OperationResult label="Verify access" result={verify.data} />}
      {testWrite.data && <OperationResult label="Test write" result={testWrite.data} />}

      {sync.data && (
        <div className="mt-3 rounded-sm border border-edge bg-canvas/40 p-3 text-xs">
          <p className="font-medium text-ink">{sync.data.summary}</p>
          <ul className="mt-1 grid gap-x-6 gap-y-0.5 text-ink-muted sm:grid-cols-2">
            <li>Appended {sync.data.appended}</li>
            <li>Already present {sync.data.already_present}</li>
            <li>Adopted from the destination {sync.data.adopted_from_destination}</li>
            <li>Confirmed by a re-read {sync.data.ambiguous_resolved}</li>
            <li>Still unconfirmed {sync.data.ambiguous_unresolved}</li>
            <li>Did not go through {sync.data.failures.length}</li>
          </ul>
          {sync.data.read_after_ambiguous_write && (
            <p className="mt-2 text-ink-faint">
              A write's outcome was not confirmed, so Hexton read the sheet again rather
              than appending a second time.
            </p>
          )}
          {sync.data.failures.map(([key, reason]) => (
            <p key={key} className="mt-1 break-all text-cue-attention">
              {key}: {reason}
            </p>
          ))}
        </div>
      )}
    </>
  );
}

function OperationResult({ label, result }: { label: string; result: { ok: boolean; message: string } }) {
  return (
    <p
      className={`mt-2 text-xs ${result.ok ? "text-ok" : "text-cue-attention"}`}
      role={result.ok ? undefined : "status"}
    >
      {label}: {result.message}
    </p>
  );
}

function Outbox({ rows }: { rows: OutboxRow[] }) {
  const retry = useRetryOutboxRecord();

  return (
    <div className="mt-5 border-t border-edge pt-4">
      <h3 className="text-sm font-semibold">Outbox</h3>
      <p className="mt-1 text-xs text-ink-muted">
        Every report Hexton owes the sheet. Retrying re-reads the destination first, so it
        cannot duplicate a row that actually landed.
      </p>
      <table className="mt-2 w-full text-left text-xs">
        <caption className="sr-only">
          Every report row Hexton still owes the spreadsheet, with the Sync Key that
          makes the write idempotent and the Schedule ID it belongs to.
        </caption>
        <thead className="text-ink-faint">
          <tr>
            <th className="py-1 pr-3 font-normal">Sync Key</th>
            <th className="py-1 pr-3 font-normal">Schedule ID</th>
            <th className="py-1 pr-3 font-normal">Kind</th>
            <th className="py-1 pr-3 font-normal">Status</th>
            <th className="py-1 pr-3 font-normal">Attempts</th>
            <th className="py-1 font-normal">Reason</th>
          </tr>
        </thead>
        <tbody className="text-ink-muted">
          {rows.map((row) => (
            <tr key={row.id} className="border-t border-edge/60 align-top">
              <td className="py-1.5 pr-3 font-mono text-[11px]">{row.sync_key}</td>
              <td className="py-1.5 pr-3 font-mono text-[11px]">{row.schedule_id ?? "—"}</td>
              <td className="py-1.5 pr-3">{row.kind === "daily_summary" ? "Summary" : "Upload"}</td>
              <td className={`py-1.5 pr-3 ${row.status === "failed" ? "text-cue-attention" : ""}`}>
                {row.status}
              </td>
              <td className="py-1.5 pr-3">{row.attempts}</td>
              <td className="py-1.5 break-all">
                {row.last_error ?? "—"}
                {(row.status === "failed" || row.status === "ambiguous") && (
                  <button
                    className="btn ml-2 px-2 py-0.5 text-[11px]"
                    disabled={retry.isPending}
                    onClick={() => retry.mutate(row.id)}
                  >
                    Retry this row
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {retry.error && (
        <p className="mt-2 text-xs text-cue-attention" role="alert">
          {retry.error instanceof ApiError ? retry.error.message : "That did not go through."}
        </p>
      )}
    </div>
  );
}

function Row({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warning";
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 sm:block">
      <dt className="text-xs text-ink-faint">{label}</dt>
      <dd className={`text-sm ${tone === "warning" ? "text-cue-attention" : tone === "ok" ? "text-ok" : ""}`}>
        {value}
      </dd>
    </div>
  );
}
