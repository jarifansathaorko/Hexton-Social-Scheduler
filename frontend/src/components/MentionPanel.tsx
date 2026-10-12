import { useEffect, useState } from "react";
import type { Job, Mention } from "../api/types";
import { MIN_ATTESTATION_CHARS } from "../hooks/api";
import type { useJobDetailActions } from "../hooks/jobActions";
import { formatWhen } from "../lib/state";

type Actions = ReturnType<typeof useJobDetailActions>;

/**
 * The creator-mention workflow panel (`04-feature-prd.md` §10 FEAT-008, `DF-19`).
 *
 * `DF-19` is the constraint the whole panel is shaped by: the `videos` resource has no
 * mention entity, so no API call creates or reads back a mention. That means the app's
 * job is to *help a human* and to keep an honest ledger — and two consequences follow,
 * both of which this panel renders rather than hides:
 *
 * 1. **"Verified" means a person said so.** `attestation_only` is shown as text
 *    wherever the state is shown. A UI that rendered a bare "Verified" would let an
 *    operator reasonably believe a check had been performed that was not.
 * 2. **Text is never evidence.** An `@handle` in the description moves the state
 *    towards "manual required", never towards "verified", so the panel tells the
 *    operator to use Studio's mention picker instead of trusting the caption.
 *
 * The confirm control takes a sentence, not a checkbox. That sentence is stored
 * verbatim and is the only evidence that will ever exist, so the operator is asked to
 * write what they did rather than to tick something they will not remember.
 *
 * There is no browser automation, no cookie extraction, and no private endpoint here
 * or anywhere in this workflow. The only actor is the operator, in YouTube Studio.
 */
export function MentionPanel({ job, actions }: { job: Job; actions: Actions }) {
  const mention = actions.mention.data;
  const [attestation, setAttestation] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  // Reset the draft when the job changes. Without this the panel would carry a
  // sentence typed for the previous job, which is exactly the kind of attestation
  // that is worse than none at all.
  useEffect(() => {
    setAttestation("");
    setLocalError(null);
  }, [job.id]);

  if (!job.mention_requested) return null;

  if (actions.mention.isLoading) {
    return (
      <section className="card p-4 text-sm text-ink-muted" aria-busy="true">
        Loading the mention workflow…
      </section>
    );
  }

  const view: Mention | undefined = mention ?? fallbackView(job);
  const confirmed = view.state === "verified";
  const pending = actions.confirmMention.isPending;

  function submit() {
    const words = attestation.trim();
    if (words.length < MIN_ATTESTATION_CHARS) {
      setLocalError(
        `Write what you did in YouTube Studio, in at least ${MIN_ATTESTATION_CHARS} ` +
          "characters. This is the only evidence Hexton will ever have that the " +
          "mention exists, so it is recorded verbatim rather than as a checkbox.",
      );
      return;
    }
    setLocalError(null);
    actions.confirmMention.mutate(words);
  }

  return (
    <section className="rounded-md border border-cue-attention/40 bg-cue-attention/5 p-4" aria-label="Creator mention">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-cue-attention">Creator mention</h3>
        <span className="text-xs text-ink-muted">{view.label}</span>
      </header>

      {view.intended_creator && (
        <p className="mt-2 text-xs text-ink-muted">
          Intended creator: <span className="font-medium text-ink">@{view.intended_creator}</span>
        </p>
      )}

      {view.steps.length > 0 && (
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-xs text-ink-muted">
          {view.steps.map((step) => (
            <li key={step.index}>{step.instruction}</li>
          ))}
        </ol>
      )}

      <p className="mt-3 text-xs text-ink-muted">{view.reason}</p>

      {/* The qualifier is rendered, never collapsed: an operator reading "Verified"
          without it would reasonably assume an API check nobody performed. */}
      {confirmed && (
        <p className="mt-3 rounded-sm bg-canvas/60 px-2 py-1.5 text-xs text-ink-muted">
          Confirmed by your attestation{view.confirmed_at ? ` on ${formatWhen(view.confirmed_at)}` : ""}.
          Hexton did not verify this through the API and cannot re-verify it later.
        </p>
      )}

      {view.rewrite_warning && (
        <p className="mt-3 rounded-sm border border-cue-attention/40 px-2 py-1.5 text-xs text-cue-attention">
          {view.rewrite_warning}
        </p>
      )}

      <div className="mt-3 flex flex-col gap-2">
        <label className="text-xs font-medium text-ink" htmlFor={`attestation-${job.id}`}>
          What did you do in YouTube Studio?
        </label>
        <textarea
          id={`attestation-${job.id}`}
          className="input min-h-16 text-xs"
          placeholder="e.g. I opened the video in Studio and picked @alice from the mention picker in the description."
          value={attestation}
          disabled={pending}
          onChange={(event) => {
            setAttestation(event.target.value);
            setLocalError(null);
          }}
        />
        <p className="text-[11px] text-ink-faint">
          Stored verbatim. It is the only record that this mention exists, because the API
          cannot read a mention back.
        </p>
      </div>

      {(localError || actions.confirmMention.error) && (
        <p className="mt-2 text-xs text-cue-attention" role="alert">
          {localError ?? messageOf(actions.confirmMention.error)}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn" disabled={pending} onClick={submit}>
          {pending ? "Recording…" : "Confirm I added it in Studio"}
        </button>
        {confirmed && (
          <button
            className="btn"
            disabled={actions.clearMention.isPending}
            onClick={() => actions.clearMention.mutate()}
          >
            Withdraw confirmation
          </button>
        )}
        <button
          className="btn"
          disabled={actions.reportMentionRemoved.isPending}
          onClick={() => actions.reportMentionRemoved.mutate()}
        >
          Report it as removed
        </button>
      </div>

      {view.prohibitions.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-edge pt-3 text-[11px] text-ink-faint">
          {view.prohibitions.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The panel before the workflow query resolves.
 *
 * Deliberately minimal and deliberately *not* optimistic about the state: showing a
 * guessed label for a claim this consequential would be worse than showing nothing.
 */
function fallbackView(job: Job): Mention {
  return {
    job_id: job.id,
    state: job.mention_state,
    label: "",
    requested: true,
    handles: job.mention_handles,
    intended_creator: job.mention_handles[0] ?? "",
    attestation_only: false,
    confirmed_at: null,
    reason: job.mention_reason ?? "",
    steps: [],
    prohibitions: [],
    rewrite_warning: null,
  };
}

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "That did not go through. Nothing was recorded.";
}
