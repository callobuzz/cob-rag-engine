import { HttpResponse, http } from "msw";
import { BASE, CORPORA } from "../data";

const PATIENT = CORPORA[0]?.id ?? "";

/** Latest gap report (`/analytics/gaps`), with clusters as the list response carries them. */
export const GAP_REPORT = {
	id: "9a9a9a9a-0000-4000-8000-000000000001",
	corpus_id: PATIENT,
	status: "completed",
	query_count: 40,
	cluster_count: 3,
	started_at: "2026-09-29T02:00:00Z",
	completed_at: "2026-09-29T02:03:00Z",
	clusters: [
		{
			topic_id: 1,
			query_count: 4,
			keywords: ["hba1c", "diabetes", "package"],
			representative_queries: ["Is HbA1c included in the diabetes package?"],
		},
		{
			topic_id: 0,
			query_count: 7,
			keywords: ["home collection", "baner", "sunday"],
			representative_queries: ["Home collection in Baner on Sunday?"],
		},
	],
};

export const HISTORY = [
	{
		id: "0b0b0b0b-0000-4000-8000-000000000001",
		query_text: "Do I need to fast for a lipid profile?",
		corpus_id: PATIENT,
		created_at: "2026-09-30T08:10:00Z",
		confidence_score: 0.93,
	},
	{
		id: "0b0b0b0b-0000-4000-8000-000000000002",
		query_text: "लिपिड प्रोफाइल के लिए उपवास ज़रूरी है?",
		corpus_id: PATIENT,
		created_at: "2026-09-30T07:40:00Z",
		confidence_score: 0.88,
	},
];

/** Analytics the Overview reads (D2): trends, count, history and gap reports. */
export const analyticsHandlers = [
	http.get(`${BASE}/api/v1/analytics/queries/trends`, () =>
		HttpResponse.json({
			data: [
				{ bucket: "2026-09-28T00:00:00Z", count: 100, avg_confidence: 0.9, avg_latency: 800 },
				{ bucket: "2026-09-29T00:00:00Z", count: 100, avg_confidence: 0.96, avg_latency: 700 },
			],
		}),
	),
	http.get(`${BASE}/api/v1/analytics/queries/count`, () => HttpResponse.json({ count: 42 })),
	http.get(`${BASE}/api/v1/analytics/queries/history`, ({ request }) => {
		const limit = Number(new URL(request.url).searchParams.get("limit") ?? 20);
		return HttpResponse.json({ items: HISTORY.slice(0, limit), total: HISTORY.length, offset: 0, limit });
	}),
	http.get(`${BASE}/api/v1/analytics/gaps`, () => HttpResponse.json({ items: [GAP_REPORT], total: 1 })),
];
