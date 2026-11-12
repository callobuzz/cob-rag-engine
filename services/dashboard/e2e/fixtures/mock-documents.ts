/**
 * Documents and ingestion part of the fake engine (D4): documents with versions and passages,
 * uploads (multipart, idempotency keys), jobs that move through the real stages over time,
 * "looks like an existing document" reviews, records import with created / updated / unchanged
 * counts, restore and merge. It follows the contracts closely enough to exercise the dashboard,
 * including A5's `owner_required` (delete, merge) and A6's `document_limit_reached` on every
 * add-path. Indian demo data only.
 */

import { randomUUID } from "node:crypto";

export const STAGE_MS = 450;
const FILE_STAGES = ["queued", "parsing", "chunking", "enriching", "embedding", "storing", "completed"] as const;

interface Chunk {
	id: string;
	chunk_index: number;
	text: string;
	page: number | null;
	token_count: number;
	is_table: boolean;
	is_code: boolean;
	parent_chunk_id: string | null;
	section: string[] | null;
}
interface Version {
	id: string;
	status: "active" | "inactive";
	parser_version: string | null;
	chunk_schema_version: string | null;
	created_at: string;
	chunks: Chunk[];
	/** Records: the text, to tell "updated" from "unchanged". */
	text?: string;
}
export interface DocRecord {
	id: string;
	corpusId: string;
	file_name: string | null;
	mime_type: string | null;
	size_bytes: number | null;
	ingested_at: string;
	external_id: string | null;
	metadata: Record<string, string> | null;
	source_uri: string | null;
	versions: Version[];
}
interface JobRecord {
	id: string;
	corpusId: string;
	kind: "file" | "records";
	file_name: string | null;
	mime_type: string | null;
	size: number;
	mode: string;
	target: string | null;
	autoConfirm: boolean;
	metadata: Record<string, string> | null;
	key: string | null;
	createdAt: number;
	startedAt: number;
	/** Similar documents when the upload looks like an existing one (auto mode). */
	similar: { document_id: string; similarity: number; match_count: number }[] | null;
	reviewing: boolean;
	applied: boolean;
	expired: boolean;
	skipped: boolean;
	failed: string | null;
	records:
		| { id: string; text: string; title?: string | null; url?: string | null; metadata?: Record<string, string> }[]
		| null;
	counts: Record<string, number> | null;
}

export interface DocState {
	docs: DocRecord[];
	jobs: JobRecord[];
	/** Tenant id → max active documents (A6 `max_documents`; absent = unlimited). */
	limits: Map<string, number>;
	/** Corpus id → tenant id (from the KB state). */
	tenantOf: (corpusId: string) => string | undefined;
	/** Called after the set of documents of a corpus changed (KB counts). */
	onChange: (corpusId: string) => void;
}

/** Random ids; `kind` only documents what the id is for. */
function uid(_kind: string): string {
	return randomUUID();
}
const iso = (ms: number) => new Date(ms).toISOString();

function chunksFor(name: string, lines: string[], section: string[]): Chunk[] {
	const parent = uid("c0000000");
	return lines.map((text, i) => ({
		id: i === 0 ? parent : uid("c0000000"),
		chunk_index: i,
		text,
		page: 1,
		token_count: Math.ceil(text.length / 4),
		is_table: /₹\d/.test(text) && i > 0,
		is_code: false,
		parent_chunk_id: i > 0 ? parent : null,
		section: [name.replace(/\.[a-z]+$/, ""), ...section],
	}));
}

function version(created: number, chunks: Chunk[], active: boolean, text?: string): Version {
	return {
		id: uid("7e500000"),
		status: active ? "active" : "inactive",
		parser_version: text === undefined ? "docling-2.14" : null,
		chunk_schema_version: "2",
		created_at: iso(created),
		chunks,
		text,
	};
}

const PRICE_LINES = [
	"Shree Labs, Pune — test price list, October 2026. All prices include GST.",
	"CBC (complete blood count): ₹350. Thyroid profile (T3, T4, TSH): ₹550.",
	"Lipid profile: ₹600, fasting 10–12 hours. HbA1c: ₹450. Vitamin D (25-OH): ₹1,200.",
	"Reports are ready the same day by 7 pm, on WhatsApp and e-mail.",
];
const HOME_LINES = [
	"Home collection across Pune: Kothrud, Baner, Aundh, Hadapsar and Viman Nagar.",
	"Slots 6:30 am to 11 am, Monday to Saturday. Book on +91 98220 12345 or on WhatsApp.",
	"Free for orders above ₹999; otherwise ₹100 is added. Pay by UPI or cash.",
];

export function createDocState(
	patientHelp: string,
	tenantOf: DocState["tenantOf"],
	onChange: DocState["onChange"],
): DocState {
	const now = Date.now();
	const guide: DocRecord = {
		id: uid("d0c00000"),
		corpusId: patientHelp,
		file_name: "test-prep-guide.pdf",
		mime_type: "application/pdf",
		size_bytes: 412_000,
		ingested_at: iso(now - 3 * 86_400_000),
		external_id: null,
		metadata: { category: "preparation" },
		source_uri: null,
		versions: [
			version(
				now - 20 * 86_400_000,
				chunksFor("test-prep-guide.pdf", ["Before a fasting test, eat dinner by 9 pm."], ["Fasting"]),
				false,
			),
			version(
				now - 3 * 86_400_000,
				chunksFor(
					"test-prep-guide.pdf",
					[
						"Before a fasting test, do not eat for 10–12 hours. Water is fine.",
						"Carry your doctor's prescription and a photo ID. Children under 5 need a parent present.",
					],
					["Fasting"],
				),
				true,
			),
		],
	};
	const home: DocRecord = {
		id: uid("d0c00000"),
		corpusId: patientHelp,
		file_name: "home-collection.pdf",
		mime_type: "application/pdf",
		size_bytes: 91_000,
		ingested_at: iso(now - 9 * 86_400_000),
		external_id: null,
		metadata: null,
		source_uri: "https://shree-labs.in/home-collection",
		versions: [version(now - 9 * 86_400_000, chunksFor("home-collection.pdf", HOME_LINES, ["Home collection"]), true)],
	};
	return { docs: [guide, home], jobs: [], limits: new Map(), tenantOf, onChange };
}

const active = (d: DocRecord) => d.versions.find((v) => v.status === "active") ?? null;

function summary(d: DocRecord) {
	return {
		id: d.id,
		file_name: d.file_name,
		mime_type: d.mime_type,
		size_bytes: d.size_bytes,
		ingested_at: d.ingested_at,
		external_id: d.external_id,
		metadata: d.metadata,
		active_version_id: active(d)?.id ?? null,
		version_count: d.versions.length,
	};
}
const versionOut = (v: Version) => ({
	id: v.id,
	status: v.status,
	parser_version: v.parser_version,
	chunk_schema_version: v.chunk_schema_version,
	created_at: v.created_at,
	chunk_count: v.chunks.length,
});

/** Documents of the tenant that count against the cap (those with an active version). */
function activeCount(s: DocState, tenantId: string): number {
	return s.docs.filter((d) => s.tenantOf(d.corpusId) === tenantId && active(d)).length;
}
function capError(s: DocState, corpusId: string, adding: number) {
	if (adding <= 0) return null;
	const tenant = s.tenantOf(corpusId);
	const limit = tenant ? s.limits.get(tenant) : undefined;
	if (limit == null) return null;
	const used = activeCount(s, tenant ?? "");
	if (used + adding <= limit) return null;
	return { detail: `Document limit reached (${limit})`, code: "document_limit_reached", limit, used, adding };
}

/** Where a job is now, applying its result once it completes. */
function statusOf(s: DocState, j: JobRecord, now: number): string {
	if (j.expired) return "expired";
	if (j.skipped) return "skipped";
	if (j.reviewing) return "pending_review";
	if (j.failed) return "failed";
	const step = Math.floor((now - j.startedAt) / STAGE_MS);
	if (j.kind === "records") {
		if (step < 1) return "queued";
		if (step < 2) return "embedding";
	} else {
		if (j.similar && !j.autoConfirm && j.mode === "auto" && step >= 2) {
			j.reviewing = true;
			return "pending_review";
		}
		if (step < FILE_STAGES.length - 1) return FILE_STAGES[step] ?? "queued";
	}
	if (!j.applied) apply(s, j, now);
	return j.failed ? "failed" : "completed";
}

function apply(s: DocState, j: JobRecord, now: number) {
	j.applied = true;
	if (j.kind === "records") {
		const counts = { created: 0, updated: 0, unchanged: 0, chunks: 0 };
		for (const r of j.records ?? []) {
			const existing = s.docs.find((d) => d.corpusId === j.corpusId && d.external_id === r.id);
			const chunks = chunksFor(r.title ?? r.id, [r.title ? `${r.title}\n${r.text}` : r.text], ["FAQ"]);
			if (!existing) {
				s.docs.unshift({
					id: uid("d0c00000"),
					corpusId: j.corpusId,
					file_name: (r.title || r.id).slice(0, 500),
					mime_type: "text/plain",
					size_bytes: r.text.length,
					ingested_at: iso(now),
					external_id: r.id,
					metadata: r.metadata ?? null,
					source_uri: r.url ?? null,
					versions: [version(now, chunks, true, r.text)],
				});
				counts.created++;
				counts.chunks += chunks.length;
			} else if (active(existing)?.text === r.text) {
				counts.unchanged++;
			} else {
				for (const v of existing.versions) v.status = "inactive";
				existing.versions.push(version(now, chunks, true, r.text));
				counts.updated++;
				counts.chunks += chunks.length;
			}
		}
		j.counts = counts;
	} else {
		const lines = /price/i.test(j.file_name ?? "")
			? PRICE_LINES
			: /home/i.test(j.file_name ?? "")
				? HOME_LINES
				: [`${j.file_name ?? "Document"}: text read from the upload.`];
		const chunks = chunksFor(j.file_name ?? "document", lines, ["Section 1"]);
		const target = j.target ? s.docs.find((d) => d.id === j.target) : null;
		if (target && (j.mode === "replace" || j.mode === "version")) {
			for (const v of target.versions) v.status = "inactive";
			target.versions.push(version(now, chunks, true));
			target.size_bytes = j.size;
			target.ingested_at = iso(now);
		} else {
			s.docs.unshift({
				id: uid("d0c00000"),
				corpusId: j.corpusId,
				file_name: j.file_name,
				mime_type: j.mime_type,
				size_bytes: j.size,
				ingested_at: iso(now),
				external_id: null,
				metadata: j.metadata,
				source_uri: null,
				versions: [version(now, chunks, true)],
			});
		}
	}
	s.onChange(j.corpusId);
}

function jobOut(s: DocState, j: JobRecord, now: number) {
	const status = statusOf(s, j, now);
	const done = status === "completed" || status === "failed";
	return {
		id: j.id,
		corpus_id: j.corpusId,
		status,
		file_name: j.file_name,
		mode: j.mode,
		target_document_id: j.target,
		created_at: iso(j.createdAt),
		finished_at: done ? iso(j.startedAt + STAGE_MS * FILE_STAGES.length) : null,
		expires_at: j.kind === "file" ? iso(j.createdAt + 7 * 86_400_000) : null,
		processed_documents: done ? (j.records?.length ?? 1) : 0,
		total_documents: j.records?.length ?? 1,
		errors: j.failed ? { error: j.failed, failed_at_step: "parsing" } : null,
		records: j.counts,
		similar_documents:
			j.similar?.map((x) => ({ ...x, file_name: s.docs.find((d) => d.id === x.document_id)?.file_name ?? null })) ??
			null,
	};
}

// TV5: what the first-run brochure step reads (uploaded files of a knowledge base, and how many are read)
export function filesOf(s: DocState, corpusId: string, now = Date.now()): { count: number; learned: number } {
	const files = s.jobs.filter((j) => j.corpusId === corpusId && j.kind === "file");
	return { count: files.length, learned: files.filter((j) => statusOf(s, j, now) === "completed").length };
}
// /TV5

export interface Multipart {
	fields: Record<string, string>;
	file: { name: string; type: string; size: number } | null;
}

/** Enough multipart/form-data parsing for POST /ingest (text fields + one file). */
export function parseMultipart(body: Buffer | null, contentType: string): Multipart {
	const out: Multipart = { fields: {}, file: null };
	const boundary = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType);
	if (!body || !boundary) return out;
	const sep = Buffer.from(`--${boundary[1] ?? boundary[2]}`);
	let pos = body.indexOf(sep);
	while (pos !== -1) {
		const next = body.indexOf(sep, pos + sep.length);
		if (next === -1) break;
		const part = body.subarray(pos + sep.length + 2, next - 2);
		const headEnd = part.indexOf("\r\n\r\n");
		if (headEnd !== -1) {
			const head = part.subarray(0, headEnd).toString("utf8");
			const content = part.subarray(headEnd + 4);
			const name = /name="([^"]*)"/.exec(head)?.[1] ?? "";
			const filename = /filename="([^"]*)"/.exec(head)?.[1];
			if (filename !== undefined) {
				out.file = { name: filename, type: /content-type:\s*([^\r\n]+)/i.exec(head)?.[1] ?? "", size: content.length };
			} else {
				out.fields[name] = content.toString("utf8");
			}
		}
		pos = next;
	}
	return out;
}

export interface DocCtx {
	isAdmin: boolean;
	tenantId: string | undefined;
	role: "owner" | "member" | null;
	/** Corpora this principal may use. */
	canUse: (corpusId: string) => boolean;
	reply: (status: number, body?: unknown) => Promise<void>;
	multipart: () => Multipart;
}

/** Handles documents and ingest routes; returns false when the path isn't one of them. */
export async function handleDocumentRoute(
	s: DocState,
	method: string,
	url: URL,
	body: Record<string, unknown>,
	ctx: DocCtx,
): Promise<boolean> {
	const path = url.pathname;
	const now = Date.now();
	const { reply } = ctx;
	const done = (status: number, payload?: unknown) => reply(status, payload).then(() => true);
	const err = (status: number, detail: string, code?: string, extra: Record<string, unknown> = {}) =>
		done(status, code ? { detail, code, ...extra } : { detail, ...extra });
	const ownerOnly = () => ctx.role === "member";
	const corpusParam = url.searchParams.get("corpus_id") ?? "";

	// ---- ingest ----
	if (method === "POST" && path === "/api/v1/ingest") {
		const mp = ctx.multipart();
		const corpusId = mp.fields.corpus_id ?? "";
		if (!ctx.canUse(corpusId)) return err(403, "Corpus does not belong to this tenant");
		if (!mp.file) return err(422, "file is required");
		const key = mp.fields.idempotency_key || null;
		const known = key ? s.jobs.find((j) => j.corpusId === corpusId && j.key === key) : undefined;
		if (known) {
			return done(202, {
				job_id: known.id,
				status: statusOf(s, known, now),
				message: "Already accepted: this is the job the idempotency_key first created",
			});
		}
		const mode = mp.fields.mode || "auto";
		const target = mp.fields.target_document_id || null;
		if ((mode === "replace" || mode === "version") && !target)
			return err(422, `target_document_id is required when mode is '${mode}'`);
		const targetDoc = target ? s.docs.find((d) => d.id === target && d.corpusId === corpusId) : null;
		if (target && !targetDoc) return err(404, "Target document not found");
		const adding = mode === "new" || mode === "auto" ? 1 : 0;
		const cap = capError(s, corpusId, adding);
		if (cap) return done(409, cap);
		let metadata: Record<string, string> | null = null;
		if (mp.fields.metadata) {
			try {
				metadata = JSON.parse(mp.fields.metadata) as Record<string, string>;
			} catch {
				return err(422, "Invalid metadata JSON");
			}
		}
		const same = s.docs.find((d) => d.corpusId === corpusId && d.file_name === mp.file?.name && active(d));
		const job: JobRecord = {
			id: uid("10b00000"),
			corpusId,
			kind: "file",
			file_name: mp.file.name,
			mime_type: mp.file.type || null,
			size: mp.file.size,
			mode,
			target,
			autoConfirm: mp.fields.auto_confirm === "true",
			metadata,
			key,
			createdAt: now,
			startedAt: now,
			similar: same ? [{ document_id: same.id, similarity: 0.96, match_count: 38 }] : null,
			reviewing: false,
			applied: false,
			expired: false,
			skipped: false,
			failed: null,
			records: null,
			counts: null,
		};
		s.jobs.unshift(job);
		return done(202, { job_id: job.id, status: "queued", message: "Document accepted for processing" });
	}
	if (method === "POST" && path === "/api/v1/ingest/records") {
		const corpusId = String(body.corpus_id ?? "");
		if (!ctx.canUse(corpusId)) return err(403, "Corpus does not belong to this tenant");
		const records = (body.records ?? []) as NonNullable<JobRecord["records"]>;
		if (!records.length || records.length > 1000) return err(422, "records: 1 to 1000 items");
		const adding = records.filter((r) => !s.docs.some((d) => d.corpusId === corpusId && d.external_id === r.id)).length;
		const cap = capError(s, corpusId, adding);
		if (cap) return done(409, cap);
		const job: JobRecord = {
			id: uid("10b00000"),
			corpusId,
			kind: "records",
			file_name: null,
			mime_type: null,
			size: 0,
			mode: "records",
			target: null,
			autoConfirm: true,
			metadata: null,
			key: null,
			createdAt: now,
			startedAt: now,
			similar: null,
			reviewing: false,
			applied: false,
			expired: false,
			skipped: false,
			failed: null,
			records,
			counts: null,
		};
		s.jobs.unshift(job);
		return done(202, { job_id: job.id, status: "queued", records: records.length });
	}
	const confirm = /^\/api\/v1\/ingest\/([^/]+)\/confirm$/.exec(path);
	if (method === "POST" && confirm) {
		const j = s.jobs.find((x) => x.id === confirm[1]);
		if (!j || !ctx.canUse(j.corpusId)) return err(404, "Job not found");
		const status = statusOf(s, j, now);
		const action = String(body.action ?? "");
		if (action === "skip" && status === "skipped") {
			return done(200, { job_id: j.id, status: "skipped", message: "Skipped" });
		}
		if (status !== "pending_review") return err(409, `Job is not pending review (current status: ${status})`);
		if (action === "skip") {
			j.skipped = true;
			j.reviewing = false;
			return done(200, { job_id: j.id, status: "skipped", message: "Skipped" });
		}
		if (action === "replace") {
			if (!body.target_document_id) return err(422, "target_document_id required when action is 'replace'");
			j.mode = "replace";
			j.target = String(body.target_document_id);
		} else {
			const cap = capError(s, j.corpusId, 1);
			if (cap) return done(409, cap);
			j.mode = "new";
			j.target = null;
		}
		j.reviewing = false;
		j.similar = null;
		j.startedAt = now;
		return done(200, { job_id: j.id, status: "queued", message: `Confirmed: proceeding as '${j.mode}'` });
	}
	const oneJob = /^\/api\/v1\/ingest\/jobs\/([^/]+)$/.exec(path);
	if (method === "GET" && oneJob) {
		const j = s.jobs.find((x) => x.id === oneJob[1]);
		if (!j || !ctx.canUse(j.corpusId)) return err(404, "Job not found");
		return done(200, jobOut(s, j, now));
	}
	if (method === "GET" && path === "/api/v1/ingest/jobs") {
		const status = url.searchParams.get("status");
		const offset = Number(url.searchParams.get("offset") ?? 0);
		const limit = Number(url.searchParams.get("limit") ?? 20);
		const all = s.jobs
			.filter((j) => ctx.canUse(j.corpusId) && (!corpusParam || j.corpusId === corpusParam))
			.map((j) => jobOut(s, j, now))
			.filter((j) => !status || j.status === status);
		const items = all.slice(offset, offset + limit);
		return done(200, { items, count: items.length, total: all.length, offset, limit });
	}

	// ---- documents ----
	if (!path.startsWith("/api/v1/documents")) return false;
	if (!ctx.canUse(corpusParam)) return err(403, "Corpus does not belong to this tenant");
	const inCorpus = s.docs.filter((d) => d.corpusId === corpusParam);
	if (method === "GET" && path === "/api/v1/documents") {
		const q = (url.searchParams.get("search") ?? "").toLowerCase();
		const offset = Number(url.searchParams.get("offset") ?? 0);
		const limit = Number(url.searchParams.get("limit") ?? 50);
		const all = inCorpus.filter(
			(d) => !q || (d.file_name ?? "").toLowerCase().includes(q) || (d.external_id ?? "").toLowerCase().includes(q),
		);
		return done(200, { items: all.slice(offset, offset + limit).map(summary), count: all.length });
	}
	const m = /^\/api\/v1\/documents\/([^/]+)(\/versions(?:\/([^/]+)\/restore)?|\/replace\/([^/]+))?$/.exec(path);
	if (!m) return false;
	const doc = inCorpus.find((d) => d.id === m[1]);
	if (!doc) return err(404, "Document not found");
	const sub = m[2];
	if (method === "GET" && !sub) {
		const vid = url.searchParams.get("version_id") ?? active(doc)?.id ?? null;
		const v = vid ? doc.versions.find((x) => x.id === vid) : null;
		if (vid && !v) return err(404, "Version not found");
		const offset = Number(url.searchParams.get("chunk_offset") ?? 0);
		const limit = Number(url.searchParams.get("chunk_limit") ?? 50);
		const chunks = v?.chunks ?? [];
		return done(200, {
			...summary(doc),
			source_uri: doc.source_uri,
			versions: doc.versions.map(versionOut),
			version_id: v?.id ?? null,
			chunks: chunks.slice(offset, offset + limit),
			chunk_total: chunks.length,
			chunk_offset: offset,
			chunk_limit: limit,
		});
	}
	if (method === "GET" && sub === "/versions") {
		return done(200, {
			document_id: doc.id,
			file_name: doc.file_name,
			versions: doc.versions.map(versionOut),
			count: doc.versions.length,
		});
	}
	if (method === "POST" && m[3]) {
		const v = doc.versions.find((x) => x.id === m[3]);
		if (!v) return err(404, "Version not found");
		if (v.status === "active") return err(409, "Version is already active");
		const cap = capError(s, doc.corpusId, active(doc) ? 0 : 1);
		if (cap) return done(409, cap);
		const previous = active(doc)?.id ?? null;
		for (const x of doc.versions) x.status = x.id === v.id ? "active" : "inactive";
		s.onChange(doc.corpusId);
		return done(200, {
			document_id: doc.id,
			restored_version_id: v.id,
			previous_active_version_id: previous,
			status: "restored",
		});
	}
	if (method === "POST" && m[4]) {
		if (ownerOnly()) return err(403, "Only a workspace owner can do this", "owner_required");
		const into = inCorpus.find((d) => d.id === m[4]);
		if (!into) return err(404, "Old document not found");
		if (into.id === doc.id) return err(422, "Cannot merge document with itself");
		for (const v of into.versions) v.status = "inactive";
		into.versions.push(...doc.versions);
		s.docs = s.docs.filter((d) => d.id !== doc.id);
		s.onChange(doc.corpusId);
		return done(200, { old_document_id: into.id, new_document_id: doc.id, versions_preserved: into.versions.length });
	}
	if (method === "DELETE" && !sub) {
		if (ownerOnly()) return err(403, "Only a workspace owner can do this", "owner_required");
		s.docs = s.docs.filter((d) => d.id !== doc.id);
		s.onChange(doc.corpusId);
		return done(204);
	}
	return false;
}
