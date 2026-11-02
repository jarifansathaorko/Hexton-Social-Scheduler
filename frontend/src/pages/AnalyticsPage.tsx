import { useMemo, useState } from "react";
import type { AnalyticsVideoItem } from "../api/types";
import {
  IconArrowUpDown,
  IconAward,
  IconChannel,
  IconCheck,
  IconCheckCircle,
  IconClock,
  IconClose,
  IconDownload,
  IconExternalLink,
  IconGlobe,
  IconRefreshCw,
  IconSearch,
  IconTrendingUp,
} from "../components/Icons";
import {
  useAnalyticsOverview,
  useAnalyticsVideos,
  useConnections,
  useRefreshAnalytics,
  useUpdateVideoApproval,
} from "../hooks/api";

function formatPublishedDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatPublishedTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * 1K+ Views & Deliverables Page (Phase 1 & 2).
 *
 * Provides multi-channel performance monitoring across all 13+ brand channels,
 * tracking clips that reach the 1,000+ views threshold for client deliverables,
 * with live batch stat refreshes (50 IDs per quota unit), channel dropdown filtering,
 * proof-of-work CSV exports, and creator approval note-taking (✓ / ✗).
 */
export type SortOption = "most_views" | "least_views" | "last_uploaded" | "first_uploaded";

export function AnalyticsPage() {
  const [selectedChannel, setSelectedChannel] = useState<string>("");
  const [activeTab, setActiveTab] = useState<"all" | "qualified" | "approaching">("all");
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortOption>("most_views");
  const [refreshNotice, setRefreshNotice] = useState<string | null>(null);
  const [selectedDemographicVideo, setSelectedDemographicVideo] = useState<AnalyticsVideoItem | null>(null);

  const overview = useAnalyticsOverview(selectedChannel || undefined);
  const refreshMutation = useRefreshAnalytics();
  const updateApprovalMutation = useUpdateVideoApproval();
  const connectionsQuery = useConnections();

  // Local approval notes state for instant zero-lag responsiveness backed by localStorage and SQLite
  const [localApprovals, setLocalApprovals] = useState<
    Record<number, "approved" | "rejected" | null>
  >(() => {
    try {
      const saved = localStorage.getItem("hexton_creator_approval_notes");
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  });

  const toggleApproval = (video: AnalyticsVideoItem, target: "approved" | "rejected") => {
    const current =
      localApprovals[video.id] !== undefined
        ? localApprovals[video.id]
        : (video.creator_approval ?? null);
    const nextValue = current === target ? null : target;

    // 1. Update React state immediately
    setLocalApprovals((prev) => {
      const updated = { ...prev, [video.id]: nextValue };
      try {
        localStorage.setItem("hexton_creator_approval_notes", JSON.stringify(updated));
      } catch {
        // quota safety
      }
      return updated;
    });

    // 2. Persist to backend database (non-blocking note taking)
    updateApprovalMutation.mutate({ jobId: video.id, approval: nextValue });
  };

  // Combine channels from OAuth connections and analytics summaries
  const availableChannels = useMemo(() => {
    const map = new Map<
      string,
      { channel_id: string; title: string; handle: string | null; count?: number }
    >();

    const conns = connectionsQuery.data ?? [];
    for (const conn of conns) {
      for (const ch of conn.channels ?? []) {
        if (ch.channel_id) {
          map.set(ch.channel_id, {
            channel_id: ch.channel_id,
            title: ch.title,
            handle: ch.handle,
          });
        }
      }
    }

    const summaries = overview.data?.channel_summaries ?? [];
    for (const s of summaries) {
      if (s.channel_id) {
        const existing = map.get(s.channel_id);
        map.set(s.channel_id, {
          channel_id: s.channel_id,
          title: s.channel_title || existing?.title || s.channel_id,
          handle: s.handle || existing?.handle || null,
          count: s.video_count,
        });
      }
    }

    return Array.from(map.values());
  }, [connectionsQuery.data, overview.data?.channel_summaries]);

  // Filters for video table
  const videoFilters = useMemo(() => {
    return {
      channel_id: selectedChannel || undefined,
      qualified_only: activeTab === "qualified",
      search: search.trim() || undefined,
      sort_by: sortBy,
      limit: 100,
    };
  }, [selectedChannel, activeTab, search, sortBy]);

  const videos = useAnalyticsVideos(videoFilters);

  // Filter approaching items if tab is approaching & sort
  const displayItems = useMemo(() => {
    const raw = videos.data?.items ?? [];
    let items = raw;
    if (activeTab === "approaching") {
      items = raw.filter((v) => v.view_count >= 500 && v.view_count < 1000);
    }
    const sorted = [...items];
    switch (sortBy) {
      case "most_views":
        sorted.sort((a, b) => (b.view_count !== a.view_count ? b.view_count - a.view_count : b.id - a.id));
        break;
      case "least_views":
        sorted.sort((a, b) => (a.view_count !== b.view_count ? a.view_count - b.view_count : a.id - b.id));
        break;
      case "last_uploaded":
        sorted.sort((a, b) => {
          const timeA = new Date(a.published_at || a.publish_at_utc || a.uploaded_at || 0).getTime();
          const timeB = new Date(b.published_at || b.publish_at_utc || b.uploaded_at || 0).getTime();
          return timeB !== timeA ? timeB - timeA : b.id - a.id;
        });
        break;
      case "first_uploaded":
        sorted.sort((a, b) => {
          const timeA = new Date(a.published_at || a.publish_at_utc || a.uploaded_at || 0).getTime();
          const timeB = new Date(b.published_at || b.publish_at_utc || b.uploaded_at || 0).getTime();
          return timeA !== timeB ? timeA - timeB : a.id - b.id;
        });
        break;
    }
    return sorted;
  }, [videos.data?.items, activeTab, sortBy]);

  const handleRefresh = async () => {
    try {
      setRefreshNotice(null);
      const res = await refreshMutation.mutateAsync(selectedChannel || undefined);
      setRefreshNotice(
        `Synced ${res.synced_count} clips (${res.qualified_count} qualified at 1K+). Total views: ${res.total_views.toLocaleString()}`,
      );
      setTimeout(() => setRefreshNotice(null), 8000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setRefreshNotice(`Sync notice: ${msg}`);
    }
  };

  const handleExportCsv = (qualifiedOnly: boolean) => {
    const params = new URLSearchParams();
    if (qualifiedOnly) params.set("min_views", "1000");
    if (selectedChannel) params.set("channel_id", selectedChannel);
    if (sortBy) params.set("sort_by", sortBy);
    const url = `/api/analytics/export/csv?${params.toString()}`;
    window.open(url, "_blank");
  };

  const ov = overview.data;
  const channelSummaries = ov?.channel_summaries ?? [];

  return (
    <div className="flex flex-col gap-6">
      {/* Page Header */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight text-white">
              1K+ Deliverables
            </h1>
            <span className="rounded-full bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-0.5 text-xs font-semibold text-emerald-400 flex items-center gap-1">
              <IconAward size={13} />
              Deliverables Hub
            </span>
          </div>
          <p className="mt-1 text-sm text-[var(--hs-text-secondary)]">
            Multi-channel performance tracking across all 13+ brand channels. Verify clips reaching
            1,000+ views for client sign-off.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshMutation.isPending}
            className="btn btn-secondary py-2.5 px-3.5 rounded-xl font-medium text-xs sm:text-sm flex items-center gap-2"
            title="Batch queries live stats from YouTube (50 videos per quota unit)"
          >
            <IconRefreshCw
              size={15}
              className={refreshMutation.isPending ? "animate-spin text-[var(--hs-violet-bright)]" : ""}
            />
            <span>{refreshMutation.isPending ? "Updating Views..." : "Refresh Live Views"}</span>
          </button>

          <button
            type="button"
            onClick={() => handleExportCsv(true)}
            className="btn btn-primary shadow-lg shadow-violet-950/50 py-2.5 px-4 rounded-xl font-medium text-xs sm:text-sm flex items-center gap-2"
            title="Download client deliverables sheet formatted with verified watch links"
          >
            <IconDownload size={15} />
            <span>Export Deliverables (1K+ CSV)</span>
          </button>
        </div>
      </header>

      {/* Sync Status Banner */}
      {refreshNotice && (
        <div className="flex items-center gap-2 rounded-xl border border-[var(--hs-border-strong)] bg-[var(--hs-violet-soft)] px-4 py-3 text-xs sm:text-sm text-[var(--hs-text)] animate-in fade-in">
          <IconCheckCircle size={16} className="text-emerald-400 shrink-0" />
          <span>{refreshNotice}</span>
        </div>
      )}

      {/* Top Telemetry / Metric Cards */}
      <div className="grid gap-3.5 grid-cols-2 lg:grid-cols-4">
        {/* Card 1: Qualified Deliverables (1K+) */}
        <div className="hs-card p-4 relative overflow-hidden border-emerald-500/30 bg-gradient-to-br from-[var(--hs-surface)] to-emerald-950/20">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-emerald-400/90 flex items-center gap-1.5">
              <IconAward size={13} />
              Qualified (1K+ Views)
            </span>
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
              <IconCheckCircle size={15} />
            </div>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-white">
              {ov?.qualified_videos ?? 0}
            </span>
            <span className="text-xs font-semibold text-emerald-400">
              {ov?.qualification_rate ?? 0}% rate
            </span>
          </div>
          <p className="mt-1 text-[11px] text-[var(--hs-text-muted)]">
            Ready for client proof-of-work
          </p>
        </div>

        {/* Card 2: Total Views Tracked */}
        <div className="hs-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--hs-text-muted)]">
              Total Reach Tracked
            </span>
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--hs-violet-soft)] border border-[var(--hs-border-strong)] text-[var(--hs-violet-bright)]">
              <IconTrendingUp size={15} />
            </div>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-white">
              {(ov?.total_views ?? 0).toLocaleString()}
            </span>
            <span className="text-xs text-[var(--hs-text-muted)]">views</span>
          </div>
          <p className="mt-1 text-[11px] text-[var(--hs-text-muted)]">
            Across {ov?.total_videos ?? 0} published clips
          </p>
        </div>

        {/* Card 3: Approaching 1K Milestone */}
        <div className="hs-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--hs-text-muted)]">
              Approaching 1K (500-999)
            </span>
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400">
              <IconClock size={15} />
            </div>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-white">
              {ov?.approaching_videos ?? 0}
            </span>
            <span className="text-xs text-amber-400">trending</span>
          </div>
          <p className="mt-1 text-[11px] text-[var(--hs-text-muted)]">
            Likely next deliverables
          </p>
        </div>

        {/* Card 4: Brand Channels Tracked */}
        <div className="hs-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--hs-text-muted)]">
              Active Brand Channels
            </span>
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-blue-500/10 border border-blue-500/30 text-blue-400">
              <IconChannel size={15} />
            </div>
          </div>
          <div className="mt-3 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-white">
              {availableChannels.length > 0 ? availableChannels.length : channelSummaries.length}
            </span>
            <span className="text-xs text-[var(--hs-text-muted)]">channels</span>
          </div>
          <p className="mt-1 text-[11px] text-[var(--hs-text-muted)]">
            13+ channel portfolio monitoring
          </p>
        </div>
      </div>

      {/* Multi-Channel Quick Selector Grid (13+ Channels) */}
      {availableChannels.length > 0 && (
        <section className="hs-card p-4">
          <div className="flex items-center justify-between pb-3 border-b border-[var(--hs-border)]">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--hs-text-muted)]">
              Channel Breakdown (Quick Switch)
            </h2>
            {selectedChannel && (
              <button
                type="button"
                onClick={() => setSelectedChannel("")}
                className="text-xs text-[var(--hs-violet-bright)] hover:underline"
              >
                Clear Channel Filter (Show All)
              </button>
            )}
          </div>
          <div className="mt-3 flex gap-2.5 overflow-x-auto pb-1 scrollbar-thin">
            <button
              type="button"
              onClick={() => setSelectedChannel("")}
              className={`shrink-0 rounded-xl px-3.5 py-2.5 text-left transition-all border ${
                selectedChannel === ""
                  ? "bg-[var(--hs-violet-soft)] border-[var(--hs-border-strong)] text-white shadow-sm"
                  : "bg-[var(--hs-surface-raised)] border-[var(--hs-border)] text-[var(--hs-text-secondary)] hover:text-white"
              }`}
            >
              <div className="text-xs font-semibold">All Channels</div>
              <div className="text-[11px] text-[var(--hs-text-muted)] mt-0.5">
                {ov?.qualified_videos ?? 0} Qualified / {ov?.total_videos ?? 0} Clips
              </div>
            </button>

            {availableChannels.map((ch) => {
              const isSelected = selectedChannel === ch.channel_id;
              const summary = channelSummaries.find((s) => s.channel_id === ch.channel_id);
              return (
                <button
                  key={ch.channel_id}
                  type="button"
                  onClick={() => setSelectedChannel(ch.channel_id)}
                  className={`shrink-0 rounded-xl px-3.5 py-2.5 text-left transition-all border ${
                    isSelected
                      ? "bg-[var(--hs-violet-soft)] border-[var(--hs-border-strong)] text-white shadow-sm"
                      : "bg-[var(--hs-surface-raised)] border-[var(--hs-border)] text-[var(--hs-text-secondary)] hover:text-white"
                  }`}
                >
                  <div className="text-xs font-semibold truncate max-w-[180px]">
                    {ch.title}
                  </div>
                  <div className="text-[11px] text-[var(--hs-text-muted)] mt-0.5 flex items-center gap-2">
                    {summary ? (
                      <>
                        <span className="text-emerald-400 font-medium">
                          {summary.qualified_count} / {summary.video_count} qual.
                        </span>
                        <span>•</span>
                        <span>{summary.total_views.toLocaleString()} views</span>
                      </>
                    ) : (
                      <span>{ch.handle ?? "Active Channel"}</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {/* Main Filter & Navigation Tabs Bar */}
      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* Tabs */}
          <div className="flex flex-wrap items-center gap-1.5 rounded-xl bg-[var(--hs-surface-raised)] p-1 border border-[var(--hs-border)]">
            <button
              type="button"
              onClick={() => setActiveTab("all")}
              className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-all flex items-center gap-1.5 ${
                activeTab === "all"
                  ? "bg-[var(--hs-violet-soft)] border border-[var(--hs-border-strong)] text-[var(--hs-violet-bright)] shadow-sm"
                  : "text-[var(--hs-text-secondary)] hover:text-white"
              }`}
            >
              <span>All Published Clips</span>
              <span className="ml-1 rounded-full bg-[var(--hs-border)] px-1.5 py-0.2 text-[10px]">
                {ov?.total_videos ?? 0}
              </span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab("qualified")}
              className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-all flex items-center gap-1.5 ${
                activeTab === "qualified"
                  ? "bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 shadow-sm"
                  : "text-[var(--hs-text-secondary)] hover:text-white"
              }`}
            >
              <IconAward size={13} />
              <span>Qualified Deliverables (1K+)</span>
              <span className="ml-1 rounded-full bg-emerald-500/20 px-1.5 py-0.2 text-[10px]">
                {ov?.qualified_videos ?? 0}
              </span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab("approaching")}
              className={`rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-all flex items-center gap-1.5 ${
                activeTab === "approaching"
                  ? "bg-amber-500/15 border border-amber-500/30 text-amber-300 shadow-sm"
                  : "text-[var(--hs-text-secondary)] hover:text-white"
              }`}
            >
              <IconClock size={13} />
              <span>Approaching 1K (500-999)</span>
              <span className="ml-1 rounded-full bg-amber-500/20 px-1.5 py-0.2 text-[10px]">
                {ov?.approaching_videos ?? 0}
              </span>
            </button>
          </div>

          {/* Right Toolbar: Sort Viewing, Channel Dropdown & Search */}
          <div className="flex flex-wrap items-center gap-2.5">
            {/* Sort Dropdown */}
            <div className="relative flex items-center">
              <label htmlFor="sort-by-select" className="sr-only">
                Sort Viewing
              </label>
              <div className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none text-[var(--hs-violet-bright)]">
                <IconArrowUpDown size={14} />
              </div>
              <select
                id="sort-by-select"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortOption)}
                className="appearance-none rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] pl-9 pr-8 py-1.5 text-xs text-white focus:border-[var(--hs-violet)] focus:outline-none cursor-pointer hover:border-[var(--hs-border-strong)] transition-all min-w-[155px]"
                title="Sort viewing clips"
              >
                <option value="most_views">Most Views</option>
                <option value="last_uploaded">Last Uploaded</option>
                <option value="first_uploaded">First Uploaded</option>
                <option value="least_views">Least Views</option>
              </select>
              <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-[var(--hs-text-muted)] text-[10px]">
                ▼
              </div>
            </div>

            {/* Channel Dropdown Filter */}
            <div className="relative flex items-center">
              <label htmlFor="channel-filter-select" className="sr-only">
                Filter by Channel
              </label>
              <div className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none text-rose-400">
                <IconChannel size={14} />
              </div>
              <select
                id="channel-filter-select"
                value={selectedChannel}
                onChange={(e) => setSelectedChannel(e.target.value)}
                className="appearance-none rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] pl-9 pr-8 py-1.5 text-xs text-white focus:border-[var(--hs-violet)] focus:outline-none cursor-pointer hover:border-[var(--hs-border-strong)] transition-all min-w-[200px]"
              >
                <option value="">All Channels ({availableChannels.length > 0 ? `${availableChannels.length} Connected` : "All"})</option>
                {availableChannels.map((c) => (
                  <option key={c.channel_id} value={c.channel_id}>
                    {c.title} {c.handle ? `(${c.handle})` : ""}
                  </option>
                ))}
              </select>
              <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-[var(--hs-text-muted)] text-[10px]">
                ▼
              </div>
            </div>

            {/* Search Input */}
            <div className="relative min-w-[200px]">
              <IconSearch
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--hs-text-muted)]"
              />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter by title..."
                className="w-full rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] pl-9 pr-3 py-1.5 text-xs text-white placeholder:text-[var(--hs-text-muted)] focus:border-[var(--hs-violet)] focus:outline-none"
              />
            </div>

            {(selectedChannel || search || sortBy !== "most_views") && (
              <button
                type="button"
                onClick={() => {
                  setSelectedChannel("");
                  setSearch("");
                  setSortBy("most_views");
                }}
                className="rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] px-2.5 py-1.5 text-xs text-[var(--hs-text-muted)] hover:text-white hover:border-[var(--hs-border-strong)] transition-colors"
                title="Reset channel, search, and sorting filters"
              >
                Reset
              </button>
            )}
          </div>
        </div>

        {/* Deliverables Video Table */}
        <div className="hs-card overflow-hidden">
          {videos.isLoading ? (
            <div className="p-8 text-center text-sm text-[var(--hs-text-muted)]">
              Loading video metrics...
            </div>
          ) : displayItems.length === 0 ? (
            <div className="p-12 text-center flex flex-col items-center justify-center gap-3">
              <div className="grid h-12 w-12 place-items-center rounded-2xl bg-[var(--hs-surface-raised)] border border-[var(--hs-border)] text-[var(--hs-text-muted)]">
                <IconAward size={22} />
              </div>
              <div>
                <p className="text-sm font-semibold text-white">No videos in this view</p>
                <p className="text-xs text-[var(--hs-text-muted)] mt-1 max-w-sm">
                  {activeTab === "qualified"
                    ? "No clips have reached the 1,000 views threshold yet. Hit 'Refresh Live Views' to query current YouTube statistics."
                    : selectedChannel
                    ? "No published clips found for the selected channel."
                    : "No published clips match the active filter criteria."}
                </p>
              </div>
              <div className="flex items-center gap-2 mt-2">
                {selectedChannel && (
                  <button
                    type="button"
                    onClick={() => setSelectedChannel("")}
                    className="btn btn-secondary py-2 px-3.5 rounded-xl text-xs"
                  >
                    View All Channels
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleRefresh}
                  className="btn btn-secondary py-2 px-3.5 rounded-xl text-xs"
                >
                  Refresh Live Views Now
                </button>
              </div>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-[var(--hs-border)] bg-[var(--hs-surface-raised)]/50 text-[var(--hs-text-muted)] font-semibold uppercase tracking-wider text-[11px]">
                    <th className="py-3 px-4">Clip / Title</th>
                    <th className="py-3 px-4">Channel</th>
                    <th className="py-3 px-4 min-w-[170px]">
                      <button
                        type="button"
                        onClick={() =>
                          setSortBy((prev) => (prev === "most_views" ? "least_views" : "most_views"))
                        }
                        className="group inline-flex items-center gap-1.5 hover:text-white transition-colors cursor-pointer select-none text-left"
                        title="Sort by view count"
                      >
                        <span>Live Views & Progress</span>
                        {sortBy === "most_views" && (
                          <span className="text-[var(--hs-violet-bright)] font-bold text-[10px]">▼</span>
                        )}
                        {sortBy === "least_views" && (
                          <span className="text-[var(--hs-violet-bright)] font-bold text-[10px]">▲</span>
                        )}
                        {sortBy !== "most_views" && sortBy !== "least_views" && (
                          <span className="opacity-0 group-hover:opacity-40 transition-opacity text-[10px]">↕</span>
                        )}
                      </button>
                    </th>
                    <th className="py-3 px-4 min-w-[210px]">Audience Demographics (% Country)</th>
                    <th className="py-3 px-4">Engagement</th>
                    <th className="py-3 px-4">
                      <button
                        type="button"
                        onClick={() =>
                          setSortBy((prev) =>
                            prev === "last_uploaded" ? "first_uploaded" : "last_uploaded",
                          )
                        }
                        className="group inline-flex items-center gap-1.5 hover:text-white transition-colors cursor-pointer select-none text-left"
                        title="Sort by upload / published date"
                      >
                        <span>Published Date</span>
                        {sortBy === "last_uploaded" && (
                          <span className="text-[var(--hs-violet-bright)] font-bold text-[10px]">▼</span>
                        )}
                        {sortBy === "first_uploaded" && (
                          <span className="text-[var(--hs-violet-bright)] font-bold text-[10px]">▲</span>
                        )}
                        {sortBy !== "last_uploaded" && sortBy !== "first_uploaded" && (
                          <span className="opacity-0 group-hover:opacity-40 transition-opacity text-[10px]">↕</span>
                        )}
                      </button>
                    </th>
                    <th className="py-3 px-4 text-center">Status</th>
                    <th className="py-3 px-4 text-center min-w-[120px]">Creator Approval</th>
                    <th className="py-3 px-4 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--hs-border)]/60 text-[var(--hs-text)]">
                  {displayItems.map((v) => {
                    const isQual = v.is_qualified;
                    const percent = Math.min(100, Math.round((v.view_count / 1000) * 100));
                    const remaining = Math.max(0, 1000 - v.view_count);
                    const currentApproval =
                      localApprovals[v.id] !== undefined
                        ? localApprovals[v.id]
                        : (v.creator_approval ?? null);

                    return (
                      <tr
                        key={v.id}
                        className="hover:bg-[var(--hs-surface-soft)]/60 transition-colors"
                      >
                        {/* Title & Schedule Info */}
                        <td className="py-3.5 px-4 font-medium max-w-xs">
                          <div className="truncate text-white font-semibold" title={v.title}>
                            {v.title}
                          </div>
                          <div className="text-[11px] text-[var(--hs-text-muted)] font-mono mt-0.5">
                            ID: {v.schedule_id}
                          </div>
                        </td>

                        {/* Channel Badge */}
                        <td className="py-3.5 px-4">
                          <span className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--hs-surface-raised)] border border-[var(--hs-border)] px-2.5 py-1 text-[11px] font-medium text-[var(--hs-text-secondary)]">
                            <IconChannel size={12} className="text-rose-400" />
                            <span className="truncate max-w-[140px]">{v.channel_title}</span>
                          </span>
                        </td>

                        {/* Views & Progress to 1K */}
                        <td className="py-3.5 px-4">
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-bold text-white text-sm">
                              {v.view_count.toLocaleString()}
                            </span>
                            <span
                              className={`text-[11px] font-semibold ${
                                isQual
                                  ? "text-emerald-400"
                                  : v.view_count >= 500
                                  ? "text-amber-400"
                                  : "text-[var(--hs-text-muted)]"
                              }`}
                            >
                              {isQual ? "100% Met" : `${percent}% (${remaining} to 1K)`}
                            </span>
                          </div>

                          {/* Visual Progress Bar */}
                          <div className="mt-1.5 h-2 w-full rounded-full bg-[var(--hs-surface-soft)] overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all duration-500 ${
                                isQual
                                  ? "bg-gradient-to-r from-emerald-500 to-teal-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]"
                                  : v.view_count >= 500
                                  ? "bg-gradient-to-r from-amber-500 to-yellow-400"
                                  : "bg-gradient-to-r from-violet-600 to-[var(--hs-violet-bright)]"
                              }`}
                              style={{ width: `${percent}%` }}
                            />
                          </div>
                        </td>

                        {/* Audience Demographics */}
                        <td className="py-3.5 px-4 min-w-[210px]">
                          {v.demographics && v.demographics.length > 0 ? (
                            <div className="flex flex-col gap-1.5">
                              {/* Top Country Chips */}
                              <div className="flex items-center gap-1.5 flex-wrap">
                                {v.demographics.slice(0, 3).map((d) => (
                                  <span
                                    key={d.country_code}
                                    className="inline-flex items-center gap-1 rounded-md bg-[var(--hs-surface-raised)] border border-[var(--hs-border)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--hs-text-secondary)] shadow-sm"
                                    title={`${d.country_name}: ${d.percentage}% (${d.views.toLocaleString()} views)`}
                                  >
                                    <span>{d.flag}</span>
                                    <span className="font-bold text-white">{d.country_code}</span>
                                    <span className="text-cyan-400 font-semibold">{d.percentage}%</span>
                                  </span>
                                ))}
                              </div>

                              {/* Multi-Country Colored Distribution Bar */}
                              <div className="h-1.5 w-full rounded-full bg-[var(--hs-surface-soft)] overflow-hidden flex">
                                {v.demographics.map((d, idx) => {
                                  const barColors = [
                                    "bg-cyan-400",
                                    "bg-indigo-400",
                                    "bg-emerald-400",
                                    "bg-amber-400",
                                    "bg-rose-400",
                                    "bg-purple-400",
                                  ];
                                  return (
                                    <div
                                      key={d.country_code}
                                      className={`${barColors[idx % barColors.length]} h-full transition-all`}
                                      style={{ width: `${d.percentage}%` }}
                                      title={`${d.country_name} (${d.country_code}): ${d.percentage}%`}
                                    />
                                  );
                                })}
                              </div>

                              {/* View Full Breakdown Button */}
                              <button
                                type="button"
                                onClick={() => setSelectedDemographicVideo(v)}
                                className="text-[10px] text-cyan-400 hover:text-cyan-300 text-left font-medium hover:underline flex items-center gap-1 w-fit mt-0.5"
                              >
                                <IconGlobe size={11} />
                                <span>View all {v.demographics.length} countries breakdown →</span>
                              </button>
                            </div>
                          ) : (
                            <span className="text-[11px] text-[var(--hs-text-muted)]">Awaiting sync</span>
                          )}
                        </td>

                        {/* Engagement */}
                        <td className="py-3.5 px-4 text-[11px] text-[var(--hs-text-secondary)]">
                          <div>👍 {v.like_count.toLocaleString()} likes</div>
                          <div className="text-[var(--hs-text-muted)] mt-0.5">
                            💬 {v.comment_count.toLocaleString()} comments
                          </div>
                        </td>

                        {/* Published Date */}
                        <td className="py-3.5 px-4 text-[11px] text-[var(--hs-text-secondary)] whitespace-nowrap">
                          <div className="font-semibold text-white">
                            {formatPublishedDate(v.published_at || v.publish_at_utc)}
                          </div>
                          <div className="text-[10px] text-[var(--hs-text-muted)] mt-0.5">
                            {formatPublishedTime(v.published_at || v.publish_at_utc)}
                          </div>
                        </td>

                        {/* Status Badge */}
                        <td className="py-3.5 px-4 text-center">
                          {isQual ? (
                            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 border border-emerald-500/30 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-300">
                              <IconCheckCircle size={12} />
                              Qualified (1K+)
                            </span>
                          ) : v.view_count >= 500 ? (
                            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 border border-amber-500/30 px-2.5 py-0.5 text-[11px] font-semibold text-amber-300">
                              <IconClock size={12} />
                              Approaching 1K
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 rounded-full bg-violet-500/10 border border-violet-500/20 px-2.5 py-0.5 text-[11px] font-medium text-violet-300">
                              In Progress
                            </span>
                          )}
                        </td>

                        {/* Creator Approval Sign-off (Note taking: ✓ and ✗) */}
                        <td className="py-3.5 px-4 text-center">
                          <div className="inline-flex items-center gap-1.5 rounded-xl bg-[var(--hs-surface-raised)]/70 p-1 border border-[var(--hs-border)]">
                            <button
                              type="button"
                              onClick={() => toggleApproval(v, "approved")}
                              title={
                                currentApproval === "approved"
                                  ? "Approved by Creator (Click to remove note)"
                                  : "Mark note: Approved by Creator"
                              }
                              className={`grid h-7 w-7 place-items-center rounded-lg border transition-all ${
                                currentApproval === "approved"
                                  ? "bg-emerald-500/25 border-emerald-500/60 text-emerald-300 shadow-[0_0_8px_rgba(52,211,153,0.3)] ring-1 ring-emerald-500/30 font-bold"
                                  : "bg-transparent border-transparent text-[var(--hs-text-muted)] hover:text-emerald-400 hover:bg-emerald-500/10"
                              }`}
                            >
                              <IconCheck size={14} />
                            </button>

                            <button
                              type="button"
                              onClick={() => toggleApproval(v, "rejected")}
                              title={
                                currentApproval === "rejected"
                                  ? "Rejected by Creator (Click to remove note)"
                                  : "Mark note: Rejected by Creator"
                              }
                              className={`grid h-7 w-7 place-items-center rounded-lg border transition-all ${
                                currentApproval === "rejected"
                                  ? "bg-rose-500/25 border-rose-500/60 text-rose-300 shadow-[0_0_8px_rgba(244,63,94,0.3)] ring-1 ring-rose-500/30 font-bold"
                                  : "bg-transparent border-transparent text-[var(--hs-text-muted)] hover:text-rose-400 hover:bg-rose-500/10"
                              }`}
                            >
                              <IconClose size={14} />
                            </button>
                          </div>
                          {currentApproval && (
                            <div
                              className={`text-[10px] mt-0.5 font-medium ${
                                currentApproval === "approved" ? "text-emerald-400" : "text-rose-400"
                              }`}
                            >
                              {currentApproval === "approved" ? "Approved" : "Rejected"}
                            </div>
                          )}
                        </td>

                        {/* YouTube Watch Link */}
                        <td className="py-3.5 px-4 text-right">
                          <a
                            href={v.watch_url}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] px-2.5 py-1 text-[11px] font-medium text-[var(--hs-text-secondary)] hover:text-white hover:border-[var(--hs-border-strong)] transition-all"
                            title="Open clip on YouTube"
                          >
                            <span>Watch</span>
                            <IconExternalLink size={12} />
                          </a>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* Demographic Details Modal */}
      {selectedDemographicVideo && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-fade-in"
          onClick={() => setSelectedDemographicVideo(null)}
        >
          <div
            className="bg-[var(--hs-surface)] border border-[var(--hs-border-strong)] rounded-2xl max-w-lg w-full p-6 shadow-2xl relative max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-start justify-between pb-4 border-b border-[var(--hs-border)]">
              <div className="flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-xl bg-cyan-500/15 border border-cyan-500/30 text-cyan-400 shadow-inner">
                  <IconGlobe size={20} />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white">Audience Demographics</h3>
                  <p className="text-xs text-[var(--hs-text-muted)] mt-0.5">
                    Geographic distribution and viewer percentage by country
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setSelectedDemographicVideo(null)}
                className="grid h-8 w-8 place-items-center rounded-lg border border-[var(--hs-border)] text-[var(--hs-text-muted)] hover:text-white hover:bg-[var(--hs-surface-raised)] transition-colors"
                title="Close"
              >
                <IconClose size={16} />
              </button>
            </div>

            {/* Video Context Summary Card */}
            <div className="mt-4 p-3.5 rounded-xl bg-[var(--hs-surface-raised)] border border-[var(--hs-border)] flex flex-col gap-2">
              <div className="font-semibold text-white text-xs line-clamp-2">
                {selectedDemographicVideo.title}
              </div>
              <div className="flex items-center justify-between text-[11px] text-[var(--hs-text-secondary)]">
                <span className="inline-flex items-center gap-1.5">
                  <IconChannel size={12} className="text-rose-400" />
                  {selectedDemographicVideo.channel_title}
                </span>
                <span className="font-bold text-white text-xs">
                  {selectedDemographicVideo.view_count.toLocaleString()} total views
                </span>
              </div>
              <div className="flex items-center justify-between text-[11px] text-[var(--hs-text-muted)] pt-1.5 border-t border-[var(--hs-border)]/50">
                <span>Published on YouTube:</span>
                <span className="font-semibold text-white">
                  {formatPublishedDate(selectedDemographicVideo.published_at || selectedDemographicVideo.publish_at_utc)}{" "}
                  <span className="text-[var(--hs-text-secondary)] font-normal">
                    ({formatPublishedTime(selectedDemographicVideo.published_at || selectedDemographicVideo.publish_at_utc)})
                  </span>
                </span>
              </div>
            </div>

            {/* Countries Breakdown List */}
            <div className="mt-5 space-y-3">
              <div className="text-xs font-semibold uppercase tracking-wider text-[var(--hs-text-muted)] flex items-center justify-between">
                <span>Country & Region</span>
                <span>Share / Views</span>
              </div>

              {(selectedDemographicVideo.demographics ?? []).map((d) => (
                <div
                  key={d.country_code}
                  className="p-3 rounded-xl bg-[var(--hs-surface-soft)]/70 border border-[var(--hs-border)]/60 flex flex-col gap-2 hover:border-[var(--hs-border)] transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-lg">{d.flag}</span>
                      <span className="font-semibold text-white text-xs">{d.country_name}</span>
                      <span className="text-[10px] text-[var(--hs-text-muted)] font-mono uppercase bg-[var(--hs-surface-raised)] px-1.5 py-0.5 rounded border border-[var(--hs-border)]">
                        {d.country_code}
                      </span>
                    </div>
                    <div className="text-right">
                      <span className="text-xs font-bold text-cyan-400">
                        {d.percentage}%
                      </span>
                      <span className="text-[11px] text-[var(--hs-text-muted)] ml-2">
                        ({d.views.toLocaleString()} views)
                      </span>
                    </div>
                  </div>

                  {/* Gradient Progress Bar */}
                  <div className="h-2 w-full rounded-full bg-[var(--hs-surface-raised)] overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-indigo-500 shadow-[0_0_8px_rgba(6,182,212,0.4)] transition-all duration-500"
                      style={{ width: `${d.percentage}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* Modal Footer */}
            <div className="mt-6 pt-4 border-t border-[var(--hs-border)] flex items-center justify-between">
              <a
                href={selectedDemographicVideo.watch_url}
                target="_blank"
                rel="noreferrer noopener"
                className="btn btn-secondary py-2 px-3 text-xs inline-flex items-center gap-1.5"
              >
                <span>Watch on YouTube</span>
                <IconExternalLink size={12} />
              </a>
              <button
                type="button"
                onClick={() => setSelectedDemographicVideo(null)}
                className="btn btn-primary py-2 px-4 text-xs font-semibold"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
