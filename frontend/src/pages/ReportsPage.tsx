import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { SheetsPanel } from "../components/SheetsPanel";
import {
  useConnections,
  useDailySummary,
  useJobs,
  usePolicyStatus,
  useReportPreview,
} from "../hooks/api";
import { formatWhen } from "../lib/state";

/**
 * Reports & Google Sheets.
 *
 * The page is organised around the two things an operator actually needs from a
 * report: proof that a row was written, and confidence that the row says something
 * true. The panel handles the destination, the health of the last sync and every row
 * still owed; the sections below it show the *content* — the daily completion summary
 * as it stands, and exactly what one upload's row would claim before it is written.
 *
 * Nothing here can start an upload. Enqueuing reports and syncing write to the local
 * outbox and to the spreadsheet, never to YouTube.
 */
export function ReportsPage() {
  const connections = useConnections();
  const rows = connections.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Reports & Google Sheets</h1>
        <p className="mt-1 text-sm text-ink-muted">
          One durable row per scheduled upload, keyed by Sync Key so a retry can never
          duplicate a row that already landed. A row claims “Published” only when a
          video ID and verified remote evidence both exist.
        </p>
      </header>

      <SheetsPanel connections={rows} />

      <DailyCompletion />

      <RowPreview />
    </div>
  );
}

/**
 * The daily completion summary.
 *
 * A date, not a range: the backend reports on one report date at a time, bounded by
 * the configured cutoff and time zone, and offering a free-form range here would imply
 * a query the endpoint does not perform.
 */
function DailyCompletion() {
  const policy = usePolicyStatus();
  const accepted = policy.data?.accepted ?? false;
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const summary = useDailySummary(accepted ? date : null);

  if (!accepted) return <Locked title="Daily completion summary" />;

  const rows = summary.data?.rows ?? [];
  const first = rows.length > 0 ? rows[0] : undefined;
  const columns = first ? Object.keys(first) : [];

  return (
    <section className="card p-5" aria-label="Daily completion summary">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">Daily completion report</h2>
          <p className="mt-1 text-sm text-ink-muted">
            Everything scheduled for that report date, counted by outcome.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="label mb-0" htmlFor="summary-date">
            Report date
          </label>
          <input
            id="summary-date"
            type="date"
            className="input w-auto"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </div>
      </div>

      {summary.isLoading && <p className="mt-4 text-sm text-ink-muted">Reading…</p>}
      {summary.error && (
        <p role="alert" className="mt-4 text-sm text-cue-warning">
          {summary.error instanceof ApiError ? summary.error.message : "That date could not be read."}
        </p>
      )}
      {summary.data && rows.length === 0 && (
        <p className="mt-4 text-sm text-ink-faint">
          No summary has been produced for {date} yet. Rows appear once at least one
          upload has been reported for that date.
        </p>
      )}
      {rows.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <caption className="sr-only">{`Completion summary for ${date}`}</caption>
            <thead>
              <tr className="border-b border-edge text-left text-xs uppercase tracking-wide text-ink-faint">
                {columns.map((column) => (
                  <th key={column} scope="col" className="px-3 py-2 font-medium first:pl-0">
                    {column.replace(/_/g, " ")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index} className="border-b border-edge/60 last:border-0">
                  {columns.map((column) => (
                    <td key={column} className="px-3 py-2 first:pl-0">
                      {formatCell(row[column])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/**
 * What one upload's row would say.
 *
 * Shown before anything is written so the claim can be checked in one place: whether
 * the row would assert "Published" at all, whether a video ID is present, and whether
 * a *verified* publication time exists. Those three are the difference between a
 * report an operator can act on and one that quietly overstates what was proven.
 */
function RowPreview() {
  const policy = usePolicyStatus();
  const accepted = policy.data?.accepted ?? false;
  const jobs = useJobs("", "");
  const [jobId, setJobId] = useState<number | null>(null);
  const preview = useReportPreview(accepted && jobId !== null ? jobId : null);

  const options = useMemo(() => jobs.data?.items ?? [], [jobs.data]);

  if (!accepted) return <Locked title="Per-upload report details" />;

  return (
    <section className="card p-5" aria-label="Per-upload report row">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">Per-upload report row</h2>
          <p className="mt-1 text-sm text-ink-muted">
            A preview only. Nothing is written to the spreadsheet until you run a sync.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="label mb-0" htmlFor="preview-job">
            Schedule
          </label>
          <select
            id="preview-job"
            className="input w-auto max-w-xs"
            value={jobId ?? ""}
            onChange={(e) => setJobId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Choose a scheduled upload…</option>
            {options.map((job) => (
              <option key={job.id} value={job.id}>
                {job.title || "(untitled)"} · {job.public_id}
              </option>
            ))}
          </select>
        </div>
      </div>

      {jobId === null && (
        <p className="mt-4 text-sm text-ink-faint">
          Pick a schedule to see the exact row, its Sync Key and Schedule ID.
        </p>
      )}
      {preview.isLoading && <p className="mt-4 text-sm text-ink-muted">Reading…</p>}
      {preview.error && (
        <p role="alert" className="mt-4 text-sm text-cue-warning">
          {preview.error instanceof ApiError ? preview.error.message : "That row could not be previewed."}
        </p>
      )}
      {preview.data && (
        <div className="mt-4 flex flex-col gap-3">
          <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs uppercase tracking-wide text-ink-faint">Sync Key</dt>
              <dd className="break-all font-mono text-xs">{preview.data.sync_key}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-ink-faint">Schedule ID</dt>
              <dd className="break-all font-mono text-xs">{preview.data.schedule_id}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-ink-faint">Report date</dt>
              <dd>{preview.data.report_date}</dd>
            </div>
          </dl>

          <ul className="flex flex-wrap gap-2 text-xs">
            <Claim
              ok={preview.data.claims_published}
              yes="Would claim Published"
              no="Will not claim Published"
            />
            <Claim
              ok={preview.data.video_id_present}
              yes="Video ID present"
              no="No video ID"
            />
            <Claim
              ok={preview.data.verified_publication_time_present}
              yes="Verified publication time present"
              no="No verified publication time"
            />
          </ul>

          <div className="overflow-x-auto rounded-md border border-edge">
            <table className="w-full border-collapse text-left text-xs">
              <caption className="sr-only">The exact cells this row would write</caption>
              <thead>
                <tr className="border-b border-edge bg-surface-raised text-ink-faint">
                  {preview.data.columns.map((column) => (
                    <th key={column} scope="col" className="px-3 py-2 font-medium">
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="text-ink-muted">
                <tr>
                  {preview.data.columns.map((column) => (
                    <td key={column} className="px-3 py-2 align-top">
                      {preview.data!.row[column] || "—"}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>

          <Link className="text-xs text-link hover:underline" to={`/calendar?job=${jobId}`}>
            Open this schedule
          </Link>
        </div>
      )}
    </section>
  );
}

function Claim({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return (
    <li
      className={`rounded-full border px-2.5 py-0.5 ${
        ok
          ? "border-cue-success/50 bg-cue-success/10 text-cue-success"
          : "border-cue-attention/50 bg-cue-attention/10 text-cue-attention"
      }`}
    >
      {ok ? yes : no}
    </li>
  );
}

function Locked({ title }: { title: string }) {
  return (
    <section className="card p-5" aria-label={title}>
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="mt-2 text-sm text-ink-muted">
        Accept the privacy policy in{" "}
        <Link className="text-link underline" to="/settings">
          Settings
        </Link>{" "}
        first. Reporting reads and writes under your Google authorization, so Hexton
        will not offer it until that agreement is on record.
      </p>
    </section>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  // ISO timestamps are rendered in the operator's own zone like everywhere else.
  if (/^\d{4}-\d{2}-\d{2}T/.test(text)) return formatWhen(text);
  return text;
}
