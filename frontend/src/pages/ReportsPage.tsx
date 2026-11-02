import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { SheetsPanel } from "../components/SheetsPanel";
import {
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconCopy,
  IconDownload,
  IconExternalLink,
  IconFileSpreadsheet,
  IconRefreshCw,
  IconSearch,
  IconTrendingUp,
  IconUpload,
  IconYouTube,
} from "../components/Icons";
import {
  useAnalyticsOverview,
  useAnalyticsVideos,
  useConnections,
  useDailySummary,
  usePolicyStatus,
} from "../hooks/api";
import type { AnalyticsVideoItem } from "../api/types";
import { formatWhen } from "../lib/state";

interface ColumnDef {
  id: string;
  label: string;
  description: string;
  defaultSelected: boolean;
  getValue: (item: AnalyticsVideoItem) => string | number;
  formatCell?: (value: string | number, item: AnalyticsVideoItem) => React.ReactNode;
}

const AVAILABLE_COLUMNS: ColumnDef[] = [
  {
    id: "title",
    label: "Video Title / Name",
    description: "Original clip headline or caption",
    defaultSelected: true,
    getValue: (item) => item.title,
    formatCell: (val) => <span className="font-medium text-white truncate max-w-[260px] inline-block">{val}</span>,
  },
  {
    id: "video_url",
    label: "Content URL",
    description: "Direct YouTube watch link",
    defaultSelected: true,
    getValue: (item) => item.watch_url || (item.video_id ? `https://www.youtube.com/watch?v=${item.video_id}` : ""),
    formatCell: (val) =>
      val ? (
        <a
          href={String(val)}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[var(--hs-violet-bright)] hover:underline flex items-center gap-1 font-mono text-[11px]"
        >
          <span>Watch</span>
          <IconExternalLink size={11} />
        </a>
      ) : (
        <span className="text-[var(--hs-text-muted)]">—</span>
      ),
  },
  {
    id: "channel_title",
    label: "Channel Name",
    description: "Brand channel identity",
    defaultSelected: true,
    getValue: (item) => item.channel_title,
    formatCell: (val) => <span className="font-semibold text-white">{val}</span>,
  },
  {
    id: "channel_handle",
    label: "Channel Handle",
    description: "YouTube handle e.g. @peakclips",
    defaultSelected: true,
    getValue: (item) => item.channel_handle || "",
    formatCell: (val) => <span className="text-[var(--hs-text-secondary)] font-mono text-[11px]">{val || "—"}</span>,
  },
  {
    id: "view_count",
    label: "Total Views",
    description: "Live verified YouTube views",
    defaultSelected: true,
    getValue: (item) => item.view_count,
    formatCell: (val) => (
      <span className="font-mono font-bold text-white bg-[var(--hs-surface-sunken)] px-2 py-0.5 rounded border border-[var(--hs-border)]/60 text-xs">
        {Number(val).toLocaleString()}
      </span>
    ),
  },
  {
    id: "published_at",
    label: "Published Date",
    description: "Date clip went live on YouTube",
    defaultSelected: true,
    getValue: (item) => (item.published_at ? item.published_at.slice(0, 10) : item.publish_at_utc ? item.publish_at_utc.slice(0, 10) : ""),
    formatCell: (_val, item) => {
      const raw = item.published_at || item.publish_at_utc;
      return <span className="text-[var(--hs-text-secondary)] text-xs">{raw ? formatWhen(raw) : "—"}</span>;
    },
  },
  {
    id: "like_count",
    label: "Likes",
    description: "Total viewer thumbs up",
    defaultSelected: false,
    getValue: (item) => item.like_count || 0,
    formatCell: (val) => <span className="font-mono text-xs">{Number(val).toLocaleString()}</span>,
  },
  {
    id: "comment_count",
    label: "Comments",
    description: "Audience comments count",
    defaultSelected: false,
    getValue: (item) => item.comment_count || 0,
    formatCell: (val) => <span className="font-mono text-xs">{Number(val).toLocaleString()}</span>,
  },
  {
    id: "creator_approval",
    label: "Creator Approval",
    description: "Client sign-off note",
    defaultSelected: false,
    getValue: (item) => (item.creator_approval ? item.creator_approval.toUpperCase() : "PENDING"),
    formatCell: (val) => {
      const isApproved = String(val).toUpperCase() === "APPROVED";
      const isRejected = String(val).toUpperCase() === "REJECTED";
      return (
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider ${
            isApproved
              ? "bg-[var(--hs-success)]/10 text-[var(--hs-success)] border border-[var(--hs-success)]/30"
              : isRejected
              ? "bg-[var(--hs-danger)]/10 text-[var(--hs-danger)] border border-[var(--hs-danger)]/30"
              : "bg-[var(--hs-surface-sunken)] text-[var(--hs-text-muted)] border border-[var(--hs-border)]"
          }`}
        >
          {val}
        </span>
      );
    },
  },
  {
    id: "is_qualified",
    label: "1K+ Deliverable Status",
    description: "Client sign-off qualification",
    defaultSelected: false,
    getValue: (item) => (item.is_qualified ? "QUALIFIED (1K+)" : "UNDER 1K"),
    formatCell: (val, item) => (
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold ${
          item.is_qualified
            ? "bg-[var(--hs-success)]/15 text-[var(--hs-success)] border border-[var(--hs-success)]/30"
            : "bg-[var(--hs-warning)]/10 text-[var(--hs-warning)] border border-[var(--hs-warning)]/30"
        }`}
      >
        {val}
      </span>
    ),
  },
  {
    id: "video_id",
    label: "YouTube Video ID",
    description: "Raw 11-char YouTube ID",
    defaultSelected: false,
    getValue: (item) => item.video_id || "",
    formatCell: (val) => <span className="font-mono text-[11px] text-[var(--hs-text-muted)]">{val || "—"}</span>,
  },
];

type DurationPreset = "all" | "7d" | "30d" | "this_month" | "last_month" | "custom";

export function ReportsPage() {
  const overview = useAnalyticsOverview();
  const connections = useConnections();

  // Filters State
  const [selectedChannel, setSelectedChannel] = useState<string>("");
  const [duration, setDuration] = useState<DurationPreset>("all");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [searchFilter, setSearchFilter] = useState("");
  const [copied, setCopied] = useState(false);
  const [showCloudSync, setShowCloudSync] = useState(false);

  // Selected Columns State
  const [selectedColumnIds, setSelectedColumnIds] = useState<string[]>(() =>
    AVAILABLE_COLUMNS.filter((c) => c.defaultSelected).map((c) => c.id)
  );

  // Calculate Date Boundaries
  const { startDate, endDate } = useMemo(() => {
    const now = new Date();
    if (duration === "7d") {
      const start = new Date(now.getTime() - 7 * 86400000);
      return { startDate: start.toISOString().slice(0, 10), endDate: now.toISOString().slice(0, 10) };
    }
    if (duration === "30d") {
      const start = new Date(now.getTime() - 30 * 86400000);
      return { startDate: start.toISOString().slice(0, 10), endDate: now.toISOString().slice(0, 10) };
    }
    if (duration === "this_month") {
      const start = new Date(now.getFullYear(), now.getMonth(), 1);
      return { startDate: start.toISOString().slice(0, 10), endDate: now.toISOString().slice(0, 10) };
    }
    if (duration === "last_month") {
      const start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      const end = new Date(now.getFullYear(), now.getMonth(), 0);
      return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
    }
    if (duration === "custom") {
      return { startDate: customStart || undefined, endDate: customEnd || undefined };
    }
    return { startDate: undefined, endDate: undefined };
  }, [duration, customStart, customEnd]);

  // Query videos matching scope
  const videosQuery = useAnalyticsVideos({
    channel_id: selectedChannel || undefined,
    start_date: startDate,
    end_date: endDate,
    search: searchFilter || undefined,
    sort_by: "newest",
    limit: 500,
  });

  const channels = overview.data?.channel_summaries ?? [];
  const videos = videosQuery.data?.items ?? [];

  // Summary Metrics
  const totalTrackedVideos = overview.data?.total_videos ?? 0;
  const totalTrackedViews = overview.data?.total_views ?? 0;
  const filteredCount = videos.length;
  const filteredViews = useMemo(() => videos.reduce((sum, v) => sum + (v.view_count || 0), 0), [videos]);

  // Active Columns in chosen order
  const activeColumns = useMemo(
    () => AVAILABLE_COLUMNS.filter((c) => selectedColumnIds.includes(c.id)),
    [selectedColumnIds]
  );

  const toggleColumn = (id: string) => {
    setSelectedColumnIds((prev) =>
      prev.includes(id) ? (prev.length > 1 ? prev.filter((c) => c !== id) : prev) : [...prev, id]
    );
  };

  const applyPreset = (preset: "essential" | "deliverables" | "performance" | "all") => {
    if (preset === "essential") {
      setSelectedColumnIds(["title", "video_url", "channel_title", "view_count", "published_at"]);
    } else if (preset === "deliverables") {
      setSelectedColumnIds([
        "title",
        "video_url",
        "channel_title",
        "channel_handle",
        "view_count",
        "published_at",
        "creator_approval",
        "is_qualified",
      ]);
    } else if (preset === "performance") {
      setSelectedColumnIds([
        "title",
        "video_url",
        "channel_title",
        "view_count",
        "published_at",
        "like_count",
        "comment_count",
        "is_qualified",
      ]);
    } else {
      setSelectedColumnIds(AVAILABLE_COLUMNS.map((c) => c.id));
    }
  };

  // Export to CSV with UTF-8 BOM
  const handleExportCsv = () => {
    const headers = activeColumns.map((c) => c.label);
    const rows = videos.map((v) => activeColumns.map((c) => c.getValue(v)));
    const csvContent = [
      headers.map((h) => `"${String(h).replace(/"/g, '""')}"`).join(","),
      ...rows.map((row) => row.map((cell) => `"${String(cell ?? "").replace(/"/g, '""')}"`).join(",")),
    ].join("\r\n");

    const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
    const link = document.createElement("a");
    const chanSuffix = selectedChannel ? `_${selectedChannel}` : "_all_channels";
    const dateSuffix = startDate || endDate ? `_${startDate || "start"}_to_${endDate || "now"}` : "";
    link.href = URL.createObjectURL(blob);
    link.download = `Hexton_Report${chanSuffix}${dateSuffix}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  // Export to native Excel XML (.xls)
  const handleExportExcel = () => {
    const headers = activeColumns.map((c) => c.label);
    const rows = videos.map((v) => activeColumns.map((c) => c.getValue(v)));

    const escapeXml = (unsafe: string | number) =>
      String(unsafe)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&apos;");

    let xml = `<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:html="http://www.w3.org/TR/REC-html40">
 <Styles>
  <Style ss:ID="Header">
   <Font ss:Bold="1" ss:Color="#FFFFFF" ss:Size="11"/>
   <Interior ss:Color="#7C3AED" ss:Pattern="Solid"/>
   <Alignment ss:Horizontal="Center" ss:Vertical="Center"/>
  </Style>
  <Style ss:ID="Data">
   <Alignment ss:Vertical="Center"/>
  </Style>
  <Style ss:ID="Number">
   <Alignment ss:Horizontal="Right" ss:Vertical="Center"/>
   <NumberFormat ss:Format="#,##0"/>
  </Style>
 </Styles>
 <Worksheet ss:Name="Hexton Report">
  <Table ss:DefaultRowHeight="20">
`;
    xml += '   <Row ss:StyleID="Header" ss:Height="26">\n';
    for (const h of headers) {
      xml += `    <Cell><Data ss:Type="String">${escapeXml(h)}</Data></Cell>\n`;
    }
    xml += "   </Row>\n";

    for (const row of rows) {
      xml += '   <Row ss:StyleID="Data">\n';
      for (const cell of row) {
        if (typeof cell === "number") {
          xml += `    <Cell ss:StyleID="Number"><Data ss:Type="Number">${cell}</Data></Cell>\n`;
        } else {
          xml += `    <Cell><Data ss:Type="String">${escapeXml(String(cell ?? ""))}</Data></Cell>\n`;
        }
      }
      xml += "   </Row>\n";
    }

    xml += `  </Table>
 </Worksheet>
</Workbook>`;

    const blob = new Blob([xml], { type: "application/vnd.ms-excel;charset=utf-8;" });
    const link = document.createElement("a");
    const chanSuffix = selectedChannel ? `_${selectedChannel}` : "_all_channels";
    const dateSuffix = startDate || endDate ? `_${startDate || "start"}_to_${endDate || "now"}` : "";
    link.href = URL.createObjectURL(blob);
    link.download = `Hexton_Report${chanSuffix}${dateSuffix}.xls`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  // Copy Tab-Separated Values (TSV) to clipboard
  const handleCopyTsv = async () => {
    const headers = activeColumns.map((c) => c.label);
    const rows = videos.map((v) => activeColumns.map((c) => c.getValue(v)));
    const tsv = [
      headers.join("\t"),
      ...rows.map((row) => row.map((cell) => String(cell ?? "").replace(/[\t\n\r]/g, " ")).join("\t")),
    ].join("\n");

    try {
      await navigator.clipboard.writeText(tsv);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // fallback
    }
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Page Header */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--hs-violet-soft)] border border-[var(--hs-border-strong)] text-[var(--hs-violet-bright)]">
              <IconFileSpreadsheet size={16} />
            </span>
            <h1 className="text-2xl font-bold tracking-tight text-white">Spreadsheets &amp; Reports</h1>
          </div>
          <p className="mt-1 text-sm text-[var(--hs-text-secondary)]">
            Custom spreadsheet builder. Choose your duration, select your channels, pick the data points you want, and export to Excel.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <Link
            to="/analytics"
            className="btn btn-secondary py-2.5 px-4 rounded-xl font-medium flex items-center gap-2 text-xs sm:text-sm"
          >
            <IconTrendingUp size={15} />
            <span>1K+ Deliverables Hub</span>
          </Link>
          <button
            type="button"
            onClick={handleExportExcel}
            disabled={filteredCount === 0}
            className="btn btn-primary shadow-lg shadow-violet-950/50 py-2.5 px-4 rounded-xl font-semibold flex items-center gap-2 text-xs sm:text-sm"
          >
            <IconDownload size={15} />
            <span>Export to Excel</span>
          </button>
        </div>
      </header>

      {/* 4 Minimalist KPI Cards */}
      <div className="grid gap-3.5 grid-cols-2 lg:grid-cols-4">
        <div className="hs-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--hs-text-muted)]">
              Tracked Videos
            </span>
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--hs-violet-soft)] border border-[var(--hs-border-strong)] text-[var(--hs-violet-bright)]">
              <IconYouTube size={14} />
            </div>
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-white">{totalTrackedVideos}</div>
          <span className="mt-0.5 text-[11px] text-[var(--hs-text-muted)] block">Across all channels</span>
        </div>

        <div className="hs-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--hs-text-muted)]">
              Total Reach
            </span>
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--hs-success)]/10 border border-[var(--hs-success)]/30 text-[var(--hs-success)]">
              <IconTrendingUp size={14} />
            </div>
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-[var(--hs-success)]">
            {totalTrackedViews.toLocaleString()}
          </div>
          <span className="mt-0.5 text-[11px] text-[var(--hs-text-muted)] block">Total views tracked</span>
        </div>

        <div className="hs-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--hs-text-muted)]">
              Active Channels
            </span>
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--hs-surface-sunken)] border border-[var(--hs-border)] text-white">
              <IconUpload size={14} />
            </div>
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-white">{channels.length || 13}</div>
          <span className="mt-0.5 text-[11px] text-[var(--hs-text-muted)] block">Monitored brand channels</span>
        </div>

        <div className="hs-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--hs-text-muted)]">
              Ready for Export
            </span>
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--hs-violet-soft)] border border-[var(--hs-violet)]/40 text-[var(--hs-violet-bright)]">
              <IconFileSpreadsheet size={14} />
            </div>
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-[var(--hs-violet-bright)]">{filteredCount}</div>
          <span className="mt-0.5 text-[11px] text-[var(--hs-text-muted)] block">
            {filteredViews.toLocaleString()} views selected
          </span>
        </div>
      </div>

      {/* Card 1: Scope & Date Selection */}
      <section className="hs-card p-6" aria-label="Scope and Duration">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--hs-border)]/60 pb-3 mb-5">
          <div className="flex items-center gap-2.5">
            <span className="grid h-6 w-6 place-items-center rounded-full bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] text-xs font-bold">
              1
            </span>
            <div>
              <h2 className="text-base font-bold text-white tracking-tight">Scope &amp; Duration</h2>
              <p className="text-xs text-[var(--hs-text-muted)]">
                Choose whether you want data for all channels or a single channel, plus the timeframe.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs text-[var(--hs-text-secondary)] font-medium">
            <span>{filteredCount} matching videos found</span>
          </div>
        </div>

        <div className="flex flex-col gap-5">
          {/* Channel Selector */}
          <div>
            <label className="text-xs font-semibold text-[var(--hs-text-muted)] uppercase tracking-wider mb-2.5 block">
              Target Channel
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setSelectedChannel("")}
                className={`px-3 py-1.5 rounded-xl text-xs font-medium transition-all ${
                  selectedChannel === ""
                    ? "bg-[var(--hs-violet)] text-white shadow-md shadow-violet-950/40 ring-1 ring-white/20 font-semibold"
                    : "bg-[var(--hs-surface-raised)] text-[var(--hs-text-secondary)] hover:text-white border border-[var(--hs-border)]"
                }`}
              >
                All Channels ({totalTrackedVideos})
              </button>

              {channels.map((ch) => {
                const isSelected = selectedChannel === ch.channel_id;
                return (
                  <button
                    key={ch.channel_id}
                    type="button"
                    onClick={() => setSelectedChannel(ch.channel_id)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs transition-all ${
                      isSelected
                        ? "bg-[var(--hs-violet)] text-white shadow-md shadow-violet-950/40 ring-1 ring-white/20 font-semibold"
                        : "bg-[var(--hs-surface-raised)] text-[var(--hs-text-secondary)] hover:text-white border border-[var(--hs-border)]"
                    }`}
                  >
                    <span>{ch.channel_title}</span>
                    <span className="text-[10px] opacity-70">({ch.video_count})</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Duration Selector */}
          <div>
            <label className="text-xs font-semibold text-[var(--hs-text-muted)] uppercase tracking-wider mb-2.5 block">
              Timeframe / Date Range
            </label>
            <div className="flex flex-wrap items-center gap-2">
              {(
                [
                  { id: "all", label: "All Time" },
                  { id: "7d", label: "Last 7 Days" },
                  { id: "30d", label: "Last 30 Days" },
                  { id: "this_month", label: "This Month" },
                  { id: "last_month", label: "Last Month" },
                  { id: "custom", label: "Custom Range" },
                ] as const
              ).map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setDuration(p.id)}
                  className={`px-3 py-1.5 rounded-xl text-xs transition-all ${
                    duration === p.id
                      ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-violet)]/60 font-semibold shadow-sm"
                      : "bg-[var(--hs-surface-raised)] text-[var(--hs-text-secondary)] hover:text-white border border-[var(--hs-border)]"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>

            {/* Custom Range Date Pickers */}
            {duration === "custom" && (
              <div className="mt-3 flex flex-wrap items-center gap-3 p-3 rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-sunken)]/60 animate-in fade-in-0 duration-150">
                <div className="flex items-center gap-2">
                  <label htmlFor="custom-start" className="text-xs text-[var(--hs-text-muted)]">
                    From:
                  </label>
                  <input
                    id="custom-start"
                    type="date"
                    className="input py-1 px-2.5 text-xs bg-[var(--hs-surface-raised)] border-[var(--hs-border)] rounded-lg text-white font-mono"
                    value={customStart}
                    onChange={(e) => setCustomStart(e.target.value)}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label htmlFor="custom-end" className="text-xs text-[var(--hs-text-muted)]">
                    To:
                  </label>
                  <input
                    id="custom-end"
                    type="date"
                    className="input py-1 px-2.5 text-xs bg-[var(--hs-surface-raised)] border-[var(--hs-border)] rounded-lg text-white font-mono"
                    value={customEnd}
                    onChange={(e) => setCustomEnd(e.target.value)}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* Card 2: Select Spreadsheet Columns */}
      <section className="hs-card p-6" aria-label="Select Columns">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--hs-border)]/60 pb-3 mb-5">
          <div className="flex items-center gap-2.5">
            <span className="grid h-6 w-6 place-items-center rounded-full bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] text-xs font-bold">
              2
            </span>
            <div>
              <h2 className="text-base font-bold text-white tracking-tight">Choose Columns to Include</h2>
              <p className="text-xs text-[var(--hs-text-muted)]">
                Select only what you want in your spreadsheet. Your exported file will contain exactly these fields.
              </p>
            </div>
          </div>

          {/* Quick Presets */}
          <div className="flex items-center gap-1.5 bg-[var(--hs-surface-sunken)] p-1 rounded-xl border border-[var(--hs-border)]">
            <span className="text-[10px] text-[var(--hs-text-muted)] uppercase tracking-wider px-2 font-semibold">
              Presets:
            </span>
            <button
              type="button"
              onClick={() => applyPreset("essential")}
              className="px-2.5 py-1 text-[11px] rounded-lg font-medium text-[var(--hs-text-secondary)] hover:text-white hover:bg-[var(--hs-surface-raised)] transition-colors"
            >
              Essential (5)
            </button>
            <button
              type="button"
              onClick={() => applyPreset("deliverables")}
              className="px-2.5 py-1 text-[11px] rounded-lg font-medium text-[var(--hs-text-secondary)] hover:text-white hover:bg-[var(--hs-surface-raised)] transition-colors"
            >
              Client Deliverables
            </button>
            <button
              type="button"
              onClick={() => applyPreset("performance")}
              className="px-2.5 py-1 text-[11px] rounded-lg font-medium text-[var(--hs-text-secondary)] hover:text-white hover:bg-[var(--hs-surface-raised)] transition-colors"
            >
              Full Analytics
            </button>
            <button
              type="button"
              onClick={() => applyPreset("all")}
              className="px-2.5 py-1 text-[11px] rounded-lg font-medium text-[var(--hs-violet-bright)] hover:bg-[var(--hs-violet-soft)] transition-colors"
            >
              Select All
            </button>
          </div>
        </div>

        {/* Column Selection Grid */}
        <div className="grid gap-2.5 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
          {AVAILABLE_COLUMNS.map((col) => {
            const isChecked = selectedColumnIds.includes(col.id);
            return (
              <label
                key={col.id}
                className={`flex items-start gap-3 p-3 rounded-xl border transition-all cursor-pointer select-none ${
                  isChecked
                    ? "border-[var(--hs-violet)]/60 bg-[var(--hs-violet-soft)]/20 shadow-sm"
                    : "border-[var(--hs-border)] bg-[var(--hs-surface-raised)]/40 hover:bg-[var(--hs-surface-raised)] opacity-70 hover:opacity-100"
                }`}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggleColumn(col.id)}
                  className="mt-0.5 rounded border-[var(--hs-border)] bg-[var(--hs-canvas)] text-[var(--hs-violet)] focus:ring-[var(--hs-violet)]"
                />
                <div className="flex flex-col min-w-0">
                  <span className={`text-xs font-semibold ${isChecked ? "text-white" : "text-[var(--hs-text-secondary)]"}`}>
                    {col.label}
                  </span>
                  <span className="text-[11px] text-[var(--hs-text-muted)] truncate mt-0.5">{col.description}</span>
                </div>
              </label>
            );
          })}
        </div>

        <div className="mt-4 flex items-center justify-between text-xs text-[var(--hs-text-muted)] pt-3 border-t border-[var(--hs-border)]/40">
          <span>
            <strong className="text-white font-semibold">{activeColumns.length}</strong> of {AVAILABLE_COLUMNS.length} columns selected
          </span>
          <span className="text-[11px]">Columns will appear in the spreadsheet in this order</span>
        </div>
      </section>

      {/* Card 3: Live Spreadsheet Preview & Instant Export */}
      <section className="hs-card p-6" aria-label="Live Spreadsheet Preview">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--hs-border)]/60 pb-4 mb-4">
          <div className="flex items-center gap-2.5">
            <span className="grid h-6 w-6 place-items-center rounded-full bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] text-xs font-bold">
              3
            </span>
            <div>
              <h2 className="text-base font-bold text-white tracking-tight">Live Spreadsheet Preview &amp; Export</h2>
              <p className="text-xs text-[var(--hs-text-muted)]">
                Review the exact data before exporting. What you see here is what your file will contain.
              </p>
            </div>
          </div>

          {/* Export Actions Bar */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleCopyTsv}
              disabled={filteredCount === 0}
              className="btn btn-secondary py-2 px-3 rounded-xl text-xs font-medium flex items-center gap-1.5"
              title="Copy spreadsheet tab-separated data to paste into Google Sheets or Excel directly"
            >
              {copied ? <IconCheck size={13} className="text-[var(--hs-success)]" /> : <IconCopy size={13} />}
              <span>{copied ? "Copied to Clipboard!" : "Copy (Ctrl+V Ready)"}</span>
            </button>

            <button
              type="button"
              onClick={handleExportCsv}
              disabled={filteredCount === 0}
              className="btn btn-secondary py-2 px-3 rounded-xl text-xs font-medium flex items-center gap-1.5"
            >
              <IconDownload size={13} />
              <span>Export CSV</span>
            </button>

            <button
              type="button"
              onClick={handleExportExcel}
              disabled={filteredCount === 0}
              className="btn btn-primary py-2 px-4 rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-md shadow-violet-950/40"
            >
              <IconFileSpreadsheet size={14} />
              <span>Export to Excel (.xls)</span>
            </button>
          </div>
        </div>

        {/* Live Filter Bar */}
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <div className="relative w-64 max-w-full">
              <span className="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none text-[var(--hs-text-muted)]">
                <IconSearch size={13} />
              </span>
              <input
                type="text"
                placeholder="Filter videos by title..."
                className="input py-1.5 pl-8 pr-3 text-xs w-full bg-[var(--hs-surface-raised)] border-[var(--hs-border)] rounded-lg text-white"
                value={searchFilter}
                onChange={(e) => setSearchFilter(e.target.value)}
              />
            </div>
            {searchFilter && (
              <button
                type="button"
                onClick={() => setSearchFilter("")}
                className="text-xs text-[var(--hs-text-muted)] hover:text-white"
              >
                Clear
              </button>
            )}
          </div>

          <div className="text-xs text-[var(--hs-text-secondary)] font-mono flex items-center gap-3">
            <span>
              Rows: <strong className="text-white">{filteredCount}</strong>
            </span>
            <span>•</span>
            <span>
              Total Views: <strong className="text-white">{filteredViews.toLocaleString()}</strong>
            </span>
            <span>•</span>
            <span>
              Columns: <strong className="text-white">{activeColumns.length}</strong>
            </span>
          </div>
        </div>

        {/* Preview Table */}
        {videosQuery.isLoading ? (
          <div className="py-16 text-center text-xs text-[var(--hs-text-muted)] flex flex-col items-center justify-center gap-2">
            <IconRefreshCw size={16} className="animate-spin text-[var(--hs-violet-bright)]" />
            <span>Loading spreadsheet rows…</span>
          </div>
        ) : filteredCount === 0 ? (
          <div className="py-16 text-center text-xs text-[var(--hs-text-muted)] border border-dashed border-[var(--hs-border)] rounded-xl">
            <p className="font-semibold text-white">No videos matched this duration or channel selection</p>
            <p className="mt-1 text-[11px]">Try switching the timeframe to &quot;All Time&quot; or selecting &quot;All Channels&quot;.</p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-[var(--hs-border)] max-h-[480px]">
            <table className="w-full border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 bg-[var(--hs-surface-sunken)] border-b border-[var(--hs-border)] shadow-sm">
                <tr>
                  <th scope="col" className="px-3 py-2.5 font-semibold text-[10px] uppercase tracking-wider text-[var(--hs-text-muted)] w-10 text-center">
                    #
                  </th>
                  {activeColumns.map((col) => (
                    <th
                      key={col.id}
                      scope="col"
                      className="px-3.5 py-2.5 font-semibold text-[10px] uppercase tracking-wider text-[var(--hs-text-muted)] whitespace-nowrap"
                    >
                      {col.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--hs-border)]/50 bg-[var(--hs-canvas)]">
                {videos.map((item, idx) => (
                  <tr key={item.id} className="hover:bg-[var(--hs-surface-raised)]/60 transition-colors">
                    <td className="px-3 py-2 text-center font-mono text-[11px] text-[var(--hs-text-muted)]">{idx + 1}</td>
                    {activeColumns.map((col) => (
                      <td key={col.id} className="px-3.5 py-2 whitespace-nowrap text-xs text-[var(--hs-text)]">
                        {col.formatCell ? col.formatCell(col.getValue(item), item) : col.getValue(item)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Card 4: Optional Automated Google Sheets Sync */}
      <section className="hs-card p-5" aria-label="Optional Google Sheets Cloud Sync">
        <button
          type="button"
          onClick={() => setShowCloudSync((prev) => !prev)}
          className="w-full flex items-center justify-between text-left cursor-pointer group"
        >
          <div className="flex items-center gap-3">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-[var(--hs-surface-sunken)] border border-[var(--hs-border)] text-[var(--hs-violet-bright)] group-hover:border-[var(--hs-violet)]/50 transition-colors">
              <IconUpload size={15} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-white group-hover:text-[var(--hs-violet-bright)] transition-colors">
                  Automated Google Sheets Live Sync (Optional Cloud Integration)
                </h3>
                <span className="rounded bg-[var(--hs-surface-sunken)] border border-[var(--hs-border)] px-1.5 py-0.2 text-[10px] uppercase font-mono text-[var(--hs-text-muted)]">
                  Optional
                </span>
              </div>
              <p className="mt-0.5 text-xs text-[var(--hs-text-muted)]">
                Want scheduled videos to be auto-pushed to a live Google Sheet tab on every upload? Configure Google Sheets integration here.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-1 text-xs text-[var(--hs-text-secondary)] font-medium">
            <span>{showCloudSync ? "Hide Google Sheets Setup" : "Configure Cloud Sync"}</span>
            {showCloudSync ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          </div>
        </button>

        {showCloudSync && (
          <div className="mt-5 border-t border-[var(--hs-border)]/60 pt-5 flex flex-col gap-6 animate-in fade-in-0 duration-200">
            <SheetsPanel connections={connections.data ?? []} />
            <DailyCompletion />
          </div>
        )}
      </section>
    </div>
  );
}

/**
 * The daily completion summary report.
 */
function DailyCompletion() {
  const policy = usePolicyStatus();
  const accepted = policy.data?.accepted ?? false;
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const summary = useDailySummary(accepted ? date : null);

  if (!accepted) return null;

  const rows = summary.data?.rows ?? [];
  const first = rows.length > 0 ? rows[0] : undefined;
  const columns = first ? Object.keys(first) : [];

  return (
    <section className="rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)]/50 p-5" aria-label="Daily completion summary">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--hs-border)]/60 pb-3 mb-4">
        <div>
          <h4 className="text-sm font-semibold text-white tracking-tight">Daily Completion Ledger</h4>
          <p className="mt-0.5 text-xs text-[var(--hs-text-muted)]">
            Everything scheduled for that report date, counted by outcome.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-[var(--hs-text-secondary)]" htmlFor="summary-date">
            Report date
          </label>
          <input
            id="summary-date"
            aria-label="Report date"
            type="date"
            className="input py-1 px-3 text-xs w-auto bg-[var(--hs-surface-raised)] border-[var(--hs-border)] rounded-lg text-white font-mono"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </div>
      </div>

      {summary.isLoading && <p className="text-xs text-[var(--hs-text-muted)] animate-pulse">Reading summary…</p>}
      {summary.data && rows.length === 0 && (
        <p className="text-xs text-[var(--hs-text-muted)] py-4 text-center">
          No summary has been produced for {date} yet. Rows appear once at least one upload has been reported for that date.
        </p>
      )}
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-[var(--hs-border)] text-xs uppercase tracking-wider text-[var(--hs-text-muted)] bg-[var(--hs-surface-raised)]">
                {columns.map((column) => (
                  <th key={column} scope="col" className="px-3 py-2 font-semibold">
                    {column.replace(/_/g, " ")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--hs-border)]/50">
              {rows.map((row, index) => (
                <tr key={index} className="hover:bg-[var(--hs-surface-raised)]/50 transition-colors">
                  {columns.map((column) => (
                    <td key={column} className="px-3 py-2 text-[var(--hs-text)]">
                      {String(row[column] ?? "—")}
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
