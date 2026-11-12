import { HttpResponse, http } from "msw";
import type { ChunkView, DocumentDetail, DocumentSummary, VersionSummary } from "@/features/documents/api";
import { BASE, CORPORA } from "../data";

/**
 * Documents of "Patient help" (D4), stateful between calls within a test (reset in setup.ts).
 * Indian demo data only.
 */
export const PATIENT = CORPORA[0]?.id ?? "";
export const DOC_PRICE = "d0c00001-0000-4000-8000-000000000001";
export const DOC_HOME = "d0c00002-0000-4000-8000-000000000002";
export const DOC_FAQ = "d0c00003-0000-4000-8000-000000000003";
export const V1 = "7e500001-0000-4000-8000-000000000001";
export const V2 = "7e500002-0000-4000-8000-000000000002";

interface Doc extends DocumentSummary {
	versions: VersionSummary[];
	source_uri: string | null;
	chunks: Record<string, ChunkView[]>;
}

function chunk(i: number, text: string, extra: Partial<ChunkView> = {}): ChunkView {
	return {
		id: `c0000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
		chunk_index: i,
		text,
		page: 1,
		token_count: Math.ceil(text.length / 4),
		is_table: false,
		is_code: false,
		parent_chunk_id: null,
		section: ["Test prices", "Blood tests"],
		...extra,
	};
}

function seed(): Doc[] {
	const priceChunks = Array.from({ length: 60 }, (_, i) =>
		chunk(
			i,
			i === 0
				? "CBC (complete blood count): ₹350. Thyroid profile (T3, T4, TSH): ₹550. Fasting not needed."
				: `Price list line ${i + 1}: ${"Lipid profile ₹600, fasting 10–12 hours. ".repeat(i === 1 ? 20 : 1)}`,
			i === 2 ? { is_table: true, parent_chunk_id: `c0000000-0000-4000-8000-${"0".repeat(12)}` } : {},
		),
	);
	return [
		{
			id: DOC_PRICE,
			file_name: "test-price-list-oct.pdf",
			mime_type: "application/pdf",
			size_bytes: 248_000,
			ingested_at: "2026-09-29T10:00:00Z",
			external_id: null,
			metadata: { category: "prices", branch: "Kothrud" },
			active_version_id: V2,
			version_count: 2,
			source_uri: null,
			versions: [
				{
					id: V1,
					status: "inactive",
					parser_version: "docling-2",
					chunk_schema_version: "1",
					created_at: "2026-09-01T10:00:00Z",
					chunk_count: 40,
				},
				{
					id: V2,
					status: "active",
					parser_version: "docling-2",
					chunk_schema_version: "1",
					created_at: "2026-09-29T10:00:00Z",
					chunk_count: 60,
				},
			],
			chunks: {
				[V1]: [chunk(0, "Old September price list: CBC ₹320.", { section: ["Test prices"] })],
				[V2]: priceChunks,
			},
		},
		{
			id: DOC_HOME,
			file_name: "home-collection.pdf",
			mime_type: "application/pdf",
			size_bytes: 91_000,
			ingested_at: "2026-09-20T10:00:00Z",
			external_id: null,
			metadata: null,
			active_version_id: "7e500003-0000-4000-8000-000000000003",
			version_count: 1,
			source_uri: "https://shree-labs.in/home-collection",
			versions: [
				{
					id: "7e500003-0000-4000-8000-000000000003",
					status: "active",
					parser_version: "docling-2",
					chunk_schema_version: "1",
					created_at: "2026-09-20T10:00:00Z",
					chunk_count: 1,
				},
			],
			chunks: {
				"7e500003-0000-4000-8000-000000000003": [chunk(0, "Home collection is free above ₹999 within Pune city.")],
			},
		},
		{
			id: DOC_FAQ,
			file_name: null,
			mime_type: "text/plain",
			size_bytes: 180,
			ingested_at: "2026-09-28T03:00:00Z",
			external_id: "faq-fasting",
			metadata: { language: "hi" },
			active_version_id: null,
			version_count: 1,
			source_uri: null,
			versions: [
				{
					id: "7e500004-0000-4000-8000-000000000004",
					status: "inactive",
					parser_version: null,
					chunk_schema_version: "1",
					created_at: "2026-09-28T03:00:00Z",
					chunk_count: 1,
				},
			],
			chunks: {},
		},
	];
}

let docs: Doc[] = seed();
export const documentsLog: { method: string; path: string; search: string }[] = [];
export function resetDocumentsState(): void {
	docs = seed();
	documentsLog.length = 0;
}

const summary = (d: Doc): DocumentSummary => ({
	id: d.id,
	file_name: d.file_name,
	mime_type: d.mime_type,
	size_bytes: d.size_bytes,
	ingested_at: d.ingested_at,
	external_id: d.external_id,
	metadata: d.metadata,
	active_version_id: d.active_version_id,
	version_count: d.versions.length,
});

const notFound = () => HttpResponse.json({ detail: "Document not found" }, { status: 404 });

export const documentsHandlers = [
	http.get(`${BASE}/api/v1/documents`, ({ request }) => {
		const u = new URL(request.url);
		documentsLog.push({ method: "GET", path: u.pathname, search: u.search });
		const search = (u.searchParams.get("search") ?? "").toLowerCase();
		const offset = Number(u.searchParams.get("offset") ?? 0);
		const limit = Number(u.searchParams.get("limit") ?? 50);
		const corpus = u.searchParams.get("corpus_id");
		const all = (corpus === PATIENT ? docs : []).filter(
			(d) => !search || (d.file_name ?? "").toLowerCase().includes(search) || (d.external_id ?? "").includes(search),
		);
		return HttpResponse.json({ items: all.slice(offset, offset + limit).map(summary), count: all.length });
	}),
	http.get(`${BASE}/api/v1/documents/:id/versions`, ({ params }) => {
		const d = docs.find((x) => x.id === params.id);
		if (!d) return notFound();
		return HttpResponse.json({
			document_id: d.id,
			file_name: d.file_name,
			versions: d.versions,
			count: d.versions.length,
		});
	}),
	http.get(`${BASE}/api/v1/documents/:id`, ({ params, request }) => {
		const u = new URL(request.url);
		documentsLog.push({ method: "GET", path: u.pathname, search: u.search });
		const d = docs.find((x) => x.id === params.id);
		if (!d) return notFound();
		const versionId = u.searchParams.get("version_id") ?? d.active_version_id;
		if (versionId && !d.versions.some((v) => v.id === versionId))
			return HttpResponse.json({ detail: "Version not found" }, { status: 404 });
		const offset = Number(u.searchParams.get("chunk_offset") ?? 0);
		const limit = Number(u.searchParams.get("chunk_limit") ?? 50);
		const all = versionId ? (d.chunks[versionId] ?? []) : [];
		const out: DocumentDetail = {
			...summary(d),
			active_version_id: d.active_version_id ?? null,
			external_id: d.external_id ?? null,
			metadata: d.metadata ?? null,
			source_uri: d.source_uri,
			versions: d.versions,
			version_id: versionId,
			chunks: all.slice(offset, offset + limit),
			chunk_total: all.length,
			chunk_offset: offset,
			chunk_limit: limit,
		};
		return HttpResponse.json(out);
	}),
	http.post(`${BASE}/api/v1/documents/:id/versions/:vid/restore`, ({ params }) => {
		const d = docs.find((x) => x.id === params.id);
		if (!d) return notFound();
		const v = d.versions.find((x) => x.id === params.vid);
		if (!v) return HttpResponse.json({ detail: "Version not found" }, { status: 404 });
		if (v.status === "active") return HttpResponse.json({ detail: "Version is already active" }, { status: 409 });
		const previous = d.active_version_id;
		for (const x of d.versions) x.status = x.id === v.id ? "active" : "inactive";
		d.active_version_id = v.id;
		return HttpResponse.json({
			document_id: d.id,
			restored_version_id: v.id,
			previous_active_version_id: previous,
			status: "restored",
		});
	}),
	http.post(`${BASE}/api/v1/documents/:id/replace/:old`, ({ params }) => {
		const from = docs.find((x) => x.id === params.id);
		const into = docs.find((x) => x.id === params.old);
		if (!from || !into) return notFound();
		into.versions = [...into.versions.map((v) => ({ ...v, status: "inactive" })), ...from.versions];
		docs = docs.filter((x) => x.id !== from.id);
		return HttpResponse.json({
			old_document_id: into.id,
			new_document_id: from.id,
			versions_preserved: into.versions.length,
		});
	}),
	http.delete(`${BASE}/api/v1/documents/:id`, ({ params }) => {
		const d = docs.find((x) => x.id === params.id);
		if (!d) return notFound();
		docs = docs.filter((x) => x.id !== d.id);
		return new HttpResponse(null, { status: 204 });
	}),
];

/** Test hook: turn a seeded document into the business card (a system document, kept by the assistant). */
export function markSystemDocument(id: string): void {
	const d = docs.find((x) => x.id === id);
	if (!d) return;
	d.external_id = "system:business-card";
	d.file_name = "Business card";
}
