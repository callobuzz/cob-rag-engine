import { HttpResponse, http } from "msw";
import type { Invitation, Member } from "@/lib/api/accounts-types";
import { BASE, PRIYA, ROHAN, SHREE } from "../data";
import { coded } from "./auth";

/**
 * A5 teams, stateful per tenant id. `/tenants/me/...` acts on Shree Labs; the admin routes on any
 * tenant (their invitations carry `accept_url`). Rules like `accounts/members.py`: the last owner
 * can't be demoted, removed or leave (`last_owner`), an existing member can't be invited
 * (`already_member`), the operator's address can't be invited (`superadmin_email`), and a
 * workspace invitation needs e-mail (`email_not_configured` when `teamState.emailConfigured` is
 * false).
 */
export const MEERA: Member = {
	user_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
	email: "meera@shree-labs.in",
	name: "Dr. Meera Shah",
	role: "member",
	joined_at: "2026-09-05T10:00:00Z",
	last_login_at: "2026-09-30T08:00:00Z",
};

function seed(): Map<string, { members: Member[]; invitations: Invitation[] }> {
	return new Map([
		[
			SHREE.id,
			{
				members: [
					{
						user_id: PRIYA.id,
						email: PRIYA.email,
						name: PRIYA.name,
						role: "owner",
						joined_at: "2026-09-01T10:00:00Z",
						last_login_at: "2026-09-30T09:55:00Z",
					},
					{ ...MEERA },
					{
						user_id: ROHAN.id,
						email: ROHAN.email,
						name: ROHAN.name,
						role: "member",
						joined_at: "2026-09-06T10:00:00Z",
						last_login_at: null,
					},
				],
				invitations: [
					{
						id: "1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a",
						email: "dr.nikhil@shree-labs.in",
						role: "member",
						invited_by: "Priya Kulkarni",
						created_at: "2026-09-26T10:00:00Z",
						expires_at: "2026-10-03T10:00:00Z",
					},
				],
			},
		],
	]);
}

export const teamState = {
	teams: seed(),
	emailConfigured: true,
	sent: [] as { method: string; path: string; body: unknown }[],
};

export function resetTeamState(): void {
	teamState.teams = seed();
	teamState.emailConfigured = true;
	teamState.sent = [];
}

function teamOf(id: string) {
	let t = teamState.teams.get(id);
	if (!t) {
		t = { members: [], invitations: [] };
		teamState.teams.set(id, t);
	}
	return t;
}

let seq = 0;
function invite(tenantId: string, body: { email: string; role: "owner" | "member" }, by: string) {
	const team = teamOf(tenantId);
	const email = body.email.trim().toLowerCase();
	if (email === "sv@punedigital.in") return { error: coded(409, "superadmin_email") };
	if (team.members.some((m) => m.email === email)) return { error: coded(409, "already_member") };
	team.invitations = team.invitations.filter((i) => i.email !== email);
	seq++;
	const inv: Invitation = {
		id: `2b2b2b2b-2b2b-4b2b-8b2b-${String(seq).padStart(12, "0")}`,
		email,
		role: body.role,
		invited_by: by,
		created_at: "2026-09-30T10:00:00Z",
		expires_at: "2026-10-07T10:00:00Z",
	};
	team.invitations.unshift(inv);
	return { inv };
}

function changeRole(tenantId: string, userId: string, role: "owner" | "member") {
	const team = teamOf(tenantId);
	const m = team.members.find((x) => x.user_id === userId);
	if (!m) return HttpResponse.json({ detail: "Member not found" }, { status: 404 });
	if (m.role === "owner" && role === "member" && team.members.filter((x) => x.role === "owner").length === 1)
		return coded(409, "last_owner");
	m.role = role;
	return HttpResponse.json(m);
}

function remove(tenantId: string, userId: string) {
	const team = teamOf(tenantId);
	const m = team.members.find((x) => x.user_id === userId);
	if (!m) return HttpResponse.json({ detail: "Member not found" }, { status: 404 });
	if (m.role === "owner" && team.members.filter((x) => x.role === "owner").length === 1)
		return coded(409, "last_owner");
	team.members = team.members.filter((x) => x.user_id !== userId);
	return new HttpResponse(null, { status: 204 });
}

function revoke(tenantId: string, id: string) {
	const team = teamOf(tenantId);
	if (!team.invitations.some((i) => i.id === id))
		return HttpResponse.json({ detail: "Invitation not found" }, { status: 404 });
	team.invitations = team.invitations.filter((i) => i.id !== id);
	return new HttpResponse(null, { status: 204 });
}

async function record(request: Request) {
	const text = await request.clone().text();
	teamState.sent.push({
		method: request.method,
		path: new URL(request.url).pathname,
		body: text ? JSON.parse(text) : null,
	});
}

export const membersHandlers = [
	http.get(`${BASE}/api/v1/tenants/me/members`, () => HttpResponse.json(teamOf(SHREE.id))),
	http.post(`${BASE}/api/v1/tenants/me/members`, async ({ request }) => {
		await record(request);
		if (!teamState.emailConfigured) return coded(503, "email_not_configured");
		const r = invite(SHREE.id, (await request.json()) as { email: string; role: "owner" | "member" }, PRIYA.name);
		return r.error ?? HttpResponse.json(r.inv, { status: 201 });
	}),
	http.post(`${BASE}/api/v1/tenants/me/invitations/:id/resend`, async ({ params, request }) => {
		await record(request);
		const inv = teamOf(SHREE.id).invitations.find((i) => i.id === params.id);
		if (!inv) return HttpResponse.json({ detail: "Invitation not found" }, { status: 404 });
		if (!teamState.emailConfigured) return coded(503, "email_not_configured");
		inv.expires_at = "2026-10-07T10:00:00Z";
		return HttpResponse.json(inv);
	}),
	http.delete(`${BASE}/api/v1/tenants/me/invitations/:id`, async ({ params, request }) => {
		await record(request);
		return revoke(SHREE.id, String(params.id));
	}),
	http.patch(`${BASE}/api/v1/tenants/me/members/:uid`, async ({ params, request }) => {
		await record(request);
		const b = (await request.json()) as { role: "owner" | "member" };
		return changeRole(SHREE.id, String(params.uid), b.role);
	}),
	http.delete(`${BASE}/api/v1/tenants/me/members/:uid`, async ({ params, request }) => {
		await record(request);
		return remove(SHREE.id, String(params.uid));
	}),
	http.get(`${BASE}/api/v1/admin/tenants/:id/members`, ({ params }) => HttpResponse.json(teamOf(String(params.id)))),
	http.post(`${BASE}/api/v1/admin/tenants/:id/invitations`, async ({ params, request }) => {
		await record(request);
		const r = invite(String(params.id), (await request.json()) as { email: string; role: "owner" | "member" }, "admin");
		if (r.error) return r.error;
		return HttpResponse.json(
			{ ...r.inv, accept_url: `http://127.0.0.1:5173/invite#token=inv_${r.inv.id.slice(-6)}` },
			{ status: 201 },
		);
	}),
	// A8: the operator's resend — a new link and 7 more days, with `accept_url`.
	http.post(`${BASE}/api/v1/admin/tenants/:id/invitations/:inv/resend`, async ({ params, request }) => {
		await record(request);
		const inv = teamOf(String(params.id)).invitations.find((i) => i.id === params.inv);
		if (!inv) return HttpResponse.json({ detail: "Invitation not found" }, { status: 404 });
		inv.expires_at = "2026-10-08T10:00:00Z";
		return HttpResponse.json({
			...inv,
			accept_url: `http://127.0.0.1:5173/invite#token=inv_resent_${inv.id.slice(-6)}`,
		});
	}),
	http.delete(`${BASE}/api/v1/admin/tenants/:id/invitations/:inv`, async ({ params, request }) => {
		await record(request);
		return revoke(String(params.id), String(params.inv));
	}),
	http.patch(`${BASE}/api/v1/admin/tenants/:id/members/:uid`, async ({ params, request }) => {
		await record(request);
		const b = (await request.json()) as { role: "owner" | "member" };
		return changeRole(String(params.id), String(params.uid), b.role);
	}),
	http.delete(`${BASE}/api/v1/admin/tenants/:id/members/:uid`, async ({ params, request }) => {
		await record(request);
		return remove(String(params.id), String(params.uid));
	}),
];
