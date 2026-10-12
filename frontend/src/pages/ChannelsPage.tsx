import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { Connection, ConnectionAction } from "../api/types";
import {
  useAuthorizeConnection,
  useConnections,
  useDiagnostic,
  useDisableConnection,
  useEnableConnection,
  useRefreshIdentities,
  useRevokeConnectionAction,
} from "../hooks/api";
import { formatWhen } from "../lib/state";

/**
 * YouTube channels.
 *
 * This screen exists to answer one question honestly: *which channels can this
 * connection actually publish to?* A channel listed here is one that the authorized
 * connection resolved and that an operator has since confirmed by hand. Nothing else
 * is shown, because a channel name that merely appears in a list reads as "available"
 * — and `DF-18` records that a brand-account channel's publishing path is a separate,
 * unverified claim until a second identity is authorized against it.
 *
 * Disable and revoke are separate buttons that report what they did *and what they
 * did not do* (`COMP-07`). They are never folded into one "disconnect".
 */
export function ChannelsPage() {
  const connections = useConnections();
  const rows = connections.data ?? [];

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">YouTube Channels</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Channels are listed only when the connection that resolved them is still
          authorized and the identity has been confirmed by you. Selecting a channel
          elsewhere in Hexton never widens what the connection can reach.
        </p>
      </header>

      {connections.isLoading ? (
        <p className="text-ink-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <section className="card p-6">
          <h2 className="text-base font-semibold">No connections yet</h2>
          <p className="mt-2 max-w-2xl text-sm text-ink-muted">
            A connection is one Google authorization. The live YouTube connector is not
            enabled in this build — Phase 0 recorded FEAS-01 and FEAS-02 as blocked
            pending a Google Cloud project, an OAuth consent configuration and a test
            channel. Until those gate items pass, nothing here can reach Google.
          </p>
          <Link className="btn mt-4" to="/settings">
            Open diagnostics
          </Link>
        </section>
      ) : (
        rows.map((connection) => <ConnectionCard key={connection.id} connection={connection} />)
      )}
    </div>
  );
}

function ConnectionCard({ connection }: { connection: Connection }) {
  const authorize = useAuthorizeConnection();
  const refresh = useRefreshIdentities();
  const disable = useDisableConnection();
  const enable = useEnableConnection();
  const revoke = useRevokeConnectionAction();
  const diagnostic = useDiagnostic(connection.id);

  const [result, setResult] = useState<ConnectionAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (exc: unknown) => setError(exc instanceof ApiError ? exc.message : "That operation failed.");
  const active =
    connection.status === "active" || connection.status === "enabled" || connection.status === "connected";
  const revoked = connection.status === "revoked" || connection.authorized_data_deleted_at !== null;

  return (
    <section className="card p-5" aria-label={`Connection ${connection.label}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold">{connection.label}</h2>
            <StatusChip status={connection.status} />
          </div>
          <p className="mt-1 text-xs text-ink-faint">
            {connection.provider} · {connection.public_id} ·{" "}
            {connection.channels.length} channel{connection.channels.length === 1 ? "" : "s"} resolved
          </p>
          {connection.granted_at && (
            <p className="mt-0.5 text-xs text-ink-faint">
              Authorized {formatWhen(connection.granted_at)}
              {connection.scopes.length > 0 && ` · ${connection.scopes.length} scope${connection.scopes.length === 1 ? "" : "s"}`}
            </p>
          )}
          {connection.token_expires_at && (
            <p
              className={`mt-0.5 text-xs ${
                Date.parse(connection.token_expires_at) < Date.now() + 7 * 86400_000
                  ? "text-cue-attention"
                  : "text-ink-faint"
              }`}
            >
              Credential expires {formatWhen(connection.token_expires_at)}
              {Date.parse(connection.token_expires_at) < Date.now() && " — reauthorization required"}
            </p>
          )}
          {connection.revoked_at && (
            <p className="mt-0.5 text-xs text-cue-attention">
              Revoke recorded {formatWhen(connection.revoked_at)}
              {connection.authorized_data_deleted_at
                ? ` · Authorized Data deleted ${formatWhen(connection.authorized_data_deleted_at)}`
                : " · Authorized Data deletion due within 7 calendar days"}
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            className="btn"
            disabled={authorize.isPending || revoked}
            title={revoked ? "This connection was revoked. Create a new authorization instead." : undefined}
            onClick={() => authorize.mutate(connection.id, { onSuccess: () => setError(null), onError: fail })}
          >
            Reauthorize
          </button>
          <button
            className="btn"
            disabled={refresh.isPending || revoked}
            onClick={() => refresh.mutate(connection.id, { onSuccess: () => setError(null), onError: fail })}
          >
            Refresh identities
          </button>
          {active ? (
            <button
              className="btn"
              disabled={disable.isPending}
              onClick={() =>
                disable.mutate(connection.id, {
                  onSuccess: (data) => {
                    setResult(data);
                    setError(null);
                  },
                  onError: fail,
                })
              }
            >
              Disable connection
            </button>
          ) : (
            <button
              className="btn"
              disabled={enable.isPending || revoked}
              onClick={() =>
                enable.mutate(connection.id, {
                  onSuccess: (data) => {
                    setResult(data);
                    setError(null);
                  },
                  onError: fail,
                })
              }
            >
              Enable connection
            </button>
          )}
          <button
            className="btn btn-danger"
            disabled={revoke.isPending || revoked}
            onClick={() => {
              if (
                window.confirm(
                  "Revoke this authorization? Hexton deletes the stored credential immediately and must " +
                    "delete the Authorized Data within 7 calendar days. This is not the same as disabling it.",
                )
              ) {
                revoke.mutate(connection.id, {
                  onSuccess: (data) => {
                    setResult(data);
                    setError(null);
                  },
                  onError: fail,
                });
              }
            }}
          >
            Revoke authorization
          </button>
        </div>
      </div>

      {diagnostic.data && (
        <p
          className={`mt-3 rounded-md border px-3 py-2 text-xs ${
            diagnostic.data.code === "ok"
              ? "border-cue-success/40 bg-cue-success/10 text-cue-success"
              : "border-edge bg-canvas text-ink-muted"
          }`}
        >
          <span className="font-medium">{diagnostic.data.code}</span> — {diagnostic.data.remedy}
          {diagnostic.data.detail && <span className="text-ink-faint"> ({diagnostic.data.detail})</span>}
        </p>
      )}

      {error && (
        <p role="alert" className="mt-3 rounded-md border border-cue-warning/50 bg-cue-warning/10 px-3 py-2 text-xs text-cue-warning">
          {error}
        </p>
      )}

      {result && <ActionResult result={result} onDismiss={() => setResult(null)} />}

      <div className="mt-4 border-t border-edge pt-4">
        <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">
          Verified channel identities
        </h3>
        {connection.channels.length === 0 ? (
          <p className="mt-2 text-sm text-ink-faint">
            No channel identity has been resolved from this authorization yet. Use
            “Refresh identities” once the connection is active.
          </p>
        ) : (
          <ul className="mt-3 flex flex-col gap-3">
            {connection.channels.map((channel) => (
              <li key={channel.id} className="rounded-md border border-edge bg-canvas p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="font-medium">{channel.title}</div>
                    <div className="mt-0.5 text-xs text-ink-faint">
                      {channel.handle ? `${channel.handle} · ` : ""}
                      <span className="font-mono">{channel.channel_id}</span>
                    </div>
                    <div className="mt-0.5 text-xs text-ink-faint">
                      Source: {channel.verification_source}
                      {channel.verified_at && ` · resolved ${formatWhen(channel.verified_at)}`}
                    </div>
                  </div>
                  <div className="text-right">
                    {channel.operator_confirmed_at ? (
                      <span className="text-xs text-cue-success">
                        Confirmed by you {formatWhen(channel.operator_confirmed_at)}
                      </span>
                    ) : (
                      <span className="text-xs text-cue-attention">
                        Not confirmed — never used for publishing
                      </span>
                    )}
                  </div>
                </div>

                {channel.is_brand_candidate && (
                  <p className="mt-2 rounded border border-cue-attention/40 bg-cue-attention/10 px-2.5 py-1.5 text-xs text-cue-attention">
                    This identity looks like a brand-account channel. Phase 0 recorded
                    the brand-account publishing path as unverified (DF-18): Hexton will
                    not treat this label as proof the connection can publish to it until
                    a second identity has been authorized and the path is confirmed.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function StatusChip({ status }: { status: string }) {
  const good = status === "active" || status === "enabled" || status === "connected";
  const bad = status === "revoked" || status === "deleted";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${
        good
          ? "border-cue-success/50 bg-cue-success/10 text-cue-success"
          : bad
            ? "border-cue-warning/50 bg-cue-warning/10 text-cue-warning"
            : "border-cue-attention/50 bg-cue-attention/10 text-cue-attention"
      }`}
    >
      {status}
    </span>
  );
}

/**
 * What a disable or a revoke actually did.
 *
 * `did_not` is rendered as prominently as `did`. `COMP-07` exists so an operator can
 * tell the two operations apart from the result, without having to remember which one
 * they pressed.
 */
function ActionResult({ result, onDismiss }: { result: ConnectionAction; onDismiss: () => void }) {
  return (
    <div className="mt-3 rounded-md border border-edge bg-canvas p-3 text-xs">
      <div className="flex items-start justify-between gap-3">
        <p className="font-medium text-ink">Status now: {result.status}</p>
        <button className="btn px-2 py-0.5 text-[11px]" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
      <p className="mt-1 text-ink-muted">{result.explanation}</p>
      <ul className="mt-2 grid gap-1 sm:grid-cols-2">
        <li className="text-cue-success">
          Did: {result.did.length ? result.did.join("; ") : "nothing further"}
        </li>
        <li className="text-cue-attention">
          Did not: {result.did_not.length ? result.did_not.join("; ") : "nothing further"}
        </li>
      </ul>
      {result.authorized_data_retention_days !== null && (
        <p className="mt-1 text-ink-faint">
          Authorized Data retention: {result.authorized_data_retention_days} calendar days.
        </p>
      )}
    </div>
  );
}
