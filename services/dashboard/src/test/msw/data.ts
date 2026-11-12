import type { PublicBranding, TokenResponse, TokenTenant, TokenUser } from "@/lib/api/accounts-types";
import type { Corpus } from "@/lib/api/queries";

/** Indian demo data only (README "Demo data, locale and currency"). */
export const BASE = "http://127.0.0.1:8000";

export const SHREE: TokenTenant = {
	id: "11111111-1111-4111-8111-111111111111",
	name: "Shree Labs",
	role: "owner",
	status: "active",
	is_operator: false,
};
export const RANGOLI: TokenTenant = {
	id: "22222222-2222-4222-8222-222222222222",
	name: "Rangoli Sarees",
	role: "member",
	status: "active",
	is_operator: false,
};

/** The operator's own workspace (named after the product); the super admin owns it. */
export const OPERATOR: TokenTenant = {
	id: "10101010-1010-4010-8010-101010101010",
	name: "Sahayak AI",
	role: "owner",
	status: "active",
	is_operator: true,
};

export const PRIYA: TokenUser = {
	id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
	email: "priya@shree-labs.in",
	name: "Priya Kulkarni",
	is_superadmin: false,
};
export const ROHAN: TokenUser = {
	id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
	email: "rohan@shree-labs.in",
	name: "Rohan Deshmukh",
	is_superadmin: false,
};
export const SV: TokenUser = {
	id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
	email: "sv@punedigital.in",
	name: "SV",
	is_superadmin: true,
};

export const SID = "0123456789abcdef0123456789abcdef";

export function rt(n = 1, sid = SID): string {
	return `rt_${sid}_secret${n}`;
}

export function tokenResponse(
	over: Partial<TokenResponse> & { user?: TokenUser; tenant?: TokenTenant | null } = {},
	n = 1,
): TokenResponse {
	const tenant = over.tenant === undefined ? SHREE : over.tenant;
	return {
		access_token: `at_${n}`,
		token_type: "bearer",
		expires_in: 900,
		refresh_token: rt(n),
		refresh_expires_at: "2026-10-30T10:00:00Z",
		user: PRIYA,
		tenants: tenant ? [tenant] : [],
		...over,
		tenant,
	};
}

export const BRANDING: PublicBranding = {
	product_name: "Sahayak AI",
	logo_url: null,
	primary_color: "#0b7a5f",
	support_email: "support@sahayak.ai",
	contact_text:
		"Sahayak AI is set up personally for each clinic, school and store. Message us and we'll set you up the same day.",
	contact_url: "https://wa.me/919822012345",
	signup_mode: "approval",
	powered_by: { text: "cob-rag-engine by Callobuzz", url: null },
	accounts_enabled: true,
	password_policy: { min_length: 10, max_length: 128 },
	signup_available: true,
	special_use_emails_allowed: false,
	cob_public: true,
};

export function corpus(id: string, name: string, tenant: TokenTenant): Corpus {
	return {
		id,
		name,
		tenant_id: tenant.id,
		tenant_name: tenant.name,
		description: null,
		qdrant_collection: `corpus_${id.slice(0, 8)}`,
		created_at: "2026-09-20T10:00:00Z",
		document_count: 12,
		chunk_count: 164,
		last_activity_at: null,
	};
}

export const CORPORA: Corpus[] = [
	corpus("7b221003-0000-4000-8000-000000000001", "Patient help", SHREE),
	corpus("7b2210a9-0000-4000-8000-000000000002", "Staff SOPs", SHREE),
	corpus("3e81c4d0-0000-4000-8000-000000000003", "Store assistant", RANGOLI),
	corpus("91f0e6b2-0000-4000-8000-000000000004", "Admissions help", {
		id: "33333333-3333-4333-8333-333333333333",
		name: "Vidya Classes",
		role: "owner",
		status: "active",
		is_operator: false,
	}),
];
