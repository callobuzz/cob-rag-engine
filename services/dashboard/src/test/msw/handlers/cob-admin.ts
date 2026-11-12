import { HttpResponse, http } from "msw";
import { BASE } from "../data";

/** COB9: `GET/PATCH /admin/cob/documents`, a platform upload/record import and a document delete. */
export const COB_KB = "c0b00000-0000-4000-8000-0000000000c1";

export interface CobDocFixture {
	id: string;
	name: string;
	source: "builtin" | "platform";
	audience: "admin" | "customer" | "public";
	uploaded_by: string | null;
	ingested_at: string;
}

export const cobAdminState: { docs: CobDocFixture[]; calls: string[] } = { docs: [], calls: [] };

export function resetCobAdmin(docs: CobDocFixture[] = []) {
	cobAdminState.docs = docs;
	cobAdminState.calls = [];
}

export const cobAdminHandlers = [
	http.get(`${BASE}/api/v1/admin/cob/documents`, () =>
		HttpResponse.json({ items: cobAdminState.docs, corpus_id: COB_KB }),
	),
	http.patch(`${BASE}/api/v1/admin/cob/documents/:id`, async ({ params, request }) => {
		const body = (await request.json()) as { audience: CobDocFixture["audience"] };
		const doc = cobAdminState.docs.find((d) => d.id === params.id);
		if (!doc) return HttpResponse.json({ detail: "x", code: "document_not_found" }, { status: 404 });
		if (doc.source === "builtin")
			return HttpResponse.json({ detail: "x", code: "cob_builtin_readonly" }, { status: 403 });
		doc.audience = body.audience;
		cobAdminState.calls.push(`PATCH ${doc.name} ${body.audience}`);
		return HttpResponse.json(doc);
	}),
	http.delete(`${BASE}/api/v1/documents/:id`, ({ params }) => {
		cobAdminState.calls.push(`DELETE ${params.id}`);
		cobAdminState.docs = cobAdminState.docs.filter((d) => d.id !== params.id);
		return new HttpResponse(null, { status: 204 });
	}),
];
