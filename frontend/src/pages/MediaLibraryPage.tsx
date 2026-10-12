import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { Media } from "../api/types";
import {
  useAddMedia,
  useCheckMedia,
  useDeleteMedia,
  useMedia,
  useMediaJobs,
  useRecheckMedia,
} from "../hooks/api";
import { formatBytes, formatDuration, formatWhen } from "../lib/state";

/**
 * Media library.
 *
 * Every clip this application is allowed to upload, with the evidence that makes it
 * safe to upload: its digest, its container, and when it was last verified against the
 * file on disk. Re-checking re-hashes the file rather than trusting the stored row,
 * because a file that changed after it was approved is exactly the case that must not
 * slip through — the worker applies the same rule before it uploads.
 *
 * Deletion asks the backend which jobs point at a clip *first*. Refusing a delete
 * after the operator has already confirmed it is a worse experience than telling them
 * up front that the clip is in use, and the backend enforces it either way.
 */
export function MediaLibraryPage() {
  const media = useMedia();
  const [path, setPath] = useState("");
  const [note, setNote] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const check = useCheckMedia();
  const add = useAddMedia();

  const addMedia = async () => {
    setNote(null);
    try {
      const report = await check.mutateAsync(path.trim());
      if (!report.exists) {
        setNote({ kind: "error", text: `That file does not exist: ${report.path}` });
        return;
      }
      const blocking = report.issues.find((i) => i.blocking);
      if (blocking) {
        setNote({ kind: "error", text: blocking.message });
        return;
      }
      await add.mutateAsync(path.trim());
      setNote({ kind: "ok", text: `Added ${report.path}` });
      setPath("");
    } catch (exc) {
      setNote({ kind: "error", text: exc instanceof ApiError ? exc.message : "That file could not be added." });
    }
  };

  const rows = media.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Media Library</h1>
        <p className="mt-1 text-sm text-ink-muted">
          {rows.length} clip{rows.length === 1 ? "" : "s"} registered on this PC. A clip is
          re-hashed before every upload; if the file changes after you queue it, the job
          is blocked rather than publishing different content under an approved title.
        </p>
      </header>

      <section className="card p-5">
        <h2 className="text-base font-semibold">Add a clip</h2>
        <p className="mt-1 text-sm text-ink-muted">
          Hexton reads a path you give it. It never scans your folders.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="media-path">
            Full path to the video file
          </label>
          <input
            id="media-path"
            className="input max-w-xl flex-1"
            placeholder="C:\Videos\clip.mp4"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && path.trim()) void addMedia();
            }}
          />
          <button
            className="btn"
            disabled={check.isPending || !path.trim()}
            onClick={() =>
              check.mutate(path.trim(), {
                onError: (exc) =>
                  setNote({
                    kind: "error",
                    text: exc instanceof ApiError ? exc.message : "The file could not be checked.",
                  }),
              })
            }
          >
            Check file
          </button>
          <button className="btn btn-primary" disabled={add.isPending || !path.trim()} onClick={() => void addMedia()}>
            Add to library
          </button>
        </div>

        {note && (
          <p
            role="status"
            className={`mt-3 rounded-md border px-3 py-2 text-sm ${
              note.kind === "ok"
                ? "border-cue-success/40 bg-cue-success/10 text-cue-success"
                : "border-cue-warning/50 bg-cue-warning/10 text-cue-warning"
            }`}
          >
            {note.text}
          </p>
        )}

        {check.data && !note && (
          <div className="mt-3 rounded-md border border-edge bg-canvas p-3 text-xs text-ink-muted">
            <div>
              SHA-256{" "}
              <code className="font-mono text-ink">{check.data.sha256?.slice(0, 24) ?? "—"}…</code>
            </div>
            <div>
              {formatBytes(check.data.size_bytes)} · {check.data.container ?? "unknown container"}
              {check.data.duration_seconds !== null && ` · ${formatDuration(check.data.duration_seconds)}`}
            </div>
            {check.data.issues.map((issue) => (
              <div key={issue.code} className={issue.blocking ? "text-cue-warning" : "text-cue-attention"}>
                {issue.message}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card overflow-hidden">
        <div className="border-b border-edge px-5 py-4">
          <h2 className="text-base font-semibold">Registered clips</h2>
        </div>
        {media.isLoading ? (
          <p className="px-5 py-6 text-sm text-ink-muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="px-5 py-10 text-center text-sm text-ink-faint">
            No clips yet. Add one above to start building a schedule.
          </p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">
              Registered video clips with their digest, container and validation state.
            </caption>
            <thead>
              <tr className="border-b border-edge text-left text-xs uppercase tracking-wide text-ink-faint">
                <th scope="col" className="px-5 py-3 font-medium">Clip</th>
                <th scope="col" className="px-3 py-3 font-medium">Size</th>
                <th scope="col" className="px-3 py-3 font-medium">Container</th>
                <th scope="col" className="px-3 py-3 font-medium">Last verified</th>
                <th scope="col" className="px-3 py-3 font-medium">State</th>
                <th scope="col" className="px-5 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <MediaRow key={row.id} media={row} />
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function MediaRow({ media }: { media: Media }) {
  const recheck = useRecheckMedia();
  const remove = useDeleteMedia();
  const [expanded, setExpanded] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Only asked for once the operator shows an intent to delete: the count of jobs
  // pointing at a clip is a question worth answering, not one worth asking on load.
  const jobsQuery = useMediaJobs(expanded ? media.id : null);

  const attemptDelete = async () => {
    setMessage(null);
    const affected = jobsQuery.data?.total ?? 0;
    if (affected > 0) {
      setExpanded(true);
      setMessage(
        `${affected} job${affected === 1 ? "" : "s"} still use${affected === 1 ? "s" : ""} this clip. ` +
          "Cancel or delete them first — Hexton will not remove a file that is scheduled.",
      );
      return;
    }
    if (!window.confirm(`Remove ${media.original_name || media.path} from the library?`)) return;
    try {
      await remove.mutateAsync(media.id);
    } catch (exc) {
      setMessage(exc instanceof ApiError ? exc.message : "That clip could not be removed.");
    }
  };

  return (
    <tr className="border-b border-edge/60 align-top last:border-0">
      <td className="px-5 py-3">
        <div className="font-medium">{media.original_name || media.path}</div>
        <div className="mt-0.5 break-all text-xs text-ink-faint">{media.path}</div>
        <div className="mt-0.5 font-mono text-[11px] text-ink-faint">
          {media.sha256.slice(0, 20)}…
        </div>
      </td>
      <td className="px-3 py-3 text-ink-muted">
        {formatBytes(media.size_bytes)}
        {media.duration_seconds !== null && (
          <div className="text-xs text-ink-faint">{formatDuration(media.duration_seconds)}</div>
        )}
      </td>
      <td className="px-3 py-3 text-ink-muted">{media.container ?? "—"}</td>
      <td className="px-3 py-3 text-ink-muted">
        {media.verified_at ? formatWhen(media.verified_at) : "Never"}
        {media.validated_at && (
          <div className="text-xs text-ink-faint">validated {formatWhen(media.validated_at)}</div>
        )}
      </td>
      <td className="px-3 py-3">
        {media.is_valid ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-cue-success/50 bg-cue-success/10 px-2.5 py-0.5 text-xs font-medium text-cue-success">
            Valid
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-cue-warning/50 bg-cue-warning/10 px-2.5 py-0.5 text-xs font-medium text-cue-warning">
            Needs review
          </span>
        )}
      </td>
      <td className="px-5 py-3">
        <div className="flex flex-wrap gap-2">
          <Link className="btn px-2.5 py-1 text-xs" to={`/new?media=${media.id}`}>
            Create content
          </Link>
          <button
            className="btn px-2.5 py-1 text-xs"
            disabled={recheck.isPending}
            onClick={() =>
              recheck.mutate(media.id, {
                onError: (exc) =>
                  setMessage(exc instanceof ApiError ? exc.message : "That clip could not be re-checked."),
              })
            }
          >
            Re-check
          </button>
          <button className="btn btn-danger px-2.5 py-1 text-xs" onClick={() => void attemptDelete()}>
            Remove
          </button>
        </div>
        {message && <p className="mt-2 max-w-md text-xs text-cue-attention">{message}</p>}
        {expanded && jobsQuery.data && jobsQuery.data.total > 0 && (
          <ul className="mt-2 flex max-w-md flex-col gap-1 text-xs text-ink-muted">
            {jobsQuery.data.items.map((job) => (
              <li key={job.id} className="truncate">
                {job.title || "(untitled)"} · {job.display_label}
              </li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  );
}
