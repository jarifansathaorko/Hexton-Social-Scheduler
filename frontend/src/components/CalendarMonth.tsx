import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { Job } from "../api/types";
import { cueClass, cueFromBackend, formatWhen } from "../lib/state";
import {
  IconChevronLeft,
  IconChevronRight,
  IconPlus,
} from "./Icons";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/**
 * Month-view Calendar matching Panel 2 of the HexScheduler design reference:
 * - View mode toggles: Month, Week, List, Today
 * - Month navigation header with previous/next controls
 * - Status Legend pills with canonical colors (Scheduled, In Progress, Needs Attention, Failed, Completed)
 * - Fine dark grid with day numbers, violet highlight for today, and styled job chips
 * - Direct click to select job into URL query param
 */
export function CalendarMonth({
  jobs,
  onSelect,
  selectedId,
}: {
  jobs: Job[];
  onSelect: (id: number) => void;
  selectedId: number | null;
}) {
  const [viewMode, setViewMode] = useState<"Month" | "Week" | "List">("Month");
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const { weeks, unscheduled } = useMemo(() => {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const first = new Date(year, month, 1);
    const startOffset = first.getDay(); // Sunday-first matching reference collage
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const cells: (Date | null)[] = Array.from({ length: startOffset }, () => null);
    for (let day = 1; day <= daysInMonth; day += 1) cells.push(new Date(year, month, day));
    while (cells.length % 7 !== 0) cells.push(null);

    const weeks: (Date | null)[][] = [];
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));

    const unscheduled = jobs.filter((j) => !j.publish_at_utc);
    return { weeks, unscheduled };
  }, [cursor, jobs]);

  const byDay = useMemo(() => {
    const map = new Map<string, Job[]>();
    for (const job of jobs) {
      if (!job.publish_at_utc) continue;
      const date = new Date(job.publish_at_utc);
      const key = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
      const list = map.get(key) ?? [];
      list.push(job);
      map.set(key, list);
    }
    return map;
  }, [jobs]);

  const todayKey = (() => {
    const now = new Date();
    return `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`;
  })();

  const monthTitle = cursor.toLocaleDateString("en-US", { month: "long", year: "numeric" });

  const goToday = () => {
    const now = new Date();
    setCursor(new Date(now.getFullYear(), now.getMonth(), 1));
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Calendar Top Controls Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 hs-card p-4">
        {/* Month Title & Prev/Next */}
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-bold text-white tracking-tight">{monthTitle}</h2>
          <div className="flex items-center gap-1">
            <button
              className="btn p-1.5 rounded-lg text-[var(--hs-text-muted)] hover:text-white"
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
              aria-label="Previous month"
            >
              <IconChevronLeft size={16} />
            </button>
            <button
              className="btn p-1.5 rounded-lg text-[var(--hs-text-muted)] hover:text-white"
              onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
              aria-label="Next month"
            >
              <IconChevronRight size={16} />
            </button>
          </div>
        </div>

        {/* View Mode Segmented Control + Add Schedule CTA */}
        <div className="flex items-center gap-3">
          <div className="flex items-center rounded-lg border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] p-0.5">
            {(["Month", "Week", "List"] as const).map((mode) => (
              <button
                key={mode}
                className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${
                  viewMode === mode
                    ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)] shadow-sm"
                    : "text-[var(--hs-text-secondary)] hover:text-white"
                }`}
                onClick={() => setViewMode(mode)}
              >
                {mode}
              </button>
            ))}
            <button
              className="px-3 py-1 text-xs font-medium rounded-md text-[var(--hs-text-secondary)] hover:text-white border-l border-[var(--hs-border)] ml-1"
              onClick={goToday}
            >
              Today
            </button>
          </div>

          <Link to="/new" className="btn btn-primary py-1.5 px-3 rounded-lg text-xs font-semibold">
            <IconPlus size={14} />
            Add Schedule
          </Link>
        </div>
      </div>

      {/* Status Legend Pills */}
      <StatusLegendBar />

      {/* Calendar Surface */}
      <div className="hs-card overflow-hidden">
        {/* Weekday Columns */}
        <div className="grid grid-cols-7 border-b border-[var(--hs-border)] bg-[var(--hs-surface-raised)]">
          {WEEKDAYS.map((day) => (
            <div key={day} className="px-3 py-2.5 text-center text-xs font-semibold uppercase tracking-wider text-[var(--hs-text-muted)]">
              {day}
            </div>
          ))}
        </div>

        {/* Date Matrix Grid */}
        <div className="grid grid-cols-7 divide-x divide-y divide-[var(--hs-border)]/50 bg-[var(--hs-surface)]">
          {weeks.flat().map((date, index) => {
            const key = date ? `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}` : `gap-${index}`;
            const dayJobs = date ? (byDay.get(key) ?? []) : [];
            const isToday = date && key === todayKey;

            return (
              <div
                key={key}
                className={`min-h-[120px] p-2 flex flex-col justify-between transition-colors ${
                  !date ? "bg-[var(--hs-canvas)]/40" : isToday ? "bg-[var(--hs-violet-soft)]/20" : "hover:bg-[var(--hs-surface-raised)]/40"
                }`}
              >
                {date && (
                  <>
                    <div className="flex items-center justify-between mb-1.5">
                      <span
                        className={`text-xs font-mono font-semibold ${
                          isToday
                            ? "grid h-6 w-6 place-items-center rounded-full bg-[var(--hs-violet)] text-white shadow-sm"
                            : "text-[var(--hs-text-muted)]"
                        }`}
                      >
                        {date.getDate()}
                      </span>
                      {dayJobs.length > 2 && (
                        <span className="text-[10px] font-semibold text-[var(--hs-violet-bright)] bg-[var(--hs-violet-soft)] border border-[var(--hs-border-strong)] px-1.5 rounded-full">
                          +{dayJobs.length - 2}
                        </span>
                      )}
                    </div>

                    {/* Job Event Chips */}
                    <ul className="flex flex-col gap-1.5 overflow-hidden">
                      {dayJobs.slice(0, 3).map((job) => {
                        const isSelected = job.id === selectedId;
                        const cue = cueFromBackend(job.display_cue);
                        const chipClass =
                          cue === "warning"
                            ? "bg-[var(--hs-danger)]/15 border-[var(--hs-danger)]/40 text-[var(--hs-danger)]"
                            : cue === "attention"
                              ? "bg-[var(--hs-warning)]/15 border-[var(--hs-warning)]/40 text-[var(--hs-warning)]"
                              : cue === "success"
                                ? "bg-[var(--hs-success)]/15 border-[var(--hs-success)]/40 text-[var(--hs-success)]"
                                : cue === "progress"
                                  ? "bg-[var(--hs-info)]/15 border-[var(--hs-info)]/40 text-[var(--hs-info)]"
                                  : "bg-[var(--hs-violet-soft)] border-[var(--hs-border-strong)] text-[var(--hs-violet-bright)]";

                        return (
                          <li key={job.id}>
                            <button
                              onClick={() => onSelect(job.id)}
                              title={`${job.title} — ${formatWhen(job.publish_at_utc)}`}
                              aria-label={`${job.title || "(untitled)"}, ${job.display_label}, publishes ${formatWhen(job.publish_at_utc)}`}
                              aria-pressed={isSelected}
                              className={`w-full truncate rounded-md border p-1.5 text-left text-[11px] font-medium transition-all ${
                                isSelected
                                  ? "border-[var(--hs-violet)] bg-[var(--hs-violet)] text-white shadow-md shadow-violet-950/50"
                                  : chipClass
                              }`}
                            >
                              <div className="flex items-center gap-1 truncate">
                                <span className="h-1.5 w-1.5 rounded-full bg-current shrink-0" />
                                <span className="truncate">{job.title || "(untitled)"}</span>
                              </div>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Unscheduled Content Rail */}
      {unscheduled.length > 0 && (
        <section className="hs-card p-4">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--hs-text-muted)]">
            No publication time yet ({unscheduled.length})
          </h3>
          <p className="mt-1 text-xs text-[var(--hs-text-secondary)]">
            These jobs have no scheduled publication date. A job with no time will not publish until you schedule it.
          </p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {unscheduled.map((job) => (
              <li key={job.id}>
                <button
                  className={`btn px-3 py-1.5 text-xs font-medium rounded-lg ${cueClass(cueFromBackend(job.display_cue))}`}
                  onClick={() => onSelect(job.id)}
                >
                  {job.title || "(untitled)"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/**
 * Status legend pills row matching Panel 2 of the reference design.
 */
function StatusLegendBar() {
  const legendItems = [
    { label: "Scheduled", color: "bg-[var(--hs-violet)]", border: "border-[var(--hs-violet)]/40" },
    { label: "In Progress", color: "bg-[var(--hs-info)]", border: "border-[var(--hs-info)]/40" },
    { label: "Needs Attention", color: "bg-[var(--hs-warning)]", border: "border-[var(--hs-warning)]/40" },
    { label: "Warning / Failed", color: "bg-[var(--hs-danger)]", border: "border-[var(--hs-danger)]/40" },
    { label: "Completed / Verified", color: "bg-[var(--hs-success)]", border: "border-[var(--hs-success)]/40" },
  ];

  return (
    <div className="flex flex-wrap items-center gap-3 px-1">
      {legendItems.map((item) => (
        <div
          key={item.label}
          className="flex items-center gap-2 rounded-full border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] px-3 py-1 text-xs font-medium text-[var(--hs-text-secondary)]"
        >
          <span className={`h-2 w-2 rounded-full ${item.color}`} aria-hidden />
          <span>{item.label}</span>
        </div>
      ))}
    </div>
  );
}
