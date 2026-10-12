import { useMemo, useState } from "react";
import type { Job } from "../api/types";
import { cueClass, cueFromBackend, formatWhen } from "../lib/state";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/**
 * Month-view calendar.
 *
 * A calendar rather than a list, because "is anything going out on the 14th?" is a
 * shape question and a list answers it badly. Jobs are placed by their **intended**
 * publication time in the operator's own zone, and a job with no time is parked in a
 * separate "unscheduled" rail rather than being given an arbitrary date — assigning
 * one would look like a plan the operator never made.
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
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const { weeks, unscheduled } = useMemo(() => {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const first = new Date(year, month, 1);
    const startOffset = (first.getDay() + 6) % 7; // Monday-first
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

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">
          {cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" })}
        </h2>
        <div className="flex gap-2">
          <button
            className="btn"
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
          >
            Previous
          </button>
          <button
            className="btn"
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
          >
            Next
          </button>
        </div>
      </div>

      <CueLegend />

      <div className="card overflow-hidden">
        <div className="grid grid-cols-7 border-b border-edge bg-surface-raised">
          {WEEKDAYS.map((day) => (
            <div key={day} className="px-2 py-2 text-center text-xs uppercase tracking-wide text-ink-faint">
              {day}
            </div>
          ))}
        </div>
        <div className="grid grid-cols-7">
          {weeks.flat().map((date, index) => {
            const key = date ? `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}` : `gap-${index}`;
            const dayJobs = date ? (byDay.get(key) ?? []) : [];
            return (
              <div
                key={key}
                className={`min-h-[110px] border-b border-r border-edge/50 p-1.5 ${
                  date && key === todayKey ? "bg-accent/[0.07]" : ""
                }`}
              >
                {date && (
                  <>
                    <div
                      className={`mb-1 text-xs ${
                        key === todayKey ? "font-semibold text-accent" : "text-ink-faint"
                      }`}
                    >
                      {date.getDate()}
                    </div>
                    <ul className="flex flex-col gap-1">
                      {dayJobs.map((job) => (
                        <li key={job.id}>
                          <button
                            onClick={() => onSelect(job.id)}
                            title={`${job.title} — ${formatWhen(job.publish_at_utc)}`}
                            aria-label={`${job.title || "(untitled)"}, ${job.display_label}, publishes ${formatWhen(job.publish_at_utc)}`}
                            aria-pressed={job.id === selectedId}
                            className={`w-full truncate rounded border px-1.5 py-1 text-left text-[11px] ${
                              job.id === selectedId
                                ? "border-accent bg-accent/10 text-ink"
                                : `${cueClass(cueFromBackend(job.display_cue))} bg-canvas`
                            }`}
                          >
                            {job.title || "(untitled)"}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {unscheduled.length > 0 && (
        <section className="card p-4">
          <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">
            No publication time yet
          </h3>
          <p className="mt-1 text-xs text-ink-faint">
            These jobs have no schedule, so they are not placed on the calendar. A job
            with no time will not publish — Hexton never invents one.
          </p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {unscheduled.map((job) => (
              <li key={job.id}>
                <button
                  className={`btn px-2.5 py-1 text-xs ${cueClass(cueFromBackend(job.display_cue))}`}
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
 * What the five colours mean.
 *
 * Colour is never the only carrier: the chip beside each entry already spells out the
 * state in words, and this legend names the cue itself. The wording is the PRD's own —
 * "grey outline, blue progress, amber attention, red warning, green filled" — so the
 * legend describes the *cue* without inventing a second mapping from colour to job
 * meaning, which is exactly the duplication the canonical state map exists to prevent.
 */
function CueLegend() {
  const cues: [string, string][] = [
    ["border-cue-neutral/50", "Grey outline — neutral"],
    ["border-cue-progress/60 bg-cue-progress/10", "Blue — in progress"],
    ["border-cue-attention/60 bg-cue-attention/10", "Amber — needs attention"],
    ["border-cue-warning/60 bg-cue-warning/10", "Red — warning"],
    ["border-cue-success/60 bg-cue-success/10", "Green — done"],
  ];
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
      {cues.map(([classes, label]) => (
        <li key={label} className="flex items-center gap-1.5">
          <span className={`h-2.5 w-2.5 rounded-full border-2 ${classes}`} aria-hidden />
          {label}
        </li>
      ))}
    </ul>
  );
}
