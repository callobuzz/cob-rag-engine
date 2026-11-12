import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, type PRIYA, rt, SHREE, tokenResponse } from "../msw/data";
import { server } from "../msw/server";
import { renderApp } from "../render";

/** Helpers shared by the Assistant screen tests (Indian demo data: Shree Labs, Patient help). */
export const PATIENT = CORPORA[0]?.id ?? "";
export const MEMBER_SHREE = { ...SHREE, role: "member" as const };

export function signedInAs(user: typeof PRIYA, tenant: typeof SHREE) {
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () =>
			HttpResponse.json(tokenResponse({ user, tenant, tenants: [tenant] }, 2)),
		),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants: [tenant], session_id: "s" })),
		http.get(`${BASE}/api/v1/corpora`, () => {
			const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
			return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 25 });
		}),
	);
}

const listeners: Array<(arg: { request: Request }) => void> = [];
afterEach(() => {
	for (const l of listeners.splice(0)) server.events.removeListener("request:start", l);
});

/** JSON bodies of every request matching `match` (method + URL test), recorded as they are sent. */
export function capture(match: (request: Request) => boolean): Record<string, unknown>[] {
	const bodies: Record<string, unknown>[] = [];
	const listener = async ({ request }: { request: Request }) => {
		if (!match(request)) return;
		try {
			bodies.push((await request.clone().json()) as Record<string, unknown>);
		} catch {
			bodies.push({});
		}
	};
	listeners.push(listener);
	server.events.on("request:start", listener);
	return bodies;
}

export const capturePuts = () => capture((r) => r.method === "PUT" && r.url.endsWith("/assistant/draft"));

export async function openAssistant(section?: string) {
	const qs = section ? `&section=${section}` : "";
	const app = renderApp(`/kbs/${PATIENT}?tab=assistant${qs}`);
	await screen.findByRole("heading", { level: 1, name: "Patient help" });
	return app;
}
