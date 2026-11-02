import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { Media } from "../api/types";
import {
  IconClose,
  IconFolder,
  IconGrid,
  IconList,
  IconMoreVertical,
  IconPlay,
  IconPlus,
  IconRefreshCw,
  IconSearch,
  IconTrash,
} from "../components/Icons";
import {
  useAddMedia,
  useCheckMedia,
  useDeleteMedia,
  useMedia,
  useMediaJobs,
  useRecheckMedia,
  useUploadMedia,
} from "../hooks/api";
import { formatBytes, formatDuration, formatWhen } from "../lib/state";

/**
 * Media Library Page.
 *
 * Implements Panel 4 of the HexScheduler visual reference:
 * - Header with clip search, format/usage filter dropdown, and "+ Add Clip" CTA
 * - Grid and List view switcher (Grid default with 16:9 responsive thumbnail cards)
 * - Path-based local file registration workflow with SHA-256 pre-flight hash verification
 * - Seamless scheduling action linking into Create Content flow
 */
export function MediaLibraryPage() {
  const media = useMedia();
  const [path, setPath] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [filterType, setFilterType] = useState<"all" | "unused" | "scheduled">("all");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [showAddModal, setShowAddModal] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const check = useCheckMedia();
  const add = useAddMedia();
  const uploadMedia = useUploadMedia();

  const handleBrowserFile = async (file: File) => {
    setNote(null);
    setIsUploading(true);
    try {
      const saved = await uploadMedia.mutateAsync({ file });
      setPath(saved.path);
      setNote({ kind: "ok", text: `Successfully registered: ${saved.original_name || saved.path}` });
      setShowAddModal(false);
    } catch (exc) {
      setNote({ kind: "error", text: exc instanceof ApiError ? exc.message : "The video file could not be uploaded." });
    } finally {
      setIsUploading(false);
    }
  };

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
      setShowAddModal(false);
    } catch (exc) {
      setNote({ kind: "error", text: exc instanceof ApiError ? exc.message : "That file could not be added." });
    }
  };

  const rows = useMemo(() => media.data ?? [], [media.data]);

  // Filter clips by search query
  const filteredRows = useMemo(() => {
    return rows.filter((clip) => {
      const name = clip.original_name || clip.path;
      return name.toLowerCase().includes(searchQuery.toLowerCase());
    });
  }, [rows, searchQuery]);

  return (
    <div className="flex flex-col gap-6">
      {/* Page Header */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Media Library</h1>
          <p className="mt-1 text-sm text-[var(--hs-text-secondary)]">
            Manage your local clips. Add files and organize them for scheduling. Only the selected path is inspected.
          </p>
          <p className="mt-0.5 text-xs text-[var(--hs-text-muted)]">
            {rows.length} clip{rows.length === 1 ? "" : "s"} registered on this PC. A clip is re-hashed before every upload.
          </p>
        </div>
        <button
          className="btn btn-primary shadow-lg shadow-violet-950/50 py-2.5 px-4 rounded-xl font-medium"
          onClick={() => setShowAddModal(true)}
        >
          <IconPlus size={16} />
          Add Clip
        </button>
      </header>

      {/* Add Clip Card / Inline Panel */}
      {(showAddModal || rows.length === 0) && (
        <section className="hs-card p-5 border-[var(--hs-violet)]/40 bg-[var(--hs-surface-raised)]">
          <div className="flex items-center justify-between border-b border-[var(--hs-border)]/60 pb-3 mb-4">
            <div className="flex items-center gap-2">
              <IconFolder size={18} className="text-[var(--hs-violet-bright)]" />
              <h2 className="text-base font-semibold text-white tracking-tight">Register a Video File</h2>
            </div>
            {rows.length > 0 && (
              <button
                className="btn p-1 text-[var(--hs-text-muted)] hover:text-white"
                onClick={() => setShowAddModal(false)}
              >
                <IconClose size={16} />
              </button>
            )}
          </div>
          <p className="text-xs text-[var(--hs-text-secondary)]">
            HexScheduler inspects the exact path you specify. It never scans your folders automatically.
          </p>
          <div className="mt-3 flex flex-wrap gap-2.5">
            <label className="sr-only" htmlFor="media-path">
              Full path to the video file
            </label>
            <input
              id="media-path"
              className="input max-w-xl flex-1 text-xs"
              placeholder="C:\Videos\clip.mp4"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && path.trim()) void addMedia();
              }}
            />
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
            <button
              type="button"
              className="btn btn-primary text-xs flex items-center gap-1.5"
              disabled={isUploading}
              onClick={() => {
                if (fileInputRef.current) {
                  fileInputRef.current.value = "";
                  fileInputRef.current.click();
                }
              }}
            >
              {isUploading ? (
                <>
                  <IconRefreshCw size={13} className="animate-spin text-white" />
                  <span>Uploading...</span>
                </>
              ) : (
                <span>Browse…</span>
              )}
            </button>
            <button
              type="button"
              className="btn btn-secondary text-xs"
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
            <button
              type="button"
              className="btn text-xs font-semibold"
              disabled={add.isPending || !path.trim()}
              onClick={() => void addMedia()}
            >
              Add to library
            </button>
          </div>

          {note && (
            <p
              role="status"
              className={`mt-3 rounded-lg border px-3 py-2 text-xs font-medium ${
                note.kind === "ok"
                  ? "border-[var(--hs-success)]/40 bg-[var(--hs-success)]/10 text-[var(--hs-success)]"
                  : "border-[var(--hs-warning)]/50 bg-[var(--hs-warning)]/10 text-[var(--hs-warning)]"
              }`}
            >
              {note.text}
            </p>
          )}

          {check.data && !note && (
            <div className="mt-3 rounded-lg border border-[var(--hs-border)] bg-[var(--hs-canvas)] p-3 text-xs text-[var(--hs-text-secondary)] flex flex-wrap items-center justify-between gap-3">
              <div>
                <span className="text-[var(--hs-text-muted)] mr-1">SHA-256</span>
                <code className="font-mono text-white text-[11px]">{check.data.sha256?.slice(0, 24) ?? "—"}…</code>
              </div>
              <div>
                {formatBytes(check.data.size_bytes)} · {check.data.container ?? "unknown container"}
                {check.data.duration_seconds !== null && ` · ${formatDuration(check.data.duration_seconds)}`}
              </div>
              {check.data.issues.map((issue) => (
                <div key={issue.code} className={issue.blocking ? "text-[var(--hs-danger)] font-medium" : "text-[var(--hs-warning)]"}>
                  {issue.message}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Filter and View Mode Controls matching Panel 4 */}
      <div className="flex flex-wrap items-center justify-between gap-3 hs-card p-3.5">
        <div className="flex items-center gap-3 flex-1 max-w-md">
          <div className="relative w-full">
            <IconSearch size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--hs-text-muted)]" />
            <input
              type="search"
              className="input pl-8 py-1.5 text-xs rounded-lg"
              placeholder="Search clips…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <select
            className="input py-1.5 px-3 text-xs bg-[var(--hs-surface-raised)] border-[var(--hs-border)] rounded-lg text-[var(--hs-text)] w-auto"
            value={filterType}
            onChange={(e) => setFilterType(e.target.value as "all" | "unused" | "scheduled")}
          >
            <option value="all">All Files</option>
            <option value="unused">Unscheduled Clips</option>
            <option value="scheduled">Scheduled Clips</option>
          </select>

          {/* Grid / List View Toggles */}
          <div className="flex items-center rounded-lg border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] p-0.5">
            <button
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === "grid"
                  ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)]"
                  : "text-[var(--hs-text-muted)] hover:text-white"
              }`}
              onClick={() => setViewMode("grid")}
              aria-label="Grid view"
            >
              <IconGrid size={16} />
            </button>
            <button
              className={`p-1.5 rounded-md transition-colors ${
                viewMode === "list"
                  ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)]"
                  : "text-[var(--hs-text-muted)] hover:text-white"
              }`}
              onClick={() => setViewMode("list")}
              aria-label="List view"
            >
              <IconList size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* Main Content: Thumbnail Grid or List Table */}
      {media.isLoading ? (
        <div className="py-20 text-center">
          <p className="text-sm text-[var(--hs-text-muted)] animate-pulse">Loading registered clips…</p>
        </div>
      ) : filteredRows.length === 0 ? (
        <div className="hs-card py-16 px-6 text-center">
          <div className="grid h-12 w-12 place-items-center rounded-full bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] mx-auto mb-3">
            <IconFolder size={24} />
          </div>
          <h2 className="text-base font-semibold text-white">No clips found</h2>
          <p className="mt-1 text-xs text-[var(--hs-text-muted)] max-w-sm mx-auto">
            {searchQuery
              ? "No video files matched your search query."
              : "HexScheduler does not scan folders automatically. Add a video file path to register your first clip."}
          </p>
          <button
            className="btn btn-primary mt-4 text-xs font-semibold"
            onClick={() => setShowAddModal(true)}
          >
            <IconPlus size={14} /> Add Clip
          </button>
        </div>
      ) : viewMode === "grid" ? (
        /* Responsive 16:9 Thumbnail Grid */
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filteredRows.map((clip) => (
            <MediaGridCard key={clip.id} media={clip} />
          ))}
        </div>
      ) : (
        /* List View */
        <div className="hs-card overflow-hidden">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Registered video clips</caption>
            <thead>
              <tr className="border-b border-[var(--hs-border)] text-left text-xs uppercase tracking-wider text-[var(--hs-text-muted)] bg-[var(--hs-surface-raised)]">
                <th scope="col" className="px-5 py-3 font-semibold">Clip</th>
                <th scope="col" className="px-3 py-3 font-semibold">Size</th>
                <th scope="col" className="px-3 py-3 font-semibold">Container</th>
                <th scope="col" className="px-3 py-3 font-semibold">Last verified</th>
                <th scope="col" className="px-3 py-3 font-semibold">State</th>
                <th scope="col" className="px-5 py-3 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--hs-border)]/50">
              {filteredRows.map((clip) => (
                <MediaTableRow key={clip.id} media={clip} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/**
 * 16:9 Aspect Ratio Clip Card matching Panel 4 of the reference collage.
 */
function MediaGridCard({ media }: { media: Media }) {
  const recheck = useRecheckMedia();
  const remove = useDeleteMedia();
  const jobs = useMediaJobs(media.id);
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);

  const name = media.original_name || media.path.split(/[\\/]/).pop() || media.path;
  const durationText = media.duration_seconds !== null ? formatDuration(media.duration_seconds) : null;

  return (
    <div className="hs-card hs-card-hover overflow-hidden flex flex-col justify-between group">
      {/* 16:9 Thumbnail Container */}
      <div className="relative aspect-video w-full bg-gradient-to-br from-slate-900 to-slate-950 flex items-center justify-center border-b border-[var(--hs-border)]/60 overflow-hidden">
        {/* Subtle decorative futuristic grid overlay */}
        <div className="absolute inset-0 bg-[radial-gradient(#8b5cf6_1px,transparent_1px)] [background-size:16px_16px] opacity-20" />
        <div className="grid h-10 w-10 place-items-center rounded-full bg-[var(--hs-violet-soft)] border border-[var(--hs-violet)]/40 text-[var(--hs-violet-bright)] group-hover:scale-110 transition-transform">
          <IconPlay size={18} />
        </div>

        {/* Video format chip */}
        <span className="absolute top-2.5 left-2.5 rounded-md bg-black/70 border border-white/10 px-1.5 py-0.5 text-[10px] font-mono font-semibold uppercase text-white">
          {media.container || "MP4"}
        </span>

        {/* Duration badge */}
        {durationText && (
          <span className="absolute bottom-2.5 right-2.5 rounded-md bg-black/80 px-1.5 py-0.5 text-[10px] font-mono font-medium text-white">
            {durationText}
          </span>
        )}
      </div>

      {/* Card Info & Details */}
      <div className="p-3.5 flex flex-col justify-between flex-1">
        <div>
          <div className="flex items-start justify-between gap-2">
            <h3 className="font-semibold text-xs text-white truncate flex-1" title={name}>
              {name}
            </h3>
            <div className="relative">
              <button
                className="p-1 rounded text-[var(--hs-text-muted)] hover:text-white"
                onClick={() => setMenuOpen(!menuOpen)}
                aria-label="Clip options"
              >
                <IconMoreVertical size={14} />
              </button>

              {menuOpen && (
                <div className="absolute right-0 top-full mt-1 w-36 rounded-lg border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] py-1 shadow-xl z-20">
                  <Link
                    to={`/new?media=${media.id}`}
                    className="block px-3 py-1.5 text-xs text-white hover:bg-[var(--hs-surface-soft)]"
                    onClick={() => setMenuOpen(false)}
                  >
                    Create Schedule
                  </Link>
                  <button
                    className="w-full text-left px-3 py-1.5 text-xs text-[var(--hs-text-secondary)] hover:bg-[var(--hs-surface-soft)] hover:text-white"
                    onClick={() => {
                      setMenuOpen(false);
                      recheck.mutate(media.id);
                    }}
                  >
                    Re-check File
                  </button>
                  <button
                    className="w-full text-left px-3 py-1.5 text-xs text-[var(--hs-danger)] hover:bg-red-500/10"
                    onClick={() => {
                      setMenuOpen(false);
                      setDeleteConfirm(true);
                    }}
                  >
                    Remove Clip
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-[var(--hs-text-muted)]">
            <span>{formatBytes(media.size_bytes)}</span>
            {durationText && <span>• {durationText}</span>}
          </div>

          <p className="mt-1 text-[10px] font-mono text-[var(--hs-text-muted)] truncate" title={media.path}>
            {media.path}
          </p>
        </div>

        {/* Delete confirmation modal dialog if open */}
        {deleteConfirm && (
          <div className="mt-2 rounded-lg border border-[var(--hs-danger)]/40 bg-[var(--hs-danger)]/10 p-2 text-xs">
            <p className="font-medium text-[var(--hs-danger)]">
              {(jobs.data?.total ?? 0) > 0
                ? `Cannot remove: referenced by ${jobs.data?.total} scheduled job(s).`
                : "Remove this clip?"}
            </p>
            <div className="mt-2 flex gap-2">
              <button
                className="btn btn-danger py-1 px-2 text-[11px]"
                disabled={remove.isPending || (jobs.data?.total ?? 0) > 0}
                onClick={() => remove.mutate(media.id)}
              >
                Confirm
              </button>
              <button
                className="btn py-1 px-2 text-[11px]"
                onClick={() => setDeleteConfirm(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Bottom CTA Row */}
        <div className="mt-3.5 pt-2.5 border-t border-[var(--hs-border)]/60 flex items-center justify-between">
          <span className="text-[10px] text-[var(--hs-text-muted)]">
            Verified {formatWhen(media.verified_at ?? media.validated_at)}
          </span>
          <Link
            to={`/new?media=${media.id}`}
            className="text-xs font-semibold text-[var(--hs-violet-bright)] hover:underline flex items-center gap-1"
          >
            Schedule <IconPlus size={12} />
          </Link>
        </div>
      </div>
    </div>
  );
}

/**
 * List Table Row view alternative
 */
function MediaTableRow({ media }: { media: Media }) {
  const recheck = useRecheckMedia();
  const remove = useDeleteMedia();
  const name = media.original_name || media.path.split(/[\\/]/).pop() || media.path;

  return (
    <tr className="hover:bg-[var(--hs-surface-raised)]/50 transition-colors">
      <td className="px-5 py-3">
        <div className="font-medium text-white truncate max-w-xs">{name}</div>
        <div className="font-mono text-[11px] text-[var(--hs-text-muted)] truncate max-w-xs">{media.path}</div>
      </td>
      <td className="px-3 py-3 font-mono text-xs text-[var(--hs-text-secondary)]">
        {formatBytes(media.size_bytes)}
      </td>
      <td className="px-3 py-3 text-xs uppercase font-mono text-[var(--hs-text-secondary)]">
        {media.container ?? "—"}
      </td>
      <td className="px-3 py-3 text-xs text-[var(--hs-text-muted)]">
        {formatWhen(media.verified_at ?? media.validated_at)}
      </td>
      <td className="px-3 py-3">
        {media.is_valid ? (
          <span className="text-xs font-medium text-[var(--hs-success)]">Verified intact</span>
        ) : (
          <span className="text-xs font-medium text-[var(--hs-danger)]">Check needed</span>
        )}
      </td>
      <td className="px-5 py-3 text-right">
        <div className="flex items-center justify-end gap-2">
          <Link to={`/new?media=${media.id}`} className="btn py-1 px-2.5 text-xs text-[var(--hs-violet-bright)]">
            Schedule
          </Link>
          <button
            className="btn py-1 px-2 text-xs"
            onClick={() => recheck.mutate(media.id)}
            disabled={recheck.isPending}
            title="Recheck"
          >
            <IconRefreshCw size={13} />
          </button>
          <button
            className="btn btn-danger py-1 px-2 text-xs"
            onClick={() => remove.mutate(media.id)}
            disabled={remove.isPending}
            title="Delete"
          >
            <IconTrash size={13} />
          </button>
        </div>
      </td>
    </tr>
  );
}
