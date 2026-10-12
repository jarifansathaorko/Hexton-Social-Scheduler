import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import type { JobInput, Media, ValidateResult } from "../api/types";
import {
  useCheckMedia,
  useConnections,
  useCreateJob,
  useJob,
  useMedia,
  useSettings,
  useUpdateJob,
  useValidateJob,
} from "../hooks/api";
import { browserZone, formatBytes, formatDuration, toLocalInput } from "../lib/state";

const CATEGORIES: [string, string][] = [
  ["22", "People & blogs"],
  ["24", "Entertainment"],
  ["26", "Howto & style"],
  ["27", "Education"],
  ["28", "Science & technology"],
];

/**
 * The content editor.
 *
 * Validation runs against the same rules the worker will apply before it uploads,
 * through `/api/jobs/validate`. That matters more than it looks: an editor that only
 * checks its own guesses lets an operator save something the queue will later refuse,
 * which is how a job ends up silently parked. The schedule problems are shown as
 * errors rather than warnings, because a publication time in the past is not a
 * preference — YouTube would publish immediately.
 */
export function EditorPage() {
  const params = useParams();
  const navigate = useNavigate();
  const jobId = params.jobId ? Number(params.jobId) : null;

  const existing = useJob(jobId);
  const media = useMedia();
  const connections = useConnections();
  const settings = useSettings();

  const [form, setForm] = useState<JobInput | null>(null);
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidateResult | null>(null);

  const checkMedia = useCheckMedia();
  const createJob = useCreateJob();
  const updateJob = useUpdateJob(jobId ?? 0);
  const validate = useValidateJob();

  useEffect(() => {
    if (form) return;
    if (jobId && existing.data) {
      const job = existing.data.job;
      setForm({
        media_id: job.media_id,
        title: job.title,
        description: job.description,
        tags: job.tags,
        hashtags: job.hashtags,
        category_id: job.category_id,
        privacy: job.privacy as JobInput["privacy"],
        connection_id: job.connection_id,
        channel_identity_id: job.channel_identity_id,
        publish_at_utc: job.publish_at_utc,
        display_timezone: job.display_timezone,
        min_lead_minutes: settings.data?.min_lead_minutes ?? null,
        mention_requested: job.mention_requested,
        consent_transforms: false,
      });
      setPath(job.media_path);
    } else if (!jobId && media.data) {
      setForm({
        media_id: 0,
        title: "",
        description: "",
        tags: [],
        hashtags: [],
        category_id: "22",
        privacy: "private",
        connection_id: null,
        channel_identity_id: null,
        publish_at_utc: null,
        display_timezone: browserZone(),
        min_lead_minutes: settings.data?.min_lead_minutes ?? null,
        mention_requested: false,
        consent_transforms: false,
      });
    }
  }, [jobId, existing.data, media.data, settings.data, form]);

  // Live validation. Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    if (!form || !form.title.trim()) {
      setValidation(null);
      return;
    }
    const timer = setTimeout(() => {
      validate.mutate(form, {
        onSuccess: setValidation,
        onError: () => setValidation(null),
      });
    }, 400);
    return () => clearTimeout(timer);
  }, [form]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedMedia = useMemo(
    () => media.data?.find((m) => m.id === form?.media_id) ?? null,
    [media.data, form?.media_id],
  );

  const activeConnection = connections.data?.find((c) => c.id === form?.connection_id) ?? null;
  const channels = activeConnection?.channels ?? [];

  if (!form) {
    return <p className="text-ink-muted">{jobId ? "Loading…" : "Preparing the editor…"}</p>;
  }

  const patch = (next: Partial<JobInput>) => setForm((current) => ({ ...current!, ...next }));

  const blocking = [
    ...(validation?.issues ?? []).filter((i) => i.severity === "error"),
    ...(validation?.schedule_issues ?? []).filter((i) => i.severity === "error"),
  ];
  const warnings = [
    ...(validation?.issues ?? []).filter((i) => i.severity === "warning"),
    ...(validation?.schedule_issues ?? []).filter((i) => i.severity === "warning"),
  ];
  const ready = form.title.trim() && form.media_id > 0 && blocking.length === 0;

  const save = async (queue: boolean) => {
    setError(null);
    try {
      if (jobId) {
        await updateJob.mutateAsync(form);
        navigate(`/content?job=${jobId}`);
      } else {
        const created = await createJob.mutateAsync({ input: form, queue });
        navigate(queue ? "/queue" : `/content?job=${created.id}`);
      }
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "The job could not be saved.");
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          {jobId ? "Edit content" : "New content"}
        </h1>
        <p className="mt-1 text-sm text-ink-muted">
          Nothing is uploaded until you queue it. Private is the default visibility and
          is only changed if you choose otherwise.
        </p>
      </header>

      {error && (
        <p role="alert" className="rounded-md border border-cue-warning/50 bg-cue-warning/10 px-3 py-2 text-sm text-cue-warning">
          {error}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex flex-col gap-6">
          <section className="card p-5">
            <h2 className="text-base font-semibold">Source clip</h2>
            <div className="mt-3 flex flex-col gap-3">
              <div className="flex gap-2">
                <label className="sr-only" htmlFor="media-path">
                  Full path to the video file
                </label>
                <input
                  id="media-path"
                  className="input"
                  placeholder="C:\Videos\clip.mp4"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                />
                <button
                  className="btn"
                  disabled={checkMedia.isPending || !path.trim()}
                  onClick={() =>
                    checkMedia.mutate(path.trim(), {
                      onSuccess: (report) => {
                        if (!report.exists) {
                          setError(`That file does not exist: ${report.path}`);
                          return;
                        }
                        const blockingIssue = report.issues.find((i) => i.blocking);
                        if (blockingIssue) {
                          setError(blockingIssue.message);
                          return;
                        }
                        setError(null);
                        // Registering happens on save; here we only surface the digest
                        // and the container the file actually declared.
                      },
                      onError: (exc) =>
                        setError(exc instanceof ApiError ? exc.message : "The file could not be checked."),
                    })
                  }
                >
                  Check file
                </button>
              </div>

              {checkMedia.data && (
                <div className="rounded-md border border-edge bg-canvas p-3 text-xs text-ink-muted">
                  <div>
                    SHA-256{" "}
                    <code className="font-mono text-ink">{checkMedia.data.sha256?.slice(0, 24) ?? "—"}…</code>
                  </div>
                  <div>
                    {formatBytes(checkMedia.data.size_bytes)} · {checkMedia.data.container ?? "unknown container"}
                    {checkMedia.data.duration_seconds !== null &&
                      ` · ${formatDuration(checkMedia.data.duration_seconds)}`}
                  </div>
                  {checkMedia.data.issues.length > 0 && (
                    <ul className="mt-2 flex flex-col gap-1">
                      {checkMedia.data.issues.map((issue) => (
                        <li key={issue.code} className={issue.blocking ? "text-cue-warning" : "text-cue-attention"}>
                          {issue.message}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {(media.data?.length ?? 0) > 0 && (
                <div>
                  <span className="label">Or pick from the library</span>
                  <ul className="flex flex-col gap-1">
                    {media.data?.map((m) => (
                      <li key={m.id}>
                        <button
                          className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm ${
                            form.media_id === m.id
                              ? "border-cue-progress bg-cue-progress/10"
                              : "border-edge hover:border-edge-strong"
                          }`}
                          onClick={() => {
                            patch({ media_id: m.id });
                            setPath(m.path);
                          }}
                        >
                          <span className="truncate">{m.original_name || m.path}</span>
                          <span className="ml-3 shrink-0 text-xs text-ink-faint">
                            {formatBytes(m.size_bytes)}
                            {m.duration_seconds !== null && ` · ${formatDuration(m.duration_seconds)}`}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {selectedMedia && (
                <p className="text-xs text-ink-faint">
                  The file is re-hashed before every upload. If it changes after you
                  queue it, the job is blocked rather than publishing different content
                  under an approved title.
                </p>
              )}
            </div>
          </section>

          <section className="card p-5">
            <h2 className="text-base font-semibold">Metadata</h2>
            <div className="mt-3 flex flex-col gap-4">
              <div>
                <label className="label" htmlFor="title">
                  Title
                </label>
                <input
                  id="title"
                  className="input"
                  maxLength={400}
                  value={form.title}
                  onChange={(e) => patch({ title: e.target.value })}
                />
                <p className="mt-1 text-xs text-ink-faint">
                  {form.title.length}/100 characters before YouTube rejects it.
                </p>
              </div>

              <div>
                <label className="label" htmlFor="description">
                  Description
                </label>
                <textarea
                  id="description"
                  className="input min-h-[140px] resize-y"
                  value={form.description}
                  onChange={(e) => patch({ description: e.target.value })}
                />
                <p className="mt-1 text-xs text-ink-faint">
                  {new Blob([form.description]).size}/5000 bytes.
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="label" htmlFor="tags">
                    Tags (comma separated)
                  </label>
                  <input
                    id="tags"
                    className="input"
                    value={form.tags.join(", ")}
                    onChange={(e) =>
                      patch({ tags: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) })
                    }
                  />
                </div>
                <div>
                  <label className="label" htmlFor="hashtags">
                    Hashtags
                  </label>
                  <input
                    id="hashtags"
                    className="input"
                    placeholder="#shorts #tutorial"
                    value={form.hashtags.join(" ")}
                    onChange={(e) =>
                      patch({ hashtags: e.target.value.split(/\s+/).map((t) => t.trim()).filter(Boolean) })
                    }
                  />
                </div>
              </div>

              <label className="flex items-start gap-2 text-sm text-ink-muted">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={form.consent_transforms}
                  onChange={(e) => patch({ consent_transforms: e.target.checked })}
                />
                <span>
                  Let Hexton normalise hashtags (lowercase, de-duplicated) and append
                  them to the description. Off by default: your text is sent exactly as
                  written unless you turn this on, and every change is listed in the
                  job's history.
                </span>
              </label>

              <div>
                <label className="label" htmlFor="category">
                  Category
                </label>
                <select
                  id="category"
                  className="input"
                  value={form.category_id}
                  onChange={(e) => patch({ category_id: e.target.value })}
                >
                  {CATEGORIES.map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </section>

          <section className="card p-5">
            <h2 className="text-base font-semibold">Publication</h2>
            <div className="mt-3 flex flex-col gap-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="label" htmlFor="privacy">
                    Visibility
                  </label>
                  <select
                    id="privacy"
                    className="input"
                    value={form.privacy}
                    onChange={(e) => patch({ privacy: e.target.value as JobInput["privacy"] })}
                  >
                    <option value="private">Private (default)</option>
                    <option value="unlisted">Unlisted</option>
                    <option value="public">Public</option>
                  </select>
                  <p className="mt-1 text-xs text-ink-faint">
                    Always sent explicitly. Hexton never relies on a server-side default.
                  </p>
                </div>
                <div>
                  <label className="label" htmlFor="publish-at">
                    Publication time ({form.display_timezone ?? browserZone()})
                  </label>
                  <input
                    id="publish-at"
                    type="datetime-local"
                    className="input"
                    value={toLocalInput(form.publish_at_utc)}
                    onChange={(e) =>
                      patch({
                        publish_at_utc: e.target.value ? new Date(e.target.value).toISOString() : null,
                      })
                    }
                  />
                </div>
              </div>

              <label className="flex items-start gap-2 text-sm text-ink-muted">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={form.mention_requested}
                  onChange={(e) => patch({ mention_requested: e.target.checked })}
                />
                <span>
                  This video mentions another creator. Hexton cannot add a mention
                  through the API — there is no mention entity — so it will ask you to
                  confirm that you added it in YouTube Studio.
                </span>
              </label>
            </div>
          </section>
        </div>

        <aside className="flex flex-col gap-4">
          <section className="card p-5">
            <h2 className="text-base font-semibold">Target</h2>
            <div className="mt-3 flex flex-col gap-4">
              <div>
                <label className="label" htmlFor="connection">
                  Connection
                </label>
                <select
                  id="connection"
                  className="input"
                  value={form.connection_id ?? ""}
                  onChange={(e) =>
                    patch({
                      connection_id: e.target.value ? Number(e.target.value) : null,
                      channel_identity_id: null,
                    })
                  }
                >
                  <option value="">None selected</option>
                  {connections.data?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.label} ({c.status})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label" htmlFor="channel">
                  Channel
                </label>
                <select
                  id="channel"
                  className="input"
                  value={form.channel_identity_id ?? ""}
                  onChange={(e) =>
                    patch({ channel_identity_id: e.target.value ? Number(e.target.value) : null })
                  }
                >
                  <option value="">None selected</option>
                  {channels.map((ch) => (
                    <option key={ch.id} value={ch.id}>
                      {ch.title} {ch.handle ? `(${ch.handle})` : ""}
                      {ch.operator_confirmed_at ? " ✓" : ""}
                    </option>
                  ))}
                </select>
                {channels.length === 0 && form.connection_id && (
                  <p className="mt-1 text-xs text-cue-attention">
                    No verified channel on this connection yet. Refresh identities from
                    Settings.
                  </p>
                )}
              </div>
            </div>
          </section>

          <section className="card p-5">
            <h2 className="text-base font-semibold">Validation</h2>
            {blocking.length === 0 && warnings.length === 0 && (
              <p className="mt-3 text-sm text-ink-faint">
                {validation ? "No problems found." : "Add a title to check this job."}
              </p>
            )}
            {blocking.length > 0 && (
              <ul className="mt-3 flex flex-col gap-2">
                {blocking.map((issue, index) => (
                  <li
                    key={`${issue.code}-${index}`}
                    className="rounded-md border border-cue-warning/40 bg-cue-warning/5 px-3 py-2 text-xs text-cue-warning"
                  >
                    {issue.message}
                  </li>
                ))}
              </ul>
            )}
            {warnings.length > 0 && (
              <ul className="mt-3 flex flex-col gap-2">
                {warnings.map((issue, index) => (
                  <li
                    key={`${issue.code}-${index}`}
                    className="rounded-md border border-cue-attention/40 bg-cue-attention/5 px-3 py-2 text-xs text-cue-attention"
                  >
                    {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className="flex flex-col gap-2">
            <button className="btn btn-primary" disabled={!ready} onClick={() => void save(false)}>
              Save draft
            </button>
            <button className="btn" disabled={!ready} onClick={() => void save(true)}>
              Save and queue
            </button>
            <button className="btn" onClick={() => navigate(-1)}>
              Cancel
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}

export type { Media };
