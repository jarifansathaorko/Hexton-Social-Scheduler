import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import type { ChannelSearchResult, JobInput, Media, ValidateResult } from "../api/types";
import { ChannelMentionDropdown } from "../components/ChannelMentionDropdown";
import { MENTION_NAMES } from "../lib/state";
import {
  IconAlertTriangle,
  IconCheck,
  IconChevronRight,
  IconRefreshCw,
  IconUpload,
} from "../components/Icons";
import {
  useAddMedia,
  useChannelSearch,
  useCheckMedia,
  useConfirmChannel,
  useConnections,
  useCreateJob,
  useJob,
  useMedia,
  useMention,
  useQueueJob,
  useSettings,
  useSmartSlots,
  useUpdateJob,
  useUploadMedia,
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
 * Content Editor / Create Content Workflow.
 *
 * Implements Panel 9 of the HexScheduler visual reference:
 * - 4-step wizard header: 1. Select Video -> 2. Settings -> 3. Schedule -> 4. Review
 * - Modern video dropzone with "Choose File" CTA and pre-flight SHA-256 verification
 * - Preserves all invariant validation: checks against `/api/jobs/validate` rules
 * - Privacy defaults to private on all new creations
 * - Strict target channel identity confirmation checks
 */
export function EditorPage() {
  const params = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const jobId = params.jobId ? Number(params.jobId) : null;
  const mediaParam = Number(search.get("media") ?? 0);

  const existing = useJob(jobId);
  const media = useMedia();
  const connections = useConnections();
  const settings = useSettings();

  const [form, setForm] = useState<JobInput | null>(null);
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidateResult | null>(null);
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Mention autocomplete state
  const [mentionTarget, setMentionTarget] = useState<"title" | "description" | null>(null);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionIndex, setMentionIndex] = useState<{ atIndex: number; cursor: number } | null>(null);
  const [selectedMentionIdx, setSelectedMentionIdx] = useState(0);

  const titleInputRef = useRef<HTMLInputElement>(null);
  const descTextareaRef = useRef<HTMLTextAreaElement>(null);

  const channelSearch = useChannelSearch(mentionQuery, Boolean(mentionTarget && mentionQuery));
  const channelResults = useMemo(() => channelSearch.data ?? [], [channelSearch.data]);

  const displayedChannels = useMemo(() => {
    const clean = mentionQuery.replace(/^@+/, "").trim();
    if (!clean) return channelResults;

    const hasExact = channelResults.some(
      (c) => c.handle.toLowerCase() === clean.toLowerCase()
    );

    if (hasExact) {
      return channelResults;
    }

    const directOption: ChannelSearchResult = {
      channel_id: `custom_${clean}`,
      title: `@${clean}`,
      handle: clean,
      thumbnail_url: "",
      subscriber_count: "Direct handle",
      verified: false,
    };

    if (channelResults.length > 0) {
      return [...channelResults, directOption];
    }

    if (channelSearch.isLoading) {
      return [];
    }

    return [directOption];
  }, [channelResults, mentionQuery, channelSearch.isLoading]);

  const checkMentionTrigger = (
    text: string,
    cursor: number,
    target: "title" | "description"
  ) => {
    const textBeforeCursor = text.slice(0, cursor);
    const lastAtIndex = textBeforeCursor.lastIndexOf("@");
    if (lastAtIndex !== -1) {
      const charBeforeAt = (lastAtIndex > 0 ? textBeforeCursor[lastAtIndex - 1] : " ") ?? " ";
      if (/\s/.test(charBeforeAt) || lastAtIndex === 0) {
        const query = textBeforeCursor.slice(lastAtIndex + 1);
        if (!/\s/.test(query) && query.length >= 1) {
          setMentionTarget(target);
          setMentionQuery(query);
          setMentionIndex({ atIndex: lastAtIndex, cursor });
          setSelectedMentionIdx(0);
          return;
        }
      }
    }
    setMentionTarget(null);
    setMentionQuery("");
    setMentionIndex(null);
  };

  const handleSelectMention = (channel: ChannelSearchResult) => {
    if (!mentionTarget || !mentionIndex || !form) return;
    const currentText = mentionTarget === "title" ? form.title : form.description;
    
    // Replace from mentionIndex.atIndex up to the end of the current mention token
    const afterAt = currentText.slice(mentionIndex.atIndex + 1);
    const match = afterAt.match(/^(\S*)/);
    const tokenLen = match && match[1] ? match[1].length : 0;
    const replaceEnd = mentionIndex.atIndex + 1 + tokenLen;

    const before = currentText.slice(0, mentionIndex.atIndex);
    const after = currentText.slice(replaceEnd);
    const replacement = `@${channel.handle} `;
    const cleanAfter = after.startsWith(" ") ? after.slice(1) : after;
    const newText = before + replacement + cleanAfter;
    const newCursor = before.length + replacement.length;

    patch({ [mentionTarget]: newText, mention_requested: true });
    setMentionTarget(null);
    setMentionQuery("");
    setMentionIndex(null);

    setTimeout(() => {
      const el = mentionTarget === "title" ? titleInputRef.current : descTextareaRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(newCursor, newCursor);
      }
    }, 10);
  };

  const handleMentionKeyDown = (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (!mentionTarget) return;
    const clean = mentionQuery.replace(/^@+/, "").trim();
    const count = displayedChannels.length;

    if (e.key === "ArrowDown") {
      if (count > 0) {
        e.preventDefault();
        setSelectedMentionIdx((prev) => (prev + 1) % count);
      }
    } else if (e.key === "ArrowUp") {
      if (count > 0) {
        e.preventDefault();
        setSelectedMentionIdx((prev) => (prev - 1 + count) % count);
      }
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      const chosen = displayedChannels[selectedMentionIdx];
      if (chosen) {
        handleSelectMention(chosen);
      } else if (clean) {
        handleSelectMention({
          channel_id: `custom_${clean}`,
          title: `@${clean}`,
          handle: clean,
          thumbnail_url: "",
          subscriber_count: "Direct handle",
          verified: false,
        });
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setMentionTarget(null);
      setMentionQuery("");
      setMentionIndex(null);
    }
  };

  const checkMedia = useCheckMedia();
  const addMedia = useAddMedia();
  const uploadMedia = useUploadMedia();
  const createJob = useCreateJob();
  const updateJob = useUpdateJob(jobId ?? 0);
  const queueJob = useQueueJob();
  const validate = useValidateJob();
  const confirmChannel = useConfirmChannel();
  const smartSlots = useSmartSlots(form?.channel_identity_id ? String(form.channel_identity_id) : null);

  const handleValidateAndRegister = async (targetPath: string) => {
    const trimmed = targetPath.trim();
    if (!trimmed) return;
    setError(null);
    try {
      const report = await checkMedia.mutateAsync(trimmed);
      if (!report.exists) {
        setError(`That file does not exist: ${report.path}`);
        return;
      }
      const blockingIssue = report.issues.find((i) => i.blocking);
      if (blockingIssue) {
        setError(blockingIssue.message);
        return;
      }
      const saved = await addMedia.mutateAsync(trimmed);
      const updates: Partial<JobInput> = { media_id: saved.id };
      if (!form?.title.trim() && saved.original_name) {
        updates.title = saved.original_name.replace(/\.[^/.]+$/, "");
      }
      patch(updates);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "The file could not be checked or added.");
    }
  };

  const handleBrowserFile = async (file: File) => {
    setError(null);
    setIsUploading(true);
    try {
      const saved = await uploadMedia.mutateAsync({ file });
      setPath(saved.path);
      const updates: Partial<JobInput> = { media_id: saved.id };
      if (!form?.title.trim() && saved.original_name) {
        updates.title = saved.original_name.replace(/\.[^/.]+$/, "");
      }
      patch(updates);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "The video file could not be uploaded.");
    } finally {
      setIsUploading(false);
    }
  };

  const handlePickFile = () => {
    setError(null);
    // Trigger native browser File Explorer dialog directly in the user session
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
      fileInputRef.current.click();
    }
  };

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
      const preselected = media.data.find((m) => m.id === mediaParam) ?? null;
      setForm({
        media_id: preselected?.id ?? 0,
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
      if (preselected) setPath(preselected.path);
    }
  }, [form, jobId, existing.data, media.data, mediaParam, settings.data]);

  const patch = (patch: Partial<JobInput>) => {
    setForm((current) => (current ? { ...current, ...patch } : null));
  };

  useEffect(() => {
    if (!form) return;
    const timer = setTimeout(async () => {
      try {
        const result = await validate.mutateAsync(form);
        setValidation(result);
      } catch {
        // Validation will be rechecked on save
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [form, validate]);

  const selectedMedia = useMemo(() => {
    if (!form?.media_id) return null;
    return media.data?.find((m) => m.id === form.media_id) ?? null;
  }, [form?.media_id, media.data]);

  const channels = useMemo(() => {
    if (!form?.connection_id) return [];
    const connection = connections.data?.find((c) => c.id === form.connection_id);
    return connection?.channels ?? [];
  }, [form?.connection_id, connections.data]);

  const save = async (queue: boolean) => {
    if (!form) return;
    setError(null);
    try {
      if (jobId) {
        await updateJob.mutateAsync(form);
        if (queue) {
          await queueJob.mutateAsync({ id: jobId });
        }
        navigate(`/calendar?job=${jobId}`);
      } else {
        const created = await createJob.mutateAsync({ input: form, queue });
        navigate(`/calendar?job=${created.id}`);
      }
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "Could not save schedule.");
    }
  };

  if (!form) {
    return (
      <div className="py-20 text-center">
        <p className="text-sm text-[var(--hs-text-muted)] animate-pulse">Loading content editor…</p>
      </div>
    );
  }

  const blocking = validation?.issues.filter((i) => i.blocking) ?? [];
  const warnings = validation?.issues.filter((i) => !i.blocking) ?? [];
  const ready = form.title.trim().length > 0 && blocking.length === 0;

  return (
    <div className="flex flex-col gap-6">
      {/* Page Header */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            {jobId ? "Edit schedule" : "New content"}
          </h1>
          <p className="mt-1 text-sm text-[var(--hs-text-secondary)]">
            Configure metadata, verify target channel confirmation, and schedule publication time.
          </p>
        </div>

        {/* Stepper matching Panel 9 */}
        <div className="hidden md:flex items-center gap-2 rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] px-4 py-2">
          {(
            [
              { num: 1, label: "Select Video" },
              { num: 2, label: "Settings" },
              { num: 3, label: "Schedule" },
              { num: 4, label: "Review" },
            ] as const
          ).map((s, idx) => (
            <div key={s.num} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setStep(s.num)}
                className={`flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded-md transition-colors ${
                  step === s.num
                    ? "bg-[var(--hs-violet)] text-white"
                    : "text-[var(--hs-text-muted)] hover:text-white"
                }`}
              >
                <span className="grid h-4 w-4 place-items-center rounded-full bg-white/20 text-[10px] font-mono">
                  {s.num}
                </span>
                <span>{s.label}</span>
              </button>
              {idx < 3 && <IconChevronRight size={13} className="text-[var(--hs-border)]" />}
            </div>
          ))}
        </div>
      </header>

      {error && (
        <div role="alert" className="rounded-xl border border-[var(--hs-danger)]/40 bg-[var(--hs-danger)]/10 px-4 py-3 text-xs text-[var(--hs-danger)] flex items-center gap-2">
          <IconAlertTriangle size={15} />
          <span>{error}</span>
        </div>
      )}

      {/* Main 2-column layout */}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
        {/* Left Column: Form Details */}
        <div className="flex flex-col gap-6">
          {/* Step 1: Video File Selection matching Panel 9 dropzone */}
          <section className="hs-card p-6" aria-label="Media selection">
            <div className="flex items-center justify-between border-b border-[var(--hs-border)]/60 pb-3 mb-4">
              <div className="flex items-center gap-2">
                <span className="grid h-5 w-5 place-items-center rounded-full bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] text-xs font-bold">1</span>
                <h2 className="text-base font-bold text-white tracking-tight">Select Video File</h2>
              </div>
              {selectedMedia && (
                <span className="text-xs font-medium text-[var(--hs-success)] flex items-center gap-1">
                  <IconCheck size={13} /> {selectedMedia.original_name || "File attached"}
                </span>
              )}
            </div>

            {/* Hidden native browser file input to open Windows Explorer immediately */}
            <input
              ref={fileInputRef}
              type="file"
              accept="video/mp4,video/quicktime,video/x-matroska,video/webm,video/*"
              className="hidden"
              tabIndex={-1}
              aria-hidden="true"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleBrowserFile(f);
              }}
            />

            {/* Video File Dropzone Box matching Panel 9 */}
            <div
              className={`rounded-2xl border-2 border-dashed p-8 text-center transition-all cursor-pointer select-none ${
                isDragging
                  ? "border-[var(--hs-violet-bright)] bg-[var(--hs-violet-soft)]/50 ring-2 ring-[var(--hs-violet-bright)]/60 scale-[1.01]"
                  : selectedMedia
                  ? "border-[var(--hs-violet)]/60 bg-[var(--hs-violet-soft)]/20"
                  : "border-[var(--hs-border)] bg-[var(--hs-surface-raised)]/40 hover:border-[var(--hs-violet-bright)]/70 hover:bg-[var(--hs-surface-raised)]/70"
              }`}
              onDragOver={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsDragging(true);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsDragging(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setIsDragging(false);
                const f = e.dataTransfer.files?.[0];
                if (f) void handleBrowserFile(f);
              }}
              onClick={handlePickFile}
            >
              <div className="grid h-12 w-12 place-items-center rounded-full bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] mx-auto mb-3 shadow-md">
                <IconUpload size={22} />
              </div>
              <p className="text-sm font-semibold text-white">Select a video file</p>
              <p className="mt-1 text-xs text-[var(--hs-text-muted)]">
                Drag and drop a file here, or click anywhere to browse. Supports MP4, MOV, MKV (max 10GB).
              </p>
              <div className="mt-4 flex flex-wrap items-center justify-center gap-3">
                <button
                  type="button"
                  className="btn btn-primary text-xs py-2 px-4 rounded-xl font-semibold shadow-md flex items-center gap-2 cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    handlePickFile();
                  }}
                  disabled={isUploading}
                >
                  {isUploading ? (
                    <>
                      <IconRefreshCw size={13} className="animate-spin text-white" />
                      <span>Attaching video file...</span>
                    </>
                  ) : (
                    <>
                      <IconUpload size={13} />
                      <span>Choose File</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {/* Select from media library clips list */}
            {media.data && media.data.length > 0 && (
              <div className="mt-4">
                <label className="label text-xs">Or pick a clip you registered earlier</label>
                <ul className="mt-2 flex flex-col gap-2 max-h-48 overflow-y-auto pr-1">
                  {media.data.map((m) => (
                    <li key={m.id}>
                      <button
                        type="button"
                        className={`flex w-full items-center justify-between rounded-xl border p-2.5 text-left text-xs transition-colors ${
                          form.media_id === m.id
                            ? "border-[var(--hs-violet)] bg-[var(--hs-violet-soft)] text-white shadow-sm"
                            : "border-[var(--hs-border)] bg-[var(--hs-surface-raised)] text-[var(--hs-text)] hover:border-[var(--hs-border-strong)]"
                        }`}
                        onClick={() => {
                          patch({ media_id: m.id });
                          setPath(m.path);
                        }}
                      >
                        <span className="truncate font-medium">{m.original_name || m.path}</span>
                        <span className="ml-3 shrink-0 text-xs text-[var(--hs-text-muted)]">
                          {formatBytes(m.size_bytes)}
                          {m.duration_seconds !== null && ` · ${formatDuration(m.duration_seconds)}`}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Path text input fallback */}
            <div className="mt-4">
              <label className="label text-xs" htmlFor="media-path">
                Video file path on your computer
              </label>
              <div className="flex gap-2">
                <input
                  id="media-path"
                  className="input text-xs flex-1 font-mono"
                  placeholder="C:\Videos\clip.mp4"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && path.trim()) void handleValidateAndRegister(path);
                  }}
                />
                <button
                  type="button"
                  className="btn text-xs font-semibold"
                  disabled={checkMedia.isPending || !path.trim()}
                  onClick={() => void handleValidateAndRegister(path)}
                >
                  Check &amp; Attach
                </button>
              </div>
            </div>

            {/* Selected media details preview */}
            {selectedMedia && (
              <div className="mt-4 rounded-xl border border-[var(--hs-border)] bg-[var(--hs-canvas)] p-3 text-xs text-[var(--hs-text-secondary)] flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-white">{selectedMedia.original_name || "clip"}</span>
                  <span className="rounded bg-[var(--hs-surface-soft)] px-1.5 py-0.5 text-[10px] uppercase font-mono">
                    {selectedMedia.container ?? "MP4"}
                  </span>
                </div>
                <div>
                  {formatBytes(selectedMedia.size_bytes)}
                  {selectedMedia.duration_seconds !== null && ` · ${formatDuration(selectedMedia.duration_seconds)}`}
                </div>
              </div>
            )}
          </section>

          {/* Step 2: Content Details & Visibility */}
          <section className="hs-card p-6" aria-label="Content settings">
            <div className="flex items-center justify-between border-b border-[var(--hs-border)]/60 pb-3 mb-4">
              <div className="flex items-center gap-2">
                <span className="grid h-5 w-5 place-items-center rounded-full bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] text-xs font-bold">2</span>
                <h2 className="text-base font-bold text-white tracking-tight">Content Details &amp; Metadata</h2>
              </div>
            </div>

            <div className="flex flex-col gap-4">
              <div className="relative">
                <label className="label text-xs flex items-center justify-between" htmlFor="title">
                  <span>Title (Required)</span>
                  <span className="text-[10px] text-[var(--hs-text-muted)] font-normal">
                    Type <code className="text-[var(--hs-violet-bright)] font-mono">@</code> to mention a channel
                  </span>
                </label>
                <input
                  ref={titleInputRef}
                  id="title"
                  className="input text-xs"
                  placeholder="A compelling title for your video (e.g. ok @fomo)"
                  value={form.title}
                  onChange={(e) => {
                    const val = e.target.value;
                    patch({ title: val });
                    checkMentionTrigger(val, e.target.selectionStart ?? val.length, "title");
                  }}
                  onKeyUp={(e) => {
                    const target = e.currentTarget;
                    checkMentionTrigger(target.value, target.selectionStart ?? target.value.length, "title");
                  }}
                  onKeyDown={handleMentionKeyDown}
                  onBlur={() => {
                    setTimeout(() => {
                      if (mentionTarget === "title") setMentionTarget(null);
                    }, 250);
                  }}
                />
                {mentionTarget === "title" && (
                  <ChannelMentionDropdown
                    channels={displayedChannels}
                    isLoading={channelSearch.isLoading}
                    selectedIndex={selectedMentionIdx}
                    onSelect={handleSelectMention}
                    onClose={() => setMentionTarget(null)}
                    query={mentionQuery}
                  />
                )}
              </div>

              <div className="relative">
                <label className="label text-xs flex items-center justify-between" htmlFor="description">
                  <span>Description</span>
                  <span className="text-[10px] text-[var(--hs-text-muted)] font-normal">
                    Type <code className="text-[var(--hs-violet-bright)] font-mono">@</code> to mention a channel
                  </span>
                </label>
                <textarea
                  ref={descTextareaRef}
                  id="description"
                  rows={4}
                  className="input text-xs"
                  placeholder="Tell viewers about your video..."
                  value={form.description}
                  onChange={(e) => {
                    const val = e.target.value;
                    patch({ description: val });
                    checkMentionTrigger(val, e.target.selectionStart ?? val.length, "description");
                  }}
                  onKeyUp={(e) => {
                    const target = e.currentTarget;
                    checkMentionTrigger(target.value, target.selectionStart ?? target.value.length, "description");
                  }}
                  onKeyDown={handleMentionKeyDown}
                  onBlur={() => {
                    setTimeout(() => {
                      if (mentionTarget === "description") setMentionTarget(null);
                    }, 250);
                  }}
                />
                {mentionTarget === "description" && (
                  <ChannelMentionDropdown
                    channels={displayedChannels}
                    isLoading={channelSearch.isLoading}
                    selectedIndex={selectedMentionIdx}
                    onSelect={handleSelectMention}
                    onClose={() => setMentionTarget(null)}
                    query={mentionQuery}
                  />
                )}
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="label text-xs" htmlFor="privacy">
                    Visibility (Defaults to Private)
                  </label>
                  <select
                    id="privacy"
                    className="input text-xs"
                    value={form.privacy}
                    onChange={(e) => patch({ privacy: e.target.value as JobInput["privacy"] })}
                  >
                    <option value="private">Private (Recommended safe default)</option>
                    <option value="unlisted">Unlisted</option>
                    <option value="public">Public</option>
                  </select>
                </div>

                <div>
                  <label className="label text-xs" htmlFor="category">
                    Category
                  </label>
                  <select
                    id="category"
                    className="input text-xs"
                    value={form.category_id}
                    onChange={(e) => patch({ category_id: e.target.value })}
                  >
                    {CATEGORIES.map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Creator Mentions Checkbox */}
              <div className="rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] p-3.5">
                <label className="flex items-start gap-2.5 cursor-pointer">
                  <input
                    type="checkbox"
                    className="mt-0.5 rounded border-[var(--hs-border)] bg-[var(--hs-canvas)] text-[var(--hs-violet)]"
                    checked={form.mention_requested}
                    onChange={(e) => patch({ mention_requested: e.target.checked })}
                  />
                  <div className="text-xs">
                    <span className="font-semibold text-white block">Request creator mention attribution</span>
                    <span className="text-[var(--hs-text-muted)] text-[11px] block mt-0.5">
                      Attestation is recorded by you. The video description will note the referenced handles.
                    </span>
                  </div>
                </label>
                {jobId !== null && form.mention_requested && <MentionStateNote jobId={jobId} />}
              </div>
            </div>
          </section>

          {/* Step 3: Scheduling Time */}
          <section className="hs-card p-6" aria-label="Schedule settings">
            <div className="flex items-center justify-between border-b border-[var(--hs-border)]/60 pb-3 mb-4">
              <div className="flex items-center gap-2">
                <span className="grid h-5 w-5 place-items-center rounded-full bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] text-xs font-bold">3</span>
                <h2 className="text-base font-bold text-white tracking-tight">Schedule Publication Time</h2>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="label text-xs" htmlFor="publish-at">
                  Publication Time (Configured Time Zone)
                </label>
                <input
                  id="publish-at"
                  type="datetime-local"
                  className="input text-xs"
                  value={form.publish_at_utc ? toLocalInput(form.publish_at_utc) : ""}
                  onChange={(e) => {
                    const localVal = e.target.value;
                    if (!localVal) {
                      patch({ publish_at_utc: null });
                      return;
                    }
                    const d = new Date(localVal);
                    patch({ publish_at_utc: d.toISOString() });
                  }}
                />
                <p className="mt-1 text-[11px] text-[var(--hs-text-muted)]">
                  Leave blank to save as an unscheduled draft.
                </p>
              </div>

              <div>
                <label className="label text-xs">Time Zone Display</label>
                <input
                  type="text"
                  readOnly
                  className="input text-xs bg-[var(--hs-canvas)] cursor-default"
                  value={form.display_timezone || "Asia/Dhaka"}
                />
              </div>
            </div>

            {/* Algorithmic Cadence & Peak Hour Recommendations */}
            <div className="mt-4 pt-4 border-t border-[var(--hs-border)]/60">
              <div className="flex items-center justify-between mb-2.5">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-[var(--hs-violet-bright)] animate-pulse" />
                  <span className="text-xs font-semibold text-white">
                    Algorithmic Cadence &amp; Peak Hour Recommendations
                  </span>
                </div>
                <span className="text-[11px] text-[var(--hs-text-muted)]">
                  Collision-free &bull; Ranked by viewer activity
                </span>
              </div>

              {smartSlots.isLoading ? (
                <div className="text-xs text-[var(--hs-text-muted)] py-2 animate-pulse">
                  Computing audience peak windows &amp; collision clearance…
                </div>
              ) : smartSlots.data && smartSlots.data.length > 0 ? (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {smartSlots.data.slice(0, 3).map((slot) => (
                    <button
                      key={slot.publish_at_utc}
                      type="button"
                      onClick={() => patch({ publish_at_utc: slot.publish_at_utc })}
                      className="group flex flex-col items-start rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)]/60 hover:border-[var(--hs-violet)] hover:bg-[var(--hs-surface-raised)] p-3 text-left transition-all shadow-sm cursor-pointer"
                      title="Click to apply this optimal schedule slot"
                    >
                      <div className="flex items-center justify-between w-full">
                        <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--hs-violet-bright)] bg-violet-950/40 border border-violet-800/40 rounded px-1.5 py-0.5">
                          {slot.score}% Match
                        </span>
                        <span className="text-[10px] text-[var(--hs-text-muted)] font-mono">
                          +{slot.clearance_hours}h gap
                        </span>
                      </div>
                      <span className="text-xs font-semibold text-white mt-1.5 group-hover:text-[var(--hs-violet-bright)] transition-colors">
                        {slot.display_local}
                      </span>
                      <span className="text-[10px] text-[var(--hs-text-muted)] mt-0.5">
                        {slot.label} &bull; Peak {(slot.peak_weight * 100).toFixed(0)}%
                      </span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </section>
        </div>

        {/* Right Column: Target Channel, Validation & Actions */}
        <aside className="flex flex-col gap-5">
          {/* Target Channel Card */}
          <section className="hs-card p-5" aria-label="Target channel">
            <h2 className="text-base font-bold text-white tracking-tight border-b border-[var(--hs-border)]/60 pb-2.5 mb-3">
              Target Channel
            </h2>

            <div className="flex flex-col gap-4">
              <div>
                <label className="label text-xs" htmlFor="connection">
                  YouTube Connection
                </label>
                <select
                  id="connection"
                  className="input text-xs"
                  value={form.connection_id ?? ""}
                  onChange={(e) => {
                    const nextConnId = e.target.value ? Number(e.target.value) : null;
                    const nextConn = connections.data?.find((c) => c.id === nextConnId);
                    const confirmed = nextConn?.channels.filter((ch) => ch.operator_confirmed_at !== null) ?? [];
                    patch({
                      connection_id: nextConnId,
                      channel_identity_id: confirmed.length === 1 && confirmed[0] ? confirmed[0].id : null,
                    });
                  }}
                >
                  <option value="">None selected</option>
                  {connections.data
                    ?.filter((c) => c.provider === "youtube")
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label} ({c.status})
                      </option>
                    ))}
                </select>
              </div>

              <div>
                <label className="label text-xs" htmlFor="channel">
                  Target Channel
                </label>
                <select
                  id="channel"
                  className="input text-xs"
                  value={form.channel_identity_id ?? ""}
                  onChange={(e) =>
                    patch({ channel_identity_id: e.target.value ? Number(e.target.value) : null })
                  }
                >
                  <option value="">None selected</option>
                  {channels
                    .filter((ch) => ch.operator_confirmed_at !== null)
                    .map((ch) => (
                      <option key={ch.id} value={ch.id}>
                        {ch.title} {ch.handle ? `(${ch.handle})` : ""}
                      </option>
                    ))}
                  {channels.some((ch) => ch.operator_confirmed_at === null) && (
                    <optgroup label="Not confirmed — not authorized for this connection">
                      {channels
                        .filter((ch) => ch.operator_confirmed_at === null)
                        .map((ch) => (
                          <option key={ch.id} value={ch.id} disabled>
                            {ch.title} {ch.handle ? `(${ch.handle})` : ""} — unconfirmed
                          </option>
                        ))}
                    </optgroup>
                  )}
                </select>

                {form.channel_identity_id && (
                  <div className="mt-2.5 flex items-center gap-1.5 rounded-lg bg-[var(--hs-success)]/10 border border-[var(--hs-success)]/30 px-3 py-1.5 text-xs text-[var(--hs-success)] font-medium">
                    <IconCheck size={13} />
                    <span>Targets: {channels.find((ch) => ch.id === form.channel_identity_id)?.title}</span>
                  </div>
                )}

                {channels.some((ch) => ch.operator_confirmed_at === null) && (
                  <div className="mt-2.5 rounded-lg border border-[var(--hs-warning)]/40 bg-[var(--hs-warning)]/10 p-3 text-xs flex flex-col gap-2">
                    <span className="text-[var(--hs-warning)] font-medium">
                      {channels.filter((ch) => ch.operator_confirmed_at === null).length} channel(s) require confirmation before publishing.
                    </span>
                    <button
                      type="button"
                      className="btn text-xs font-semibold py-1 px-2.5 bg-[var(--hs-success)]/20 text-[var(--hs-success)] border-[var(--hs-success)]/40 hover:bg-[var(--hs-success)]/30 w-fit"
                      disabled={confirmChannel.isPending}
                      onClick={() => {
                        channels
                          .filter((ch) => ch.operator_confirmed_at === null)
                          .forEach((ch) => confirmChannel.mutate(ch.id));
                      }}
                    >
                      {confirmChannel.isPending ? "Confirming…" : "Confirm all channels"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* Validation Feedback Card */}
          <section className="hs-card p-5" aria-label="Validation">
            <h2 className="text-base font-bold text-white tracking-tight border-b border-[var(--hs-border)]/60 pb-2.5 mb-3">
              Pre-flight Validation
            </h2>
            {blocking.length === 0 && warnings.length === 0 && (
              <p className="text-xs text-[var(--hs-text-muted)] py-2">
                {validation ? "✓ Ready to schedule. No problems found." : "Add a title to validate."}
              </p>
            )}
            {blocking.length > 0 && (
              <ul className="flex flex-col gap-2">
                {blocking.map((issue, index) => (
                  <li
                    key={`${issue.code}-${index}`}
                    className="rounded-lg border border-[var(--hs-danger)]/40 bg-[var(--hs-danger)]/10 px-3 py-2 text-xs text-[var(--hs-danger)] font-medium flex items-center gap-2"
                  >
                    <IconAlertTriangle size={14} className="shrink-0" />
                    <span>{issue.message}</span>
                  </li>
                ))}
              </ul>
            )}
            {warnings.length > 0 && (
              <ul className="mt-2 flex flex-col gap-2">
                {warnings.map((issue, index) => (
                  <li
                    key={`${issue.code}-${index}`}
                    className="rounded-lg border border-[var(--hs-warning)]/40 bg-[var(--hs-warning)]/10 px-3 py-2 text-xs text-[var(--hs-warning)] flex items-center gap-2"
                  >
                    <IconAlertTriangle size={14} className="shrink-0" />
                    <span>{issue.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Action Buttons matching Panel 9 */}
          <div className="flex flex-col gap-2.5 pt-2">
            <button
              className="btn btn-primary py-2.5 w-full justify-center text-sm font-semibold shadow-lg shadow-violet-950/50"
              disabled={!ready}
              onClick={() => void save(false)}
            >
              Save draft
            </button>
            <button
              className="btn btn-secondary py-2.5 w-full justify-center text-sm font-medium"
              disabled={!ready}
              onClick={() => void save(true)}
            >
              Save and queue
            </button>
            <button
              className="btn w-full justify-center text-xs py-2 text-[var(--hs-text-muted)] hover:text-white"
              onClick={() => navigate(-1)}
            >
              Cancel
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}

export type { Media };

function MentionStateNote({ jobId }: { jobId: number }) {
  const mention = useMention(jobId);
  if (mention.isLoading) return null;
  if (!mention.data) return null;
  const view = mention.data;
  if (!view.requested) return null;

  return (
    <div className="mt-2 rounded-lg border border-[var(--hs-border)] bg-[var(--hs-canvas)] p-3 text-xs">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--hs-text-muted)]">
        Mention state
      </div>
      <p className="mt-1 font-medium text-white">{view.label}</p>
      {view.handles.length > 0 && (
        <p className="mt-1 text-xs text-[var(--hs-violet-bright)] font-mono">
          {view.handles.map((handle) => `@${handle}`).join(", ")}
        </p>
      )}
      <p className="mt-1 text-[11px] text-[var(--hs-text-muted)]">{view.reason}</p>
      {view.rewrite_warning && (
        <p className="mt-2 text-xs text-[var(--hs-warning)]">{view.rewrite_warning}</p>
      )}
      <p className="mt-2 text-[11px] text-[var(--hs-text-muted)]">
        Current chip: {MENTION_NAMES[view.state]}
        {view.attestation_only && " · operator attestation, never an API verification"}
      </p>
    </div>
  );
}
