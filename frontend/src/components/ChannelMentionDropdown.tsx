import { useEffect, useRef } from "react";
import type { ChannelSearchResult } from "../api/types";
import { IconCheck, IconRefreshCw } from "./Icons";

interface ChannelMentionDropdownProps {
  channels: ChannelSearchResult[];
  isLoading: boolean;
  selectedIndex: number;
  onSelect: (channel: ChannelSearchResult) => void;
  onClose: () => void;
  query: string;
}

export function ChannelMentionDropdown({
  channels,
  isLoading,
  selectedIndex,
  onSelect,
  query,
}: ChannelMentionDropdownProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll selected item into view
  useEffect(() => {
    if (!containerRef.current) return;
    const items = containerRef.current.querySelectorAll<HTMLLIElement>("[data-channel-item]");
    const active = items[selectedIndex];
    if (active) {
      active.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [selectedIndex]);

  return (
    <div
      ref={containerRef}
      className="absolute z-50 mt-1.5 w-full max-w-[420px] overflow-hidden rounded-2xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)]/98 shadow-2xl backdrop-blur-xl animate-in fade-in-0 zoom-in-95 duration-150"
      style={{
        boxShadow: "0 20px 40px -10px rgba(0,0,0,0.7), 0 0 1px 1px rgba(255,255,255,0.06)",
      }}
      role="listbox"
      aria-label="Channel mention suggestions"
    >
      <div className="flex items-center justify-between border-b border-[var(--hs-border)]/60 px-3 py-2 bg-[var(--hs-surface-sunken)]/50">
        <span className="text-[11px] font-semibold tracking-wide uppercase text-[var(--hs-text-muted)]">
          YouTube Channels for @{query}
        </span>
        {isLoading && <IconRefreshCw size={11} className="animate-spin text-[var(--hs-violet-bright)]" />}
      </div>

      <ul className="max-h-64 overflow-y-auto py-1">
        {channels.map((ch, idx) => {
          const isSelected = idx === selectedIndex;
          const initial = ch.title.charAt(0).toUpperCase() || ch.handle.charAt(0).toUpperCase() || "Y";

          return (
            <li
              key={ch.channel_id || ch.handle}
              data-channel-item
              role="option"
              aria-selected={isSelected}
              className={`flex items-center gap-3 px-3.5 py-2.5 cursor-pointer transition-colors ${
                isSelected
                  ? "bg-[var(--hs-violet-soft)] text-white"
                  : "text-[var(--hs-text)] hover:bg-[var(--hs-surface)]/60"
              }`}
              onMouseDown={(e) => {
                // Prevent input blur before selection
                e.preventDefault();
                onSelect(ch);
              }}
            >
              {/* Channel Avatar */}
              <div className="relative h-9 w-9 shrink-0 overflow-hidden rounded-full border border-white/10 bg-gradient-to-br from-violet-600 to-indigo-800 shadow-inner flex items-center justify-center">
                {ch.thumbnail_url ? (
                  <img
                    src={ch.thumbnail_url}
                    alt={ch.title}
                    className="h-full w-full object-cover"
                    onError={(e) => {
                      // Fallback to initial on image error
                      (e.currentTarget as HTMLElement).style.display = "none";
                    }}
                  />
                ) : (
                  <span className="text-xs font-bold text-white select-none">{initial}</span>
                )}
              </div>

              {/* Channel Info */}
              <div className="flex flex-col min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-xs font-semibold text-white">{ch.title}</span>
                  {ch.verified && (
                    <span
                      title="Verified Channel"
                      className="grid h-3.5 w-3.5 place-items-center rounded-full bg-[var(--hs-border)] text-white shrink-0"
                    >
                      <IconCheck size={9} />
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5 text-[11px] text-[var(--hs-text-muted)] truncate">
                  <span className="text-[var(--hs-text-secondary)] font-medium">@{ch.handle}</span>
                  {ch.subscriber_count && (
                    <>
                      <span className="text-[var(--hs-border-strong)]">•</span>
                      <span>{ch.subscriber_count}</span>
                    </>
                  )}
                </div>
              </div>
            </li>
          );
        })}

        {channels.length === 0 && isLoading && (
          <li className="flex items-center justify-center gap-2 px-4 py-6 text-center text-xs text-[var(--hs-text-muted)]">
            <IconRefreshCw size={13} className="animate-spin text-[var(--hs-violet-bright)]" />
            <span>Searching YouTube channels…</span>
          </li>
        )}

        {channels.length === 0 && !isLoading && (
          <li
            data-channel-item
            role="option"
            className="flex items-center gap-3 px-3.5 py-3 cursor-pointer text-[var(--hs-text)] hover:bg-[var(--hs-surface)]/60 transition-colors"
            onMouseDown={(e) => {
              e.preventDefault();
              const clean = query.replace(/^@+/, "").trim();
              if (clean) {
                onSelect({
                  channel_id: `custom_${clean}`,
                  title: `@${clean}`,
                  handle: clean,
                  thumbnail_url: "",
                  subscriber_count: "Direct handle",
                  verified: false,
                });
              }
            }}
          >
            <div className="relative h-8 w-8 shrink-0 overflow-hidden rounded-full border border-white/10 bg-gradient-to-br from-violet-600 to-indigo-800 shadow-inner flex items-center justify-center font-bold text-xs text-white">
              @
            </div>
            <div className="flex flex-col min-w-0 flex-1">
              <span className="text-xs font-semibold text-white">Mention @{query.replace(/^@+/, "")}</span>
              <span className="text-[11px] text-[var(--hs-text-muted)]">Press Enter to insert handle directly</span>
            </div>
          </li>
        )}
      </ul>
    </div>
  );
}
