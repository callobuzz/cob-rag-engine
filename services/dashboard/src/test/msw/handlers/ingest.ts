import { HttpResponse, http } from "msw";
import type { Job } from "@/features/documents/api";
import { BASE } from "../data";
import { DOC_HOME, DOC_PRICE, PATIENT } from "./documents";

/**
 * Ingest jobs (D4). Each job follows a script of statuses; every GET of the job moves it one
 * step on, so a test sees queued → parsing → … → completed. `ingestState.script` sets the script
 * for the next upload (e.g. ["queued", "pending_review"]). Reset in setup.ts.
 */
export const REVIEW_JOB = "10b00001-0000-4000-8000-000000000001";

interface MockJob {
	job: Job;
	script: string[];
	step: number;
}

export const ingestState = {
	jobs: new Map<string, MockJob>(),
	script: ["queued", "parsing", "chunking", "embedding", "storing", "completed"] as string[],
	/** Multipart fields of each POST /ingest. */
	uploads: [] as Record<string, string>[],
	/** Bodies of each POST /ingest/records. */
	records: [] as { corpus_id: string; records: { id: string; text: string }[] }[],
	confirms: [] as { jobId: string; body: unknown }[],
	/** Keys the engine already has (idempotency replay). */
	keys: new Map<string, string>(),
	seq: 0,
};

function reviewJob(): Job {
	return {
		id: REVIEW_JOB,
		corpus_id: PATIENT,
		status: "pending_review",
		file_name: "test-price-list-oct (1).pdf",
		mode: "auto",
		created_at: "2026-09-30T08:00:00Z",
		finished_at: null,
		expires_at: "2026-10-07T08:00:00Z",
		processed_documents: 0,
		total_documents: 1,
		errors: null,
		records: null,
		target_document_id: null,
		retried: false,
		similar_documents: [
			{ document_id: DOC_PRICE, file_name: "test-price-list-oct.pdf", similarity: 0.94, match_count: 38 },
			{ document_id: DOC_HOME, file_name: "home-collection.pdf", similarity: 0.61, match_count: 4 },
		],
	};
}

export function resetIngestState(): void {
	ingestState.jobs = new Map([[REVIEW_JOB, { job: reviewJob(), script: ["pending_review"], step: 0 }]]);
	ingestState.script = ["queued", "parsing", "chunking", "embedding", "storing", "completed"];
	ingestState.uploads = [];
	ingestState.records = [];
	ingestState.confirms = [];
	ingestState.keys = new Map();
	ingestState.seq = 0;
}
resetIngestState();

function newJob(partial: Partial<Job>, script: string[]): Job {
	ingestState.seq++;
	const id = `10b${String(ingestState.seq).padStart(5, "0")}-0000-4000-8000-000000000000`;
	const job: Job = {
		id,
		corpus_id: PATIENT,
		status: script[0] ?? "queued",
		file_name: null,
		mode: "auto",
		created_at: "2026-09-30T09:00:00Z",
		finished_at: null,
		expires_at: null,
		processed_documents: 0,
		total_documents: 1,
		errors: null,
		records: null,
		target_document_id: null,
		retried: false,
		similar_documents: null,
		...partial,
	};
	ingestState.jobs.set(id, { job, script, step: 0 });
	return job;
}

/** Advance one step and return the job as the engine would now report it. */
function advance(m: MockJob): Job {
	const status = m.script[Math.min(m.step, m.script.length - 1)] ?? "queued";
	m.step++;
	m.job.status = status;
	if (status === "completed") {
		m.job.processed_documents = m.job.total_documents ?? 1;
		m.job.finished_at = "2026-09-30T09:01:00Z";
	}
	if (status === "failed") {
		m.job.errors = { error: "The PDF has no readable text", failed_at_step: "parsing" };
		m.job.finished_at = "2026-09-30T09:01:00Z";
	}
	if (status === "pending_review") {
		m.job.expires_at = "2026-10-07T09:00:00Z";
		m.job.similar_documents = [
			{ document_id: DOC_PRICE, file_name: "test-price-list-oct.pdf", similarity: 0.97, match_count: 41 },
		];
	}
	return { ...m.job };
}

export const ingestHandlers = [
	http.post(`${BASE}/api/v1/ingest`, async ({ request }) => {
		const form = await request.formData();
		const fields: Record<string, string> = {};
		for (const [k, v] of form.entries()) fields[k] = typeof v === "string" ? v : `file:${(v as File).name}`;
		ingestState.uploads.push(fields);
		const key = fields.idempotency_key ?? "";
		const known = key ? ingestState.keys.get(key) : undefined;
		if (known) {
			const m = ingestState.jobs.get(known);
			return HttpResponse.json(
				{
					job_id: known,
					status: m?.job.status ?? "queued",
					message: "Already accepted: this is the job the idempotency_key first created",
				},
				{ status: 202 },
			);
		}
		const job = newJob({ file_name: (fields.file ?? "").replace(/^file:/, "") || null, mode: fields.mode ?? "auto" }, [
			...ingestState.script,
		]);
		if (key) ingestState.keys.set(key, job.id);
		return HttpResponse.json(
			{ job_id: job.id, status: "queued", message: "Document accepted for processing" },
			{ status: 202 },
		);
	}),
	http.post(`${BASE}/api/v1/ingest/records`, async ({ request }) => {
		const body = (await request.json()) as { corpus_id: string; records: { id: string; text: string }[] };
		ingestState.records.push(body);
		const job = newJob(
			{
				mode: "records",
				total_documents: body.records.length,
				records: { created: body.records.length, updated: 0, unchanged: 0 },
			},
			["queued", "completed"],
		);
		return HttpResponse.json({ job_id: job.id, status: "queued", records: body.records.length }, { status: 202 });
	}),
	http.post(`${BASE}/api/v1/ingest/:jobId/confirm`, async ({ params, request }) => {
		const body = (await request.json()) as { action: string; target_document_id?: string | null };
		ingestState.confirms.push({ jobId: String(params.jobId), body });
		const m = ingestState.jobs.get(String(params.jobId));
		if (!m) return HttpResponse.json({ detail: "Job not found" }, { status: 404 });
		if (body.action === "skip" && m.job.status === "skipped")
			return HttpResponse.json({ job_id: m.job.id, status: "skipped", message: "Skipped" });
		if (m.job.status !== "pending_review")
			return HttpResponse.json(
				{ detail: `Job is not pending review (current status: ${m.job.status})` },
				{ status: 409 },
			);
		if (body.action === "skip") {
			m.job.status = "skipped";
			m.script = ["skipped"];
			m.step = 0;
			return HttpResponse.json({ job_id: m.job.id, status: "skipped", message: "Skipped" });
		}
		m.job.mode = body.action === "replace" ? "replace" : "new";
		m.job.status = "queued";
		m.script = ["queued", "parsing", "completed"];
		m.step = 0;
		return HttpResponse.json({
			job_id: m.job.id,
			status: "queued",
			message: `Confirmed: proceeding as '${m.job.mode}'`,
		});
	}),
	http.get(`${BASE}/api/v1/ingest/jobs/:jobId`, ({ params }) => {
		const m = ingestState.jobs.get(String(params.jobId));
		if (!m) return HttpResponse.json({ detail: "Job not found" }, { status: 404 });
		return HttpResponse.json(advance(m));
	}),
	http.get(`${BASE}/api/v1/ingest/jobs`, ({ request }) => {
		const u = new URL(request.url);
		const status = u.searchParams.get("status");
		const corpus = u.searchParams.get("corpus_id");
		const offset = Number(u.searchParams.get("offset") ?? 0);
		const limit = Number(u.searchParams.get("limit") ?? 20);
		const all = [...ingestState.jobs.values()]
			.map((m) => m.job)
			.filter((j) => (!status || j.status === status) && (!corpus || j.corpus_id === corpus));
		const items = all.slice(offset, offset + limit);
		return HttpResponse.json({ items, count: items.length, total: all.length, offset, limit });
	}),
];
