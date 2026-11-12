import { HttpResponse, http } from "msw";
import type { Onboarding, StartOnboarding } from "@/features/onboarding/api";
import { BASE, CORPORA } from "../data";
import { coded } from "./auth";

/**
 * BIZ4 handlers: the first-run routes (`GET/POST/PATCH /onboarding`), the avatar upload and the
 * suggested questions, stateful within a test (reset in setup.ts). A new workspace starts as
 * `new`; `POST /onboarding/website` does what the engine does (knowledge base, key, step 2).
 * Shree Dental Clinic, Pune is the demo business.
 */
export const KB = CORPORA[0]?.id ?? "";
export const KEY_ID = "5b0b0000-0000-4000-8000-000000000001";
export const SNIPPET = `<script src="${BASE}/widget.js" data-key="pk_shree_dental_0123456789" async></script>`;

export const FRESH: Onboarding = {
	step: 1,
	status: "new",
	website: null,
	kb_id: null,
	widget_key_id: null,
	snippet: null,
	connection: { status: "waiting", origin: null, first_seen_at: null, refused: [] },
	learning: null,
	files: { count: 0, learned: 0 }, // TV5
};

interface OnboardingMock {
	state: Onboarding;
	started: StartOnboarding[];
	patches: Record<string, unknown>[];
	avatars: string[];
	/** A failing start (the inner route's own status and body). */
	failStart: { status: number; body: Record<string, unknown> } | null;
	suggestions: Record<string, string[]>;
	polls: number;
}

const initial = (): OnboardingMock => ({
	state: { ...FRESH },
	started: [],
	patches: [],
	avatars: [],
	failStart: null,
	suggestions: {
		en: ["What are your prices?", "Where are you located?", "Tell me about Root Canal Treatment"],
		"hi-Latn": ["Aapki fees kitni hai?", "Aapka address kya hai?"],
	},
	polls: 0,
});

export const onboardingMock: OnboardingMock = initial();

export function resetOnboardingState(): void {
	Object.assign(onboardingMock, initial());
}

/** A workspace that has been through step 1 (what the engine returns after a successful start). */
export function startedState(over: Partial<Onboarding> = {}): Onboarding {
	return {
		step: 2,
		status: "in_progress",
		website: "https://shreedental.in",
		kb_id: KB,
		widget_key_id: KEY_ID,
		snippet: SNIPPET,
		connection: { status: "waiting", origin: null, first_seen_at: null, refused: [] },
		learning: { status: "learning", pages_learned: 23, pages_found: 60, first_pass_done: false },
		files: { count: 0, learned: 0 }, // TV5
		...over,
	};
}

export const onboardingHandlers = [
	http.get(`${BASE}/api/v1/onboarding`, () => {
		onboardingMock.polls += 1;
		return HttpResponse.json(onboardingMock.state);
	}),
	http.post(`${BASE}/api/v1/onboarding/website`, async ({ request }) => {
		const body = (await request.json()) as StartOnboarding;
		onboardingMock.started.push(body);
		if (onboardingMock.failStart) {
			return HttpResponse.json(onboardingMock.failStart.body, { status: onboardingMock.failStart.status });
		}
		if (!/\./.test(body.website))
			return coded(422, "website_url_invalid", { detail: "Enter your website like shreedental.in" });
		const host = body.website.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
		onboardingMock.state = startedState({ website: `https://${host}` });
		return HttpResponse.json(onboardingMock.state);
	}),
	http.patch(`${BASE}/api/v1/onboarding`, async ({ request }) => {
		const body = (await request.json()) as { step?: number; status?: Onboarding["status"] };
		onboardingMock.patches.push(body);
		onboardingMock.state = {
			...onboardingMock.state,
			...(body.step ? { step: body.step } : {}),
			...(body.status ? { status: body.status } : {}),
		};
		return HttpResponse.json(onboardingMock.state);
	}),
	http.post(`${BASE}/api/v1/corpora/:id/assistant/avatar`, async ({ request }) => {
		const form = await request.formData();
		const file = form.get("file");
		// jsdom cannot send a File through MSW: the screen tests send it as "file:<name>"
		const name = typeof file === "string" ? file.replace(/^file:/, "") : file instanceof File ? file.name : "";
		onboardingMock.avatars.push(name);
		if (name.endsWith(".svg")) {
			return coded(422, "avatar_invalid", {
				detail: "That file is not a usable picture. Use a PNG, JPG or WebP image at least 64 pixels wide and tall.",
			});
		}
		if (name.startsWith("huge")) return coded(413, "avatar_too_large", { detail: "That picture is too large." });
		return HttpResponse.json(
			{ avatar_url: "/api/v1/public/avatars/6f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b.png" },
			{ status: 201 },
		);
	}),
	http.get(`${BASE}/api/v1/corpora/:id/website/suggestions`, ({ request }) => {
		const lang = new URL(request.url).searchParams.get("lang") ?? "en";
		return HttpResponse.json({ items: (onboardingMock.suggestions[lang] ?? []).map((text) => ({ text })) });
	}),
];
