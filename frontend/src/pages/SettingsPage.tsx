import { ApiError } from "../api/client";
import { CompliancePanel } from "../components/CompliancePanel";
import { SheetsPanel } from "../components/SheetsPanel";
import {
  useAttachTestCredential,
  useAuthorizeConnection,
  useConnections,
  useRefreshIdentities,
  useRevokeConnection,
  useSettings,
  useWorkerStatus,
} from "../hooks/api";
import { formatWhen } from "../lib/state";

/**
 * Settings.
 *
 * Two things live here and both are operational rather than cosmetic: the connection
 * list, which is where an operator sees whether their channel is still authorized, and
 * the runtime facts — quota, credential store, data directory — that decide whether
 * the queue can do anything at all.
 *
 * No token is ever rendered, because none is ever sent. A connection is described by
 * its scopes, its identities and how healthy it is; the material that makes it work
 * stays in Windows Credential Manager.
 */
export function SettingsPage() {
  const settings = useSettings();
  const worker = useWorkerStatus();
  const connections = useConnections();

  if (settings.isLoading) return <p className="text-ink-muted">Loading…</p>;

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Everything here is local to this PC.
        </p>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Fact label="Credential store" value={settings.data?.credential_store_kind ?? "—"} />
        <Fact
          label="Quota remaining today"
          value={`${settings.data?.quota_remaining ?? "—"} / ${settings.data?.quota_daily_units ?? "—"}`}
        />
        <Fact
          label="Quota resets"
          value={`${formatWhen(settings.data?.quota_resets_at ?? null)} (midnight Pacific)`}
        />
        <Fact
          label="Worker"
          value={worker.data?.alive ? `Running (${worker.data.owner ?? "unknown"})` : "Not detected"}
          tone={worker.data?.alive ? "ok" : "warning"}
        />
      </section>

      <section className="card p-5">
        <h2 className="text-base font-semibold">Runtime</h2>
        <dl className="mt-3 grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          <Row label="Data directory" value={settings.data?.data_dir ?? "—"} mono />
          <Row label="Connector" value={settings.data?.connector_kind ?? "—"} />
          <Row
            label="Minimum lead time"
            value={`${settings.data?.min_lead_minutes ?? "—"} minutes (open decision D-4)`}
          />
          <Row
            label="Warning band"
            value={`${settings.data?.warning_lead_minutes ?? "—"} minutes`}
          />
          <Row label="Lease length" value={`${settings.data?.lease_seconds ?? "—"} seconds`} />
          <Row label="Retention" value={`${settings.data?.retention_days ?? "—"} days`} />
        </dl>
        <p className="mt-3 text-xs text-ink-faint">
          Lease length and poll interval are read by the worker at startup, so changing
          them takes effect on the next restart.
        </p>
      </section>

      <CompliancePanel connections={connections.data ?? []} />

      <Connections />

      <SheetsPanel connections={connections.data ?? []} />
    </div>
  );
}

function Connections() {
  const connections = useConnections();
  const authorize = useAuthorizeConnection();
  const attachTest = useAttachTestCredential();
  const refresh = useRefreshIdentities();
  const revoke = useRevokeConnection();

  return (
    <section className="card p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold">Connections</h2>
          <p className="mt-1 text-sm text-ink-muted">
            Each connection is one Google authorization. The channel it publishes to is
            resolved from it and must be confirmed by you before it is used.
          </p>
        </div>
      </div>

      {(connections.data?.length ?? 0) === 0 ? (
        <p className="mt-4 text-sm text-ink-faint">
          No connections yet. The live YouTube connector is not enabled in this build —
          Phase 0 recorded FEAS-01 and FEAS-02 as blocked pending a Google Cloud project
          and a consent configuration.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-4">
          {connections.data?.map((connection) => (
            <li key={connection.id} className="rounded-md border border-edge bg-canvas p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{connection.label}</div>
                  <div className="mt-0.5 text-xs text-ink-faint">
                    {connection.provider} · {connection.status} ·{" "}
                    {connection.channels.length} channel
                    {connection.channels.length === 1 ? "" : "s"}
                  </div>
                  {connection.token_expires_at && (
                    <div className="mt-0.5 text-xs text-ink-faint">
                      Credential expires {formatWhen(connection.token_expires_at)}
                    </div>
                  )}
                  {connection.revoked_at && (
                    <div className="mt-0.5 text-xs text-cue-attention">
                      Revoke recorded {formatWhen(connection.revoked_at)} · Authorized
                      Data deletion due within 7 calendar days
                      {connection.authorized_data_deleted_at
                        ? ` · deleted ${formatWhen(connection.authorized_data_deleted_at)}`
                        : ""}
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    className="btn"
                    disabled={authorize.isPending}
                    onClick={() =>
                      authorize.mutate(connection.id, {
                        onError: (exc) => window.alert(message(exc)),
                      })
                    }
                  >
                    Reconnect
                  </button>
                  <button
                    className="btn"
                    disabled={refresh.isPending}
                    onClick={() =>
                      refresh.mutate(connection.id, {
                        onError: (exc) => window.alert(message(exc)),
                      })
                    }
                  >
                    Refresh identities
                  </button>
                  <button
                    className="btn"
                    disabled={attachTest.isPending}
                    title="Development only: attaches a fake credential so the queue can be exercised."
                    onClick={() =>
                      attachTest.mutate(connection.id, {
                        onError: (exc) => window.alert(message(exc)),
                      })
                    }
                  >
                    Attach test credential
                  </button>
                  <button
                    className="btn btn-danger"
                    disabled={revoke.isPending}
                    onClick={() => {
                      if (
                        window.confirm(
                          "Revoke this connection? Hexton will delete its stored credential and must delete the Authorized Data within 7 calendar days.",
                        )
                      ) {
                        revoke.mutate(connection.id, {
                          onError: (exc) => window.alert(message(exc)),
                        });
                      }
                    }}
                  >
                    Revoke
                  </button>
                </div>
              </div>

              {connection.channels.length > 0 && (
                <ul className="mt-3 flex flex-col gap-2 border-t border-edge pt-3">
                  {connection.channels.map((channel) => (
                    <li key={channel.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                      <div>
                        <span className="font-medium">{channel.title}</span>
                        {channel.handle && (
                          <span className="ml-2 text-xs text-ink-faint">{channel.handle}</span>
                        )}
                        {channel.is_brand_candidate && (
                          <span className="ml-2 rounded border border-cue-attention/40 px-1.5 py-0.5 text-[10px] uppercase text-cue-attention">
                            brand account?
                          </span>
                        )}
                      </div>
                      {channel.operator_confirmed_at ? (
                        <span className="text-xs text-cue-success">
                          Confirmed {formatWhen(channel.operator_confirmed_at)}
                        </span>
                      ) : (
                        <span className="text-xs text-cue-attention">
                          Not confirmed — will not be used for publishing
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function message(exc: unknown): string {
  return exc instanceof ApiError ? exc.message : "That operation failed.";
}

function Fact({ label, value, tone }: { label: string; value: string; tone?: "ok" | "warning" }) {
  return (
    <div className="card px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-ink-faint">{label}</div>
      <div
        className={`mt-1 text-sm font-medium ${
          tone === "warning" ? "text-cue-attention" : tone === "ok" ? "text-cue-success" : "text-ink"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs uppercase tracking-wide text-ink-faint">{label}</dt>
      <dd className={mono ? "break-all font-mono text-xs" : "text-ink"}>{value}</dd>
    </div>
  );
}
