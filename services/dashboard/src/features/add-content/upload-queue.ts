import { useSyncExternalStore } from "react";
import { type ApiError, toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { upload } from "@/lib/api/upload";

/**
 * The upload queue (D4 decisions): every file keeps one `idempotency_key` for its whole life, so
 * "Retry" after a network error cannot create a second job; at most 3 uploads run at a time and
 * the rest wait. A module-level store, so uploads keep going while the person looks at another
 * page; rows are per knowledge base.
 */
export type IngestResponse = components["schemas"]["IngestResponse"];
export type IngestMode = "auto" | "new" | "replace" | "version";
export const INGEST_MODES: IngestMode[] = ["auto", "new", "replace", "version"];
export const PARALLEL_UPLOADS = 3;

export interface UploadOptions {
	mode: IngestMode;
	target: { id: string; name: string } | null;
	/** Raw JSON text (object of strings) or "". */
	metadata: string;
	autoConfirm: boolean;
}

export type UploadPhase = "rejected" | "ready" | "waiting" | "uploading" | "sent" | "error";

export interface UploadItem {
	id: string;
	kb: string;
	file: File;
	idempotencyKey: string;
	options: UploadOptions;
	phase: UploadPhase;
	/** 0..1 while uploading. */
	progress: number;
	jobId: string | null;
	/** Last job status the row saw (for "Clear finished"). */
	jobStatus: string | null;
	/** Why a file was refused before upload. */
	reason: string | null;
	error: ApiError | null;
	/** The engine answered with the job this key already created. */
	alreadyReceived: boolean;
}

let items: UploadItem[] = [];
const listeners = new Set<() => void>();
let seq = 0;

function emit() {
	for (const l of listeners) l();
}
function update(id: string, patch: Partial<UploadItem>) {
	items = items.map((it) => (it.id === id ? { ...it, ...patch } : it));
	emit();
}

export const DEFAULT_OPTIONS: UploadOptions = { mode: "auto", target: null, metadata: "", autoConfirm: false };

function newKey(): string {
	if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
	seq++;
	return `upl-${Date.now().toString(36)}-${seq}-${Math.random().toString(36).slice(2)}`;
}

export const uploadQueue = {
	/** Add files; `reasons[i]` (when set) refuses that file before upload. */
	add(kb: string, files: File[], reasons: (string | null)[]): void {
		const added = files.map((file, i): UploadItem => {
			seq++;
			return {
				id: `u${seq}`,
				kb,
				file,
				idempotencyKey: newKey(),
				options: { ...DEFAULT_OPTIONS },
				phase: reasons[i] ? "rejected" : "ready",
				progress: 0,
				jobId: null,
				jobStatus: null,
				reason: reasons[i] ?? null,
				error: null,
				alreadyReceived: false,
			};
		});
		items = [...items, ...added];
		emit();
	},
	setOptions(id: string, options: Partial<UploadOptions>): void {
		const it = items.find((x) => x.id === id);
		if (!it || (it.phase !== "ready" && it.phase !== "waiting")) return;
		update(id, { options: { ...it.options, ...options } });
	},
	/** Queue every ready row of this knowledge base. */
	start(kb: string, ids?: string[]): void {
		items = items.map((it) =>
			it.kb === kb && it.phase === "ready" && (!ids || ids.includes(it.id)) ? { ...it, phase: "waiting" } : it,
		);
		emit();
		pump();
	},
	/** Same idempotency key: a retry can never create a second job. */
	retry(id: string): void {
		update(id, { phase: "waiting", error: null, progress: 0, jobId: null, jobStatus: null });
		pump();
	},
	remove(id: string): void {
		const it = items.find((x) => x.id === id);
		if (!it || it.phase === "uploading") return;
		items = items.filter((x) => x.id !== id);
		emit();
	},
	setJobStatus(id: string, status: string): void {
		const it = items.find((x) => x.id === id);
		if (it && it.jobStatus !== status) update(id, { jobStatus: status });
	},
	/** Remove refused rows and rows whose job finished (done, failed, expired). */
	clearFinished(kb: string): void {
		items = items.filter(
			(it) => it.kb !== kb || !(it.phase === "rejected" || (it.phase === "sent" && FINISHED.has(it.jobStatus ?? ""))),
		);
		emit();
	},
	/** Tests only. */
	reset(): void {
		items = [];
		emit();
	},
};

const FINISHED = new Set(["completed", "failed", "expired", "skipped"]);

export function isFinishedRow(it: UploadItem): boolean {
	return it.phase === "rejected" || (it.phase === "sent" && FINISHED.has(it.jobStatus ?? ""));
}

export function validateMetadata(text: string): Record<string, string> | null | "invalid" {
	const trimmed = text.trim();
	if (!trimmed) return null;
	try {
		const v: unknown = JSON.parse(trimmed);
		if (!v || typeof v !== "object" || Array.isArray(v)) return "invalid";
		const out: Record<string, string> = {};
		for (const [k, val] of Object.entries(v)) {
			if (typeof val !== "string") return "invalid";
			out[k] = val;
		}
		return out;
	} catch {
		return "invalid";
	}
}

/** Row problems that block starting it (target missing, bad metadata). */
export function optionProblems(o: UploadOptions): { target?: true; metadata?: true } {
	const p: { target?: true; metadata?: true } = {};
	if ((o.mode === "replace" || o.mode === "version") && !o.target) p.target = true;
	if (validateMetadata(o.metadata) === "invalid") p.metadata = true;
	return p;
}

function pump() {
	let running = items.filter((it) => it.phase === "uploading").length;
	for (const it of items) {
		if (running >= PARALLEL_UPLOADS) break;
		if (it.phase !== "waiting") continue;
		running++;
		void send(it);
	}
}

async function send(it: UploadItem) {
	update(it.id, { phase: "uploading", progress: 0, error: null });
	const o = it.options;
	const meta = validateMetadata(o.metadata);
	try {
		const res = await upload<IngestResponse>({
			path: "/api/v1/ingest",
			buildForm: () => {
				const f = new FormData();
				f.append("corpus_id", it.kb);
				f.append("idempotency_key", it.idempotencyKey);
				f.append("mode", o.mode);
				if ((o.mode === "replace" || o.mode === "version") && o.target) f.append("target_document_id", o.target.id);
				f.append("auto_confirm", o.autoConfirm ? "true" : "false");
				if (meta && meta !== "invalid") f.append("metadata", JSON.stringify(meta));
				f.append("file", it.file, it.file.name);
				return f;
			},
			onProgress: (p) => update(it.id, { progress: p }),
		});
		update(it.id, {
			phase: "sent",
			progress: 1,
			jobId: res.job_id,
			jobStatus: res.status,
			alreadyReceived: /^already accepted/i.test(res.message ?? ""),
		});
	} catch (e) {
		update(it.id, { phase: "error", error: toApiError(e) });
	} finally {
		pump();
	}
}

export function useUploadItems(kb: string): UploadItem[] {
	const all = useSyncExternalStore(
		(l) => {
			listeners.add(l);
			return () => listeners.delete(l);
		},
		() => items,
		() => items,
	);
	return all.filter((it) => it.kb === kb);
}
