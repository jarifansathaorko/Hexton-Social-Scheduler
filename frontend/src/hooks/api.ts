/**
 * Server-state hooks.
 *
 * Everything that comes from the backend goes through TanStack Query, so caching,
 * refetching and invalidation are one concern in one place instead of a useEffect in
 * every screen. Mutations declare exactly which lists they invalidate, which is what
 * keeps the content table, the queue and the dashboard from disagreeing after an
 * action.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  AuthorizeResult,
  CancelResult,
  Connection,
  ConnectionAction,
  Dashboard,
  Diagnostic,
  Job,
  JobDetail,
  JobInput,
  JobList,
  Media,
  MediaCheck,
  Mention,
  OutboxRow,
  PolicyDetail,
  PolicyStatus,
  ReportPreview,
  DailySummary,
  Settings,
  SettingsUpdate,
  SheetsConfig,
  SheetsHealth,
  SheetsLimits,
  SheetsOperation,
  SheetsSync,
  ValidateResult,
  WorkerStatus,
} from "../api/types";

/**
 * The minimum length of an operator attestation.
 *
 * Mirrors the backend's own floor rather than replacing it: the server refuses anything
 * shorter with a 409, and checking here too means the operator is told *before* the
 * request rather than after it. If the two ever disagree, the server wins.
 */
export const MIN_ATTESTATION_CHARS = 5;

export const keys = {
  dashboard: ["dashboard"] as const,
  jobs: (q: string, state: string) => ["jobs", q, state] as const,
  job: (id: number) => ["job", id] as const,
  media: ["media"] as const,
  connections: ["connections"] as const,
  settings: ["settings"] as const,
  worker: ["worker"] as const,
};

/** Invalidate every list a job can appear in. */
function useInvalidateJobs() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: ["jobs"] });
    void client.invalidateQueries({ queryKey: ["job"] });
    void client.invalidateQueries({ queryKey: ["dashboard"] });
  };
}

/**
 * Invalidate the connection list.
 *
 * Every connection mutation changes something an operator reads *from that list* —
 * its status, its channel identities, whether it can still be disabled. Without this
 * the list keeps its stale copy, and the button they just pressed keeps offering the
 * action it has already performed. That reads as "nothing happened", which is exactly
 * how an operator ends up clicking again at a moment when a different action is now
 * the available one.
 */
function useInvalidateConnections() {
  const client = useQueryClient();
  return () => void client.invalidateQueries({ queryKey: keys.connections });
}

// ----------------------------------------------------------------------
// Queries
// ----------------------------------------------------------------------

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: () => api.get<Dashboard>("/api/dashboard"),
    refetchInterval: 5000,
  });
}

export function useJobs(query = "", state = "") {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (state) params.set("state", state);
  return useQuery({
    queryKey: keys.jobs(query, state),
    queryFn: () => api.get<JobList>(`/api/jobs?${params.toString()}`),
    refetchInterval: 5000,
  });
}

export function useJob(id: number | null) {
  return useQuery({
    queryKey: keys.job(id ?? 0),
    queryFn: () => api.get<JobDetail>(`/api/jobs/${id}`),
    enabled: id !== null,
    refetchInterval: 3000,
  } as UseQueryOptions<JobDetail>);
}

export function useMedia() {
  return useQuery({ queryKey: keys.media, queryFn: () => api.get<Media[]>("/api/media") });
}

export function useConnections() {
  return useQuery({
    queryKey: keys.connections,
    queryFn: () => api.get<Connection[]>("/api/connections"),
  });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => api.get<Settings>("/api/settings") });
}

export function useWorkerStatus() {
  return useQuery({
    queryKey: keys.worker,
    queryFn: () => api.get<WorkerStatus>("/api/worker/status"),
    refetchInterval: 3000,
  });
}

/**
 * The only writable runtime settings.
 *
 * These five are process-lifetime values the worker reads at startup, so the response
 * is authoritative rather than optimistic — the form re-reads after a save instead of
 * assuming the change took effect, because "saved" and "applied" are different facts
 * here and conflating them is how an operator ends up wondering why the lead time did
 * not move.
 */
export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (patch: SettingsUpdate) => api.put<Settings>("/api/settings", patch),
    onSuccess: (data) => void client.setQueryData(keys.settings, data),
  });
}

/** Which jobs point at a given media row — needed before anything can be deleted. */
export function useMediaJobs(mediaId: number | null) {
  return useQuery({
    queryKey: ["media", "jobs", mediaId ?? 0],
    queryFn: () => api.get<JobList>(`/api/media/${mediaId}/jobs`),
    enabled: mediaId !== null && mediaId > 0,
  });
}

export function useRecheckMedia() {
  const invalidate = useInvalidateJobs();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<MediaCheck>(`/api/media/${id}/recheck`),
    onSuccess: () => {
      void invalidate();
      void client.invalidateQueries({ queryKey: keys.media });
    },
  });
}

/**
 * Deleting media is refused by the backend while any job still points at it, and this
 * hook surfaces that refusal rather than hiding it: the caller must first ask
 * `useMediaJobs`, show the operator what would be affected, and only then attempt the
 * delete.
 */
export function useDeleteMedia() {
  const invalidate = useInvalidateJobs();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del<void>(`/api/media/${id}`),
    onSuccess: () => {
      void invalidate();
      void client.invalidateQueries({ queryKey: keys.media });
    },
  });
}

// ----------------------------------------------------------------------
// Mutations
// ----------------------------------------------------------------------

export function useValidateJob() {
  return useMutation({
    mutationFn: (input: JobInput) => api.post<ValidateResult>("/api/jobs/validate", input),
  });
}

export function useCheckMedia() {
  return useMutation({
    mutationFn: (path: string) => api.post<MediaCheck>("/api/media/check", { path }),
  });
}

export function useAddMedia() {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: (path: string) => api.post<Media>("/api/media", { path }),
    onSuccess: () => void invalidate(),
  });
}

export function useCreateJob() {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: ({ input, queue }: { input: JobInput; queue: boolean }) =>
      api.post<Job>("/api/jobs", { ...input, queue }),
    onSuccess: () => void invalidate(),
  });
}

export function useUpdateJob(id: number) {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: (input: JobInput) => api.put<Job>(`/api/jobs/${id}`, input),
    onSuccess: () => void invalidate(),
  });
}

export function useJobAction(path: (id: number) => string, verb: string) {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body?: unknown }) =>
      api.post<Job | CancelResult>(path(id), body ?? {}),
    onSuccess: () => {
      void invalidate();
      void verb;
    },
  });
}

export function useQueueJob() {
  return useJobAction((id) => `/api/jobs/${id}/queue`, "queue");
}
export function useRetryJob() {
  return useJobAction((id) => `/api/jobs/${id}/retry`, "retry");
}
export function useDuplicateJob() {
  return useJobAction((id) => `/api/jobs/${id}/duplicate`, "duplicate");
}
export function useRescheduleJob() {
  return useJobAction((id) => `/api/jobs/${id}/reschedule`, "reschedule");
}
export function useCancelJob() {
  return useJobAction((id) => `/api/jobs/${id}/cancel`, "cancel");
}
/**
 * The creator-mention workflow (`04-feature-prd.md` §10 FEAT-008, `DF-19`).
 *
 * `useConfirmMention` takes the attestation sentence and refuses to send an empty one,
 * because that sentence is the *only* evidence Hexton will ever have that a mention
 * exists. `POST /api/jobs/{id}/mention/confirm` rejects an empty body with a 409, and
 * this hook rejecting it client-side is the same rule expressed earlier — not a
 * different, softer one.
 */
export function useMention(jobId: number) {
  return useQuery({
    queryKey: ["mention", jobId],
    queryFn: () => api.get<Mention>(`/api/jobs/${jobId}/mention`),
    enabled: Number.isFinite(jobId) && jobId > 0,
  });
}

function useMentionAction(jobId: number) {
  const invalidate = useInvalidateJobs();
  const queryClient = useQueryClient();
  return () => {
    void invalidate();
    void queryClient.invalidateQueries({ queryKey: ["mention", jobId] });
  };
}

export function useConfirmMention(jobId: number) {
  const refresh = useMentionAction(jobId);
  return useMutation({
    mutationFn: (attestation: string) => {
      const words = attestation.trim();
      if (words.length < MIN_ATTESTATION_CHARS) {
        throw new Error(
          `Write what you did in YouTube Studio, in at least ${MIN_ATTESTATION_CHARS} characters. ` +
            "This is the only evidence Hexton will ever have that the mention exists.",
        );
      }
      return api.post<Mention>(`/api/jobs/${jobId}/mention/confirm`, { attestation: words });
    },
    onSuccess: refresh,
  });
}

export function useClearMention(jobId: number) {
  const refresh = useMentionAction(jobId);
  return useMutation({
    mutationFn: () => api.post<Mention>(`/api/jobs/${jobId}/mention/clear`),
    onSuccess: refresh,
  });
}

export function useBeginMention(jobId: number) {
  const refresh = useMentionAction(jobId);
  return useMutation({
    mutationFn: () => api.post<Mention>(`/api/jobs/${jobId}/mention/begin`),
    onSuccess: refresh,
  });
}

export function useReportMentionRemoved(jobId: number) {
  const refresh = useMentionAction(jobId);
  return useMutation({
    mutationFn: () => api.post<Mention>(`/api/jobs/${jobId}/mention/removed`),
    onSuccess: refresh,
  });
}

export function useReconcileJob() {
  return useJobAction((id) => `/api/jobs/${id}/reconcile`, "reconcile");
}
export function useDeleteJob() {
  const invalidate = useInvalidateJobs();
  return useMutation({
    mutationFn: (id: number) => api.del<void>(`/api/jobs/${id}`),
    onSuccess: () => void invalidate(),
  });
}

export function useAuthorizeConnection() {
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (id: number) => api.post<AuthorizeResult>(`/api/connections/${id}/authorize`),
    onSuccess: () => refresh(),
  });
}

export function useAttachTestCredential() {
  const invalidate = useInvalidateJobs();
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (id: number) => api.post<Connection>(`/api/connections/${id}/attach-test-credential`),
    onSuccess: () => {
      void invalidate();
      refresh();
    },
  });
}

export function useRevokeConnection() {
  const invalidate = useInvalidateJobs();
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (id: number) => api.post<Connection>(`/api/connections/${id}/revoke`),
    onSuccess: () => {
      void invalidate();
      refresh();
    },
  });
}

export function useRefreshIdentities() {
  const invalidate = useInvalidateJobs();
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (id: number) => api.post<Connection>(`/api/connections/${id}/refresh-identities`),
    onSuccess: () => {
      void invalidate();
      refresh();
    },
  });
}

export function useConfirmChannel() {
  const invalidate = useInvalidateJobs();
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (identityId: number) =>
      api.post<never>(`/api/connections/channels/${identityId}/confirm`, {
        channel_identity_id: identityId,
      }),
    onSuccess: () => {
      void invalidate();
      refresh();
    },
  });
}

export function useUnconfirmChannel() {
  const invalidate = useInvalidateJobs();
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (identityId: number) =>
      api.post<never>(`/api/connections/channels/${identityId}/unconfirm`, {}),
    onSuccess: () => {
      void invalidate();
      refresh();
    },
  });
}

// ---------------------------------------------------------------------------
// Compliance (COMP-01..COMP-10)
// ---------------------------------------------------------------------------

export const complianceKeys = {
  policy: ["compliance", "policy"] as const,
  detail: ["compliance", "policy", "detail"] as const,
  diagnostic: (id: number) => ["compliance", "diagnostic", id] as const,
};

/** The gate state. Polled slowly: it changes when the operator accepts, not on a timer. */
export function usePolicyStatus() {
  return useQuery({
    queryKey: complianceKeys.policy,
    queryFn: () => api.get<PolicyStatus>("/api/compliance/policy"),
    staleTime: 10_000,
  });
}

export function usePolicyDetail() {
  return useQuery({
    queryKey: complianceKeys.detail,
    queryFn: () => api.get<PolicyDetail>("/api/compliance/policy/detail"),
    // Versioned content that never changes within a process; fetching once is right.
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useAcceptPolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (acceptedBy: string) =>
      api.post<PolicyStatus>("/api/compliance/policy/accept", {
        accepted_by: acceptedBy.trim() || "Local operator",
      }),
    // A revised policy version re-closes the gate server-side, so the client must
    // re-read rather than assume its own copy is still current.
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: complianceKeys.policy }),
  });
}

export function useDiagnostic(connectionId: number | null) {
  return useQuery({
    queryKey: complianceKeys.diagnostic(connectionId ?? 0),
    queryFn: () => api.get<Diagnostic>(`/api/compliance/connections/${connectionId}/diagnostic`),
    enabled: connectionId !== null && connectionId > 0,
  });
}

/**
 * Disable, enable and revoke are three separate mutations, not one with a flag.
 *
 * They are separate because they are separate operations with separate consequences
 * (`COMP-07`), and a single `setConnectionMode(id, "off")` would make it far too easy
 * to revoke when the operator meant to disable — which deletes the credentials and
 * starts the deletion clock. Each refreshes the connection list, because that list is
 * where the operator sees the resulting status.
 */
export function useDisableConnection() {
  const invalidate = useInvalidateJobs();
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (id: number) =>
      api.post<ConnectionAction>(`/api/compliance/connections/${id}/disable`),
    onSuccess: () => {
      void invalidate();
      refresh();
    },
  });
}

export function useEnableConnection() {
  const invalidate = useInvalidateJobs();
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (id: number) =>
      api.post<ConnectionAction>(`/api/compliance/connections/${id}/enable`),
    onSuccess: () => {
      void invalidate();
      refresh();
    },
  });
}

export function useRevokeConnectionAction() {
  const invalidate = useInvalidateJobs();
  const refresh = useInvalidateConnections();
  return useMutation({
    mutationFn: (id: number) =>
      api.post<ConnectionAction>(`/api/compliance/connections/${id}/revoke`),
    onSuccess: () => {
      void invalidate();
      refresh();
    },
  });
}

// ---------------------------------------------------------------------------
// Sheets reporting (FEAT-010, AC-MVP-008)
// ---------------------------------------------------------------------------

export const reportingKeys = {
  config: ["reporting", "config"] as const,
  health: ["reporting", "health"] as const,
  outbox: ["reporting", "outbox"] as const,
  limits: ["reporting", "limits"] as const,
};

export function useSheetsConfig() {
  return useQuery({
    queryKey: reportingKeys.config,
    queryFn: () => api.get<SheetsConfig>("/api/reporting/config"),
  });
}

export function useSheetsHealth() {
  return useQuery({
    queryKey: reportingKeys.health,
    queryFn: () => api.get<SheetsHealth>("/api/reporting/health"),
    // Health is the panel's whole payload. Two seconds matches the job list so a
    // sync started here and a status change elsewhere land in the same refresh.
    staleTime: 2_000,
  });
}

/**
 * Rows Hexton still owes the spreadsheet.
 *
 * `enabled` exists because `GET /api/reporting/outbox` is behind the privacy-policy
 * gate: before acceptance a request would be refused with a 403, and showing "no rows"
 * for what is really "reporting is not switched on yet" would be exactly the kind of
 * quiet conflation this application is supposed to avoid.
 */
export function useSheetsOutbox(enabled = true) {
  return useQuery({
    queryKey: reportingKeys.outbox,
    queryFn: () => api.get<{ rows: OutboxRow[]; total: number }>("/api/reporting/outbox"),
    staleTime: 2_000,
    enabled,
  });
}

export function useSheetsLimits() {
  return useQuery({
    queryKey: reportingKeys.limits,
    queryFn: () => api.get<SheetsLimits>("/api/reporting/limits"),
    // Readable before the policy is accepted, and never changes within a process.
    staleTime: Number.POSITIVE_INFINITY,
  });
}

function useReportingRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["reporting"] });
    void queryClient.invalidateQueries({ queryKey: ["connections"] });
  };
}

export function useSaveSheetsConfig() {
  const refresh = useReportingRefresh();
  return useMutation({
    mutationFn: (config: SheetsConfig) => api.put<SheetsConfig>("/api/reporting/config", config),
    onSuccess: refresh,
  });
}

export function useVerifySheets() {
  const refresh = useReportingRefresh();
  return useMutation({
    mutationFn: () => api.post<SheetsOperation>("/api/reporting/verify"),
    onSuccess: refresh,
  });
}

export function useTestWriteSheets() {
  const refresh = useReportingRefresh();
  return useMutation({
    mutationFn: () => api.post<SheetsOperation>("/api/reporting/test-write"),
    onSuccess: refresh,
  });
}

export function useSyncSheets() {
  const refresh = useReportingRefresh();
  return useMutation({
    mutationFn: () => api.post<SheetsSync>("/api/reporting/sync"),
    onSuccess: refresh,
  });
}

export function useRetryOutboxRecord() {
  const refresh = useReportingRefresh();
  return useMutation({
    mutationFn: (recordId: number) => api.post<SheetsOperation>(`/api/reporting/outbox/${recordId}/retry`),
    onSuccess: refresh,
  });
}

/**
 * The exact row one job would produce, read on demand.
 *
 * Shown before anything is written so an operator can see the claim — in particular
 * whether the row would say "Published" — before a single append reaches the sheet.
 */
export function useReportPreview(jobId: number | null) {
  return useQuery({
    queryKey: ["reporting", "preview", jobId ?? 0],
    queryFn: () => api.get<ReportPreview>(`/api/reporting/preview/${jobId}`),
    enabled: jobId !== null && jobId > 0,
    staleTime: 0,
  });
}

/** The daily completion summary for one report date (`YYYY-MM-DD`). */
export function useDailySummary(reportDate: string | null) {
  return useQuery({
    queryKey: ["reporting", "daily", reportDate ?? ""],
    queryFn: () => api.get<DailySummary>(`/api/reporting/daily-summary/${reportDate}`),
    enabled: reportDate !== null && /^\d{4}-\d{2}-\d{2}$/.test(reportDate),
  });
}

/**
 * Materialise pending report records into the outbox.
 *
 * This only writes to the local outbox. It never touches YouTube and never starts an
 * upload — `enqueue_reports` is a reporting-side operation, and the separation is what
 * makes it safe to expose as a button.
 */
export function useEnqueueReports() {
  const refresh = useReportingRefresh();
  return useMutation({
    mutationFn: () => api.post<SheetsOperation>("/api/reporting/enqueue"),
    onSuccess: refresh,
  });
}
