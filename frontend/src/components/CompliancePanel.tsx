import { useState } from "react";
import { ApiError } from "../api/client";
import type { Connection, ConnectionAction } from "../api/types";
import {
  useAcceptPolicy,
  useDisableConnection,
  useEnableConnection,
  usePolicyDetail,
  usePolicyStatus,
  useRevokeConnectionAction,
} from "../hooks/api";
import { formatWhen } from "../lib/state";

/**
 * The privacy-policy gate and the disable-versus-revoke controls (`COMP-01`, `COMP-02`,
 * `COMP-07`).
 *
 * Three things this panel deliberately does:
 *
 * 1. **Shows the gate before it shows the features.** Accepting is how an operator
 *    gives informed consent, so the commitments and the policy links are rendered
 *    *above* the accept button rather than behind it. A gate that hides the thing it
 *    is asking about produces an uninformed "yes".
 * 2. **Keeps disable and revoke apart.** They are two buttons with two confirmations
 *    and two result panels, never one control with a mode. Revoking deletes the
 *    credentials and starts a deletion clock; disabling erases nothing. Conflating
 *    them is how an operator loses a working authorization by accident.
 * 3. **Re-states what each action did and did not do**, straight from the server's
 *    own `did` / `did_not` lists, so the operator does not have to trust the button
 *    label after the fact.
 *
 * A revised policy version re-closes the gate server-side, so this panel re-reads
 * status rather than assuming an earlier acceptance still stands.
 */
export function CompliancePanel({ connections }: { connections: Connection[] }) {
  const status = usePolicyStatus();
  const detail = usePolicyDetail();
  const accept = useAcceptPolicy();
  const [name, setName] = useState("");

  const accepted = status.data?.accepted ?? false;

  return (
    <section className="card p-5" aria-label="Privacy policy">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold">Privacy policy</h2>
        <span className="text-xs text-ink-muted">
          Version {status.data?.version ?? "—"}
          {accepted && status.data?.accepted_at
            ? ` · accepted ${formatWhen(status.data.accepted_at)}`
            : ""}
        </span>
      </header>

      {detail.data && (
        <>
          <p className="mt-3 text-sm text-ink-muted">
            Before Hexton touches your Google account, here is exactly what it will do
            with it:
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-ink-muted">
            {detail.data.commitments.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>

          <p className="mt-3 text-xs font-medium text-ink">Required reading</p>
          <ul className="mt-1 grid gap-1 text-xs sm:grid-cols-2">
            {Object.entries(detail.data.links).map(([title, href]) => (
              <li key={title}>
                <a
                  className="text-link underline-offset-2 hover:underline"
                  href={href}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  {title}
                </a>
              </li>
            ))}
          </ul>

          <p className="mt-3 rounded-sm bg-canvas/60 px-2 py-1.5 text-xs text-ink-muted">
            If you revoke, Hexton deletes your credentials and the data it obtained under
            that authorization within {detail.data.retention.client_revocation_days}{" "}
            calendar days. If Google revokes the grant, or the grant can no longer be
            refreshed, the window is{" "}
            {detail.data.retention.google_revocation_days} calendar days. A backup taken
            before a revocation is deleted on the same deadline as the live data.
          </p>
        </>
      )}

      {accepted ? (
        <p className="mt-3 text-xs text-ok">
          Accepted. API-connected features are available. If the policy changes, Hexton
          will ask again.
        </p>
      ) : (
        <form
          className="mt-4 flex flex-col gap-2 border-t border-edge pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            accept.mutate(name);
          }}
        >
          <label className="text-xs font-medium text-ink" htmlFor="policy-acceptor">
            Type your name to record who accepted this, then continue.
          </label>
          <input
            id="policy-acceptor"
            className="input text-sm"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Your name"
          />
          <button className="btn self-start" type="submit" disabled={accept.isPending}>
            {accept.isPending ? "Recording…" : `Accept policy ${status.data?.version ?? ""}`}
          </button>
          {accept.error && (
            <p className="text-xs text-cue-attention" role="alert">
              {accept.error instanceof ApiError
                ? accept.error.message
                : "That did not go through. Nothing was recorded."}
            </p>
          )}
        </form>
      )}

      {connections.length > 0 && (
        <div className="mt-5 border-t border-edge pt-4">
          <h3 className="text-sm font-semibold">Authorization</h3>
          <p className="mt-1 text-xs text-ink-muted">
            Disabling stops Hexton using a connection and erases nothing. Revoking deletes
            the stored credentials, asks Google to invalidate the grant, and starts the
            deletion clock above. They are not the same action and they do not do the
            same thing.
          </p>
          <ul className="mt-3 flex flex-col gap-3">
            {connections.map((connection) => (
              <ConnectionRow key={connection.id} connection={connection} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function ConnectionRow({ connection }: { connection: Connection }) {
  const disable = useDisableConnection();
  const enable = useEnableConnection();
  const revoke = useRevokeConnectionAction();
  const [confirmingRevoke, setConfirmingRevoke] = useState(false);

  const pending = disable.isPending || enable.isPending || revoke.isPending;
  const last: ConnectionAction | undefined = disable.data ?? enable.data ?? revoke.data;
  const error = disable.error ?? enable.error ?? revoke.error;

  return (
    <li className="rounded-md border border-edge bg-canvas/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {connection.label}{" "}
            <span className="text-xs font-normal text-ink-muted">
              ({connection.provider} · {connection.status})
            </span>
          </p>
          {connection.revoked_at && (
            <p className="text-xs text-cue-attention">
              Revoked {formatWhen(connection.revoked_at)}
            </p>
          )}
          {!connection.revoked_at && connection.granted_at && (
            <p className="text-xs text-ink-faint">
              Authorized {formatWhen(connection.granted_at)}
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          {connection.status === "disabled" ? (
            <button
              className="btn"
              disabled={pending}
              onClick={() => enable.mutate(connection.id)}
            >
              Enable
            </button>
          ) : (
            <button
              className="btn"
              disabled={pending || connection.status === "revoked"}
              onClick={() => disable.mutate(connection.id)}
            >
              Disable
            </button>
          )}

          {confirmingRevoke ? (
            <>
              <button
                className="btn btn-danger"
                disabled={pending}
                onClick={() => {
                  revoke.mutate(connection.id);
                  setConfirmingRevoke(false);
                }}
              >
                Yes, revoke and delete my credentials
              </button>
              <button className="btn" disabled={pending} onClick={() => setConfirmingRevoke(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button
              className="btn"
              disabled={pending}
              onClick={() => setConfirmingRevoke(true)}
            >
              Revoke…
            </button>
          )}
        </div>
      </div>

      {error && (
        <p className="mt-2 text-xs text-cue-attention" role="alert">
          {error instanceof ApiError ? error.message : "That did not go through."}
        </p>
      )}

      {/* The server's own account of what it did, and — just as importantly — what it
          did not. An operator should be able to tell disable from revoke here without
          having remembered which button they pressed. */}
      {last && last.id === connection.id && (
        <div className="mt-3 border-t border-edge pt-2 text-xs">
          <p className="font-medium text-ink">What that did</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-ink-muted">
            {last.did.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="mt-2 font-medium text-ink">What it did not do</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-ink-muted">
            {last.did_not.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {last.authorized_data_retention_days !== null && (
            <p className="mt-2 text-cue-attention">
              Authorized data is due for deletion within {last.authorized_data_retention_days}{" "}
              calendar days. Hexton records that deadline rather than remembering it.
            </p>
          )}
          <p className="mt-2 text-ink-faint">{last.explanation}</p>
        </div>
      )}
    </li>
  );
}
