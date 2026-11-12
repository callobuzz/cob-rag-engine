import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { invalidateDocumentSet, type Job } from "@/features/documents/api";
import type { EngineInfo } from "@/lib/api/accounts-types";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { formatBytes } from "@/lib/format/bytes";
import { contentStrings } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";

/**
 * Add content (D4): job polling, the jobs list and the records import call. Uploads go through
 * the queue in upload-queue.ts.
 */
export type RecordsResponse = components["schemas"]["IngestRecordsResponse"];
export type RecordInput = components["schemas"]["RecordInput"];

/** Statuses after which a job no longer changes by itself (pending_review waits for a person). */
export const TERMINAL = new Set(["completed", "failed", "pending_review", "expired", "skipped"]);
export const isTerminal = (status: string | undefined | null) => !!status && TERMINAL.has(status);

export const JOB_POLL_MS = 1500;
export const JOBS_POLL_MS = 3000;
export const JOBS_PAGE_SIZE = 20;

/** GET /ingest/jobs/{id} every 1.5 s until terminal (TanStack pauses it while the tab is hidden). */
export function jobQuery(id: string) {
	return queryOptions({
		queryKey: qk.ingest.job(id),
		queryFn: () => unwrap(api().GET("/api/v1/ingest/jobs/{job_id}", { params: { path: { job_id: id } } })),
		refetchInterval: (q) => (q.state.error || isTerminal(q.state.data?.status) ? false : JOB_POLL_MS),
		staleTime: 0,
	});
}

export function jobsQuery(params: { corpus_id: string; status?: string; offset?: number; limit?: number }) {
	return queryOptions({
		queryKey: qk.ingest.jobs(params),
		queryFn: () => unwrap(api().GET("/api/v1/ingest/jobs", { params: { query: params } })),
		refetchInterval: (q) =>
			q.state.data?.items.some((j) => !isTerminal(j.status)) && !q.state.error ? JOBS_POLL_MS : false,
		placeholderData: (prev) => prev,
	});
}

/**
 * Poll one job; when this view sees it reach `completed`, refresh what it
 * changed (documents list, KB counts, plan usage). `onStatus` sees every status change.
 */
export function useJob(kb: string, id: string | null, onStatus?: (status: string) => void) {
	const qc = useQueryClient();
	const q = useQuery({ ...jobQuery(id ?? ""), enabled: !!id });
	const status = q.data?.status;
	const last = useRef<string | undefined>(undefined);
	useEffect(() => {
		if (!status || status === last.current) return;
		last.current = status;
		onStatus?.(status);
		if (status === "completed") void invalidateDocumentSet(qc, kb);
		if (status === "pending_review") void qc.invalidateQueries({ queryKey: ["ingest", "jobs"] });
	}, [status, kb, qc, onStatus]);
	return q;
}

/** POST /ingest/jobs/{id}/retry: a failed upload runs again from its stored file, as a new job. */
export function useRetryJob() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (jobId: string) =>
			unwrap(api().POST("/api/v1/ingest/jobs/{job_id}/retry", { params: { path: { job_id: jobId } } })),
		onSettled: () => qc.invalidateQueries({ queryKey: qk.ingest.all }),
	});
}

export function recordsImport(kb: string, records: RecordInput[]): Promise<RecordsResponse> {
	return unwrap(api().POST("/api/v1/ingest/records", { body: { corpus_id: kb, records } }));
}

const AUDIO_EXT = new Set([".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"]);

export interface UploadLimits {
	extensions: string[];
	mimeTypes: string[];
	maxBytes: number;
	/** Most text one document may hold; the engine counts it after reading the file. */
	maxTokens: number;
	transcription: boolean;
}

export function uploadLimits(info: EngineInfo): UploadLimits {
	const transcription = info.features.transcription.enabled;
	const l = info.limits;
	const extensions = l.allowed_extensions.filter((e) => transcription || !AUDIO_EXT.has(e.toLowerCase()));
	const mimeTypes = l.allowed_mime_types.filter((m) => transcription || !m.startsWith("audio/"));
	return {
		extensions,
		mimeTypes,
		maxBytes: l.max_upload_bytes,
		maxTokens: l.max_document_tokens,
		transcription,
	};
}

const extOf = (name: string) => (name.includes(".") ? `.${name.split(".").pop()?.toLowerCase()}` : "");

/** Why this file would be refused by POST /ingest, or null (mirrors the server's checks). */
export function checkFile(file: File, limits: UploadLimits): string | null {
	const t = contentStrings.upload;
	const ext = extOf(file.name);
	if (AUDIO_EXT.has(ext) && !limits.transcription) return t.audioOff;
	const typeOk = limits.extensions.includes(ext) || (!!file.type && limits.mimeTypes.includes(file.type));
	if (!typeOk) return ext ? fill(t.wrongType, { ext }) : t.wrongTypeUnknown;
	if (file.size === 0) return t.empty;
	if (file.size > limits.maxBytes)
		return fill(t.tooBig, { size: formatBytes(file.size), max: formatBytes(limits.maxBytes) });
	return null;
}

export type { Job };
