import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  IconAlertTriangle,
  IconExternalLink,
  IconMoreVertical,
  IconPlus,
  IconRefreshCw,
  IconSearch,
  IconTrash,
  IconYouTube,
} from "../components/Icons";
import {
  useAuthorizeConnection,
  useConnections,
  useCreateConnection,
  useDeleteConnection,
  useRefreshIdentities,
} from "../hooks/api";

interface AccountItem {
  id: string;
  connectionId: number;
  handle: string;
  title: string;
  platform: "YouTube";
  channelId: string | null;
  needsRelink: boolean;
  statusText: string;
}

export function ChannelsPage() {
  const connections = useConnections();
  const createConnection = useCreateConnection();
  const authorize = useAuthorizeConnection();
  const deleteConnection = useDeleteConnection();
  const refreshIdentities = useRefreshIdentities();

  const [activeTab, setActiveTab] = useState<"connected" | "profile">("connected");
  const [searchQuery, setSearchQuery] = useState("");
  const [filterPlatform, setFilterPlatform] = useState<"all" | "youtube">("all");
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const menuRef = useRef<HTMLDivElement | null>(null);

  // Handle OAuth callback status and close 3-dots dropdown
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get("error");
    if (err === "no_channel") {
      setConnectError(
        "No YouTube channel was found for that Google account. Please ensure the Google account has a YouTube channel created, or choose an account with a channel."
      );
    } else if (err === "cancelled") {
      setConnectError("Channel authorization was cancelled.");
    } else if (err) {
      setConnectError(`Channel connection failed: ${err.replace(/_/g, " ")}`);
    } else if (params.get("authorized") === "true") {
      // Clean query parameter from URL
      window.history.replaceState({}, document.title, window.location.pathname);
    }

    const handleOutsideClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuId(null);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, []);

  // Map connections into account items
  const allAccounts: AccountItem[] = useMemo(() => {
    const rawConnections = connections.data ?? [];
    const ytConns = rawConnections.filter((c) => c.provider === "youtube");
    const items: AccountItem[] = [];

    for (const conn of ytConns) {
      const isExpired =
        conn.status === "expired" ||
        (conn.token_expires_at !== null &&
          Date.parse(conn.token_expires_at) < Date.now() &&
          conn.last_token_check_ok === false);
      const isRevoked = conn.status === "revoked" || conn.authorized_data_deleted_at !== null;
      const isDisconnected = conn.status === "disconnected";
      const needsRelink = isExpired || isRevoked || isDisconnected;

      if (conn.channels && conn.channels.length > 0) {
        for (const ch of conn.channels) {
          const rawHandle = ch.handle?.trim();
          const cleanHandle = rawHandle
            ? rawHandle.startsWith("@")
              ? rawHandle
              : `@${rawHandle}`
            : ch.title
              ? ch.title.startsWith("@")
                ? ch.title
                : `@${ch.title.toLowerCase().replace(/\s+/g, "")}`
              : "@youtube";

          items.push({
            id: `conn-${conn.id}-ch-${ch.id}`,
            connectionId: conn.id,
            handle: cleanHandle,
            title: ch.title || "YouTube Channel",
            platform: "YouTube",
            channelId: ch.channel_id,
            needsRelink,
            statusText: isRevoked ? "Revoked" : isExpired ? "Expired" : isDisconnected ? "Disconnected" : "Active",
          });
        }
      }
    }

    return items;
  }, [connections.data]);

  // Filter & Search
  const filteredAccounts = useMemo(() => {
    return allAccounts.filter((item) => {
      if (filterPlatform !== "all" && item.platform.toLowerCase() !== filterPlatform) {
        return false;
      }
      if (searchQuery.trim()) {
        const query = searchQuery.trim().toLowerCase();
        return (
          item.handle.toLowerCase().includes(query) ||
          item.title.toLowerCase().includes(query)
        );
      }
      return true;
    });
  }, [allAccounts, filterPlatform, searchQuery]);

  const handleConnect = async () => {
    setConnecting(true);
    setConnectError(null);
    try {
      const newConn = await createConnection.mutateAsync({
        provider: "youtube",
        label: "YouTube Channel",
      });
      const authRes = await authorize.mutateAsync(newConn.id);
      if (authRes?.authorization_url) {
        window.location.href = authRes.authorization_url;
      }
    } catch (exc) {
      setConnectError(exc instanceof ApiError ? exc.message : "Could not initiate channel connection.");
      setConnecting(false);
    }
  };

  const handleUnlink = async (connectionId: number, handle: string) => {
    setOpenMenuId(null);
    if (!window.confirm(`Unlink ${handle}? This will disconnect the channel from Hexton.`)) {
      return;
    }
    setDeletingId(connectionId);
    try {
      await deleteConnection.mutateAsync({ id: connectionId, force: true });
    } catch (exc) {
      setConnectError(exc instanceof ApiError ? exc.message : "Could not unlink account.");
    } finally {
      setDeletingId(null);
    }
  };

  const handleRelink = async (connectionId: number) => {
    setOpenMenuId(null);
    setConnecting(true);
    setConnectError(null);
    try {
      const authRes = await authorize.mutateAsync(connectionId);
      if (authRes?.authorization_url) {
        window.location.href = authRes.authorization_url;
      }
    } catch (exc) {
      setConnectError(exc instanceof ApiError ? exc.message : "Could not re-link connection.");
      setConnecting(false);
    }
  };

  const handleRefresh = async (connectionId: number) => {
    setOpenMenuId(null);
    try {
      await refreshIdentities.mutateAsync(connectionId);
    } catch (exc) {
      setConnectError(exc instanceof ApiError ? exc.message : "Could not refresh account.");
    }
  };

  return (
    <div className="flex flex-col gap-6 max-w-5xl mx-auto w-full px-2 sm:px-4 py-2">
      {/* Top Header matching Reference Image */}
      <div className="flex flex-col gap-4">
        <h1 className="text-xl font-bold tracking-tight text-white">Settings</h1>

        <div className="flex items-center justify-between gap-4 flex-wrap pb-1">
          {/* Tabs: Profile | Connected accounts */}
          <div className="flex items-center gap-1.5 bg-[#141518] p-1 rounded-xl border border-white/[0.06]">
            <button
              type="button"
              className={`px-4 py-1.5 text-xs sm:text-sm font-medium rounded-lg transition-all ${
                activeTab === "profile"
                  ? "bg-[#27272a] text-white shadow-sm"
                  : "text-zinc-400 hover:text-white"
              }`}
              onClick={() => setActiveTab("profile")}
            >
              Profile
            </button>
            <button
              type="button"
              className={`px-4 py-1.5 text-xs sm:text-sm font-medium rounded-lg transition-all ${
                activeTab === "connected"
                  ? "bg-[#27272a] text-white shadow-sm font-semibold"
                  : "text-zinc-400 hover:text-white"
              }`}
              onClick={() => setActiveTab("connected")}
            >
              Connected accounts
            </button>
          </div>

          {/* Action & User Profile Info */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="bg-[#ff5500] hover:bg-[#e04b00] active:scale-[0.98] text-white font-medium text-xs sm:text-sm px-4 py-2 rounded-xl flex items-center gap-2 shadow-lg shadow-orange-950/40 transition-all shrink-0 cursor-pointer disabled:opacity-50"
              disabled={connecting || createConnection.isPending || authorize.isPending}
              onClick={handleConnect}
            >
              <IconPlus size={16} />
              <span>{connecting ? "Opening Google…" : "Connect account"}</span>
            </button>
          </div>
        </div>
      </div>

      {connectError && (
        <div
          role="alert"
          className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-xs text-rose-400 flex items-center justify-between gap-2"
        >
          <div className="flex items-center gap-2">
            <IconAlertTriangle size={15} />
            <span>{connectError}</span>
          </div>
          <button
            type="button"
            className="text-xs text-zinc-400 hover:text-white"
            onClick={() => setConnectError(null)}
          >
            ✕
          </button>
        </div>
      )}

      {activeTab === "profile" ? (
        <div className="rounded-2xl border border-white/[0.08] bg-[#141518] p-6 text-sm text-zinc-300">
          <h2 className="text-base font-semibold text-white mb-2">Profile & Settings</h2>
          <p className="text-xs text-zinc-400 mb-4">
            Manage your local Hexton Scheduler environment and preferences.
          </p>
          <div className="flex items-center gap-3">
            <Link to="/settings" className="btn btn-secondary text-xs">
              Open Full Settings & Diagnostics →
            </Link>
          </div>
        </div>
      ) : (
        <>
          {/* Filter Pills and Search Bar Toolbar matching Reference Image */}
          <div className="flex items-center justify-between gap-3 flex-wrap">
            {/* Filter pills */}
            <div className="flex items-center gap-2 overflow-x-auto py-1">
              <button
                type="button"
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                  filterPlatform === "all"
                    ? "bg-[#27272a] text-white border border-white/10"
                    : "text-zinc-400 hover:text-white hover:bg-white/[0.04]"
                }`}
                onClick={() => setFilterPlatform("all")}
              >
                All {allAccounts.length}
              </button>

              <button
                type="button"
                className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all ${
                  filterPlatform === "youtube"
                    ? "bg-[#27272a] text-white border border-white/10"
                    : "text-zinc-400 hover:text-white hover:bg-white/[0.04]"
                }`}
                onClick={() => setFilterPlatform("youtube")}
              >
                <IconYouTube size={14} className="text-zinc-300" />
                <span>{allAccounts.length}</span>
              </button>
            </div>

            {/* Search Input */}
            <div className="relative w-full sm:w-64">
              <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-zinc-500">
                <IconSearch size={14} />
              </div>
              <input
                type="text"
                placeholder="Search account..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-xl bg-[#141518] border border-white/[0.08] pl-9 pr-3 py-1.5 text-xs sm:text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-white/20 transition-colors"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  className="absolute inset-y-0 right-0 flex items-center pr-2.5 text-zinc-500 hover:text-white text-xs"
                >
                  ✕
                </button>
              )}
            </div>
          </div>

          {/* Accounts List matching Reference Image */}
          {connections.isLoading ? (
            <div className="py-20 text-center">
              <p className="text-xs text-zinc-500 animate-pulse">Loading connected accounts…</p>
            </div>
          ) : filteredAccounts.length === 0 ? (
            <div className="rounded-2xl border border-white/[0.08] bg-[#141518]/60 p-10 text-center flex flex-col items-center justify-center">
              <div className="h-12 w-12 rounded-2xl bg-white/[0.05] border border-white/[0.08] flex items-center justify-center text-zinc-400 mb-3">
                <IconYouTube size={22} />
              </div>
              <p className="text-sm font-medium text-white">
                {searchQuery ? "No accounts found matching your search" : "No accounts connected yet"}
              </p>
              <p className="text-xs text-zinc-400 mt-1 max-w-sm">
                {searchQuery
                  ? "Try searching for a different handle or channel name."
                  : "Connect your YouTube channels to start scheduling uploads automatically."}
              </p>
              {!searchQuery && (
                <button
                  type="button"
                  className="bg-[#ff5500] hover:bg-[#e04b00] text-white text-xs font-semibold px-4 py-2 rounded-xl mt-4 cursor-pointer"
                  onClick={handleConnect}
                  disabled={connecting}
                >
                  {connecting ? "Opening Google…" : "+ Connect account"}
                </button>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-2.5" ref={menuRef}>
              {filteredAccounts.map((account) => {
                const isMenuOpen = openMenuId === account.id;
                const isDeleting = deletingId === account.connectionId;

                return (
                  <div
                    key={account.id}
                    className="group relative flex items-center justify-between gap-3 rounded-2xl border border-white/[0.06] bg-[#141518] hover:bg-[#18191d] px-4 py-3 transition-colors"
                  >
                    {/* Left: Platform Icon + Handle & Platform name */}
                    <div className="flex items-center gap-3.5 min-w-0">
                      <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/[0.05] border border-white/[0.06] text-white">
                        <IconYouTube size={20} className="text-red-500" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-white tracking-tight truncate">
                          {account.handle}
                        </p>
                        <p className="text-xs text-zinc-400 mt-0.5 font-normal">
                          {account.platform}
                          {account.title && account.title !== account.handle.replace(/^@/, "") && (
                            <span className="text-zinc-500 ml-1.5 font-normal">· {account.title}</span>
                          )}
                        </p>
                      </div>
                    </div>

                    {/* Right: Status Pill & 3-Dots Action Menu */}
                    <div className="flex items-center gap-2.5 shrink-0 relative">
                      {account.needsRelink && (
                        <div className="flex items-center gap-1.5 rounded-full border border-amber-500/20 bg-amber-500/10 px-2.5 py-0.5 text-xs font-medium text-amber-400">
                          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                          <span>• Re-link needed</span>
                        </div>
                      )}

                      {/* Three-dots menu button */}
                      <button
                        type="button"
                        className={`p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-white/[0.06] transition-colors cursor-pointer ${
                          isMenuOpen ? "bg-white/[0.08] text-white" : ""
                        }`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenMenuId(isMenuOpen ? null : account.id);
                        }}
                        aria-label="Account actions"
                      >
                        <IconMoreVertical size={16} />
                      </button>

                      {/* Dropdown Popover matching Reference Image */}
                      {isMenuOpen && (
                        <div
                          className="absolute right-0 top-full mt-1.5 z-30 min-w-[160px] rounded-xl border border-white/[0.1] bg-[#1a1b1f] p-1.5 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-100"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {account.needsRelink && (
                            <button
                              type="button"
                              className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-amber-400 hover:bg-amber-500/10 rounded-lg text-left transition-colors cursor-pointer"
                              onClick={() => handleRelink(account.connectionId)}
                            >
                              <IconRefreshCw size={13} />
                              <span>Re-link account</span>
                            </button>
                          )}

                          {account.channelId && (
                            <a
                              href={`https://youtube.com/channel/${account.channelId}`}
                              target="_blank"
                              rel="noreferrer noopener"
                              className="w-full flex items-center justify-between px-3 py-2 text-xs text-zinc-300 hover:text-white hover:bg-white/[0.06] rounded-lg transition-colors"
                              onClick={() => setOpenMenuId(null)}
                            >
                              <span>View on YouTube</span>
                              <IconExternalLink size={12} className="text-zinc-500" />
                            </a>
                          )}

                          <button
                            type="button"
                            className="w-full flex items-center gap-2 px-3 py-2 text-xs text-zinc-300 hover:text-white hover:bg-white/[0.06] rounded-lg text-left transition-colors cursor-pointer"
                            onClick={() => handleRefresh(account.connectionId)}
                          >
                            <IconRefreshCw size={13} className="text-zinc-500" />
                            <span>Refresh identity</span>
                          </button>

                          <div className="h-px bg-white/[0.06] my-1" />

                          <button
                            type="button"
                            className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-rose-500 hover:bg-rose-500/10 hover:text-rose-400 rounded-lg text-left transition-colors cursor-pointer"
                            disabled={isDeleting}
                            onClick={() => handleUnlink(account.connectionId, account.handle)}
                          >
                            <IconTrash size={13} />
                            <span>{isDeleting ? "Unlinking…" : "Unlink"}</span>
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}