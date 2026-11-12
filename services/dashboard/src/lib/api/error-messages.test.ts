import { describe, expect, it } from "vitest";
import { formatClock, formatDuration, MESSAGE_CODES, messageFor, weakPasswordReason } from "./error-messages";
import { ApiError } from "./errors";

/** Copied from docs/internal/plans/epics/accounts/README.md ("Code catalog"), 49 codes. */
const ACCOUNTS_CATALOG = [
	"review_exists",
	"version_not_active",
	"wrong_workspace_kind",
	"workspace_locked",
	"nothing_to_finalize",
	"finalize_failed",
	"tenant_required",
	"auth_required",
	"invalid_api_key",
	"invalid_token",
	"token_expired",
	"session_revoked",
	"refresh_token_invalid",
	"refresh_token_reused",
	"accounts_not_configured",
	"invalid_credentials",
	"email_not_verified",
	"no_workspace",
	"account_locked",
	"rate_limited",
	"rate_limiter_unavailable",
	"invalid_code",
	"code_expired",
	"weak_password",
	"session_required",
	"not_member",
	"email_not_configured",
	"email_unavailable",
	"smtp_test_failed",
	"signup_closed",
	"workspace_not_active",
	"invalid_status_transition",
	"confirm_mismatch",
	"owner_required",
	"last_owner",
	"already_member",
	"invitation_invalid",
	"invitation_email_mismatch",
	"account_exists",
	"superadmin_email",
	"plan_not_found",
	"plan_inactive",
	"plan_in_use",
	"plan_is_default",
	"plan_name_taken",
	"kb_limit_reached",
	"document_limit_reached",
	"widget_key_limit_reached",
	"question_cap_reached",
];

/** The conversation-layer codes the dashboard shows (C2 raises them; C4-C8 add their own with their screens). */
const CONVERSATION_CATALOG = [
	"assistant_not_found",
	"assistant_draft_missing",
	"assistant_draft_conflict",
	"assistant_version_not_found",
	"assistant_rule_limit",
	"assistant_rule_budget",
	"assistant_prompt_budget",
	"assistant_reference_invalid",
	"assistant_schema_unsupported",
	"template_not_found",
	"encryption_not_configured",
	"system_document",
	"assistant_disabled",
	"assistant_llm_unavailable",
	"assistant_llm_failed",
	"golden_set_not_found",
	"golden_set_limit",
	"golden_set_is_default",
	"eval_run_not_found",
	"eval_run_in_progress",
	"eval_cost_unconfirmed",
	"eval_compare_invalid",
	"eval_criterion_not_found",
	"eval_criterion_exists",
	"eval_sample_not_found",
	"eval_judge_unavailable",
	"eval_judge_key_required",
	"eval_settings_invalid",
	"eval_thresholds_invalid",
	"review_item_not_found",
	"review_payload_required",
	"review_base_revision_required",
	"setup_session_not_found",
	"setup_step_invalid",
	"lead_not_found",
	"export_too_large",
	"lead_field_invalid",
	"lead_invalid_phone",
	"lead_invalid_email",
	"lead_consent_required",
	"leads_disabled",
	"widget_session_invalid",
	"lead_rate_limited",
	"withdraw_token_invalid",
	"webhook_event_not_allowed",
	"webhook_url_not_allowed",
	"webhook_not_found",
	"handoff_not_found",
	"handoff_disabled",
	"handoff_channel_unavailable",
	"callback_window_invalid",
	"handoff_rate_limited",
];

/** The COB catalog (docs/internal/plans/epics/cob/README.md, "Error codes"), plus the reused conversation code. */
const COB_CATALOG = [
	"system_resource_protected",
	"cob_docs_syncing",
	"cob_disabled",
	"cob_proposal_not_found",
	"cob_proposal_used",
	"cob_proposal_expired",
	"cob_proposal_scope_changed",
	"job_not_retryable",
	"cob_public_disabled",
	"cob_public_rate_limited",
	"cob_public_unavailable",
	"docs_page_not_found",
	"docs_search_unavailable",
	"conversation_not_found",
];

const err = (code: string, extra: Record<string, unknown> = {}, retryAfterS: number | null = null) =>
	new ApiError({ status: 400, code, detail: "server detail", extra, retryAfterS });

describe("messageFor", () => {
	it("has an entry for every accounts catalog code plus network", () => {
		expect(ACCOUNTS_CATALOG).toHaveLength(49);
		for (const code of [...ACCOUNTS_CATALOG, "network"]) {
			expect(MESSAGE_CODES, code).toContain(code);
			const m = messageFor(err(code));
			expect(m.title.length, code).toBeGreaterThan(0);
		}
	});

	it("every conversation-layer code has a plain message and never says corpus", () => {
		for (const code of CONVERSATION_CATALOG) {
			expect(MESSAGE_CODES, code).toContain(code);
			const m = messageFor(err(code));
			expect(m.title.length, code).toBeGreaterThan(0);
			expect(`${m.title} ${m.body ?? ""}`.toLowerCase(), code).not.toContain("corpus");
		}
	});

	it("every COB code has a plain sentence and avoids the engine's internal words", () => {
		for (const code of COB_CATALOG) {
			expect(MESSAGE_CODES, code).toContain(code);
			const m = messageFor(err(code));
			expect(m.title.length, code).toBeGreaterThan(0);
			const text = `${m.title} ${m.body ?? ""}`.toLowerCase();
			for (const word of ["corpus", "tenant", "director", "audience"]) expect(text, code).not.toContain(word);
		}
	});

	it("the public COB limit says whether it was the day or the hour", () => {
		expect(messageFor(err("cob_public_rate_limited", { scope: "daily" })).title).toContain("tomorrow");
		expect(messageFor(err("cob_public_rate_limited", { scope: "ip" }, 600)).title).toContain("10 minutes");
	});

	it("rule limit, rule budget and prompt budget name the numbers", () => {
		expect(messageFor(err("assistant_rule_limit", { limit: 30, enabled: 32 })).title).toBe(
			"Only 30 guidance rules can be on at once (32 are on). Turn some off first.",
		);
		expect(messageFor(err("assistant_rule_budget", { budget: 1200, used: 1450 })).title).toContain("1,450");
		expect(messageFor(err("assistant_prompt_budget", { budget: 3000, used: 4100 })).title).toContain("4,100");
	});

	it("lead errors name what to do, and an oversized export names its limit", () => {
		expect(messageFor(err("export_too_large", { limit: 50000 })).title).toBe(
			"Too many leads to export at once (limit 50,000). Narrow the dates or status.",
		);
		expect(messageFor(err("lead_field_invalid", { field: "assignee_user_id", reason: "not_member" })).title).toBe(
			"Pick someone from this workspace.",
		);
		expect(messageFor(err("lead_field_invalid", { field: "status", reason: "not_an_option" })).title).toBe(
			"Please check this field.",
		);
		expect(messageFor(err("lead_rate_limited", {}, 600)).title).toBe("Too many tries. Please try again in 10 minutes.");
		expect(messageFor(err("withdraw_token_invalid")).body).toContain("Contact the business");
	});

	it("a full golden set names its limit", () => {
		expect(messageFor(err("golden_set_limit", { limit: 200 })).title).toBe("A golden set holds at most 200 questions.");
	});

	it("a draft conflict names nobody", () => {
		expect(messageFor(err("assistant_draft_conflict", { current_revision: 9 })).title).toBe(
			"Someone else saved this draft a moment ago. Load their version, then make your change again.",
		);
	});

	it("explains the operator-workspace codes in plain words", () => {
		for (const code of [
			"operator_workspace_protected",
			"operator_confirmation_required",
			"plan_not_assignable",
			"plan_internal_locked",
			"not_superadmin",
		]) {
			expect(MESSAGE_CODES, code).toContain(code);
		}
		expect(messageFor(err("operator_workspace_protected")).title).toBe("Your own workspace can't be suspended.");
		expect(messageFor(err("operator_confirmation_required")).title).toContain("confirm");
		expect(messageFor(err("plan_not_assignable")).title).toContain("Internal plan");
		expect(messageFor(err("plan_internal_locked")).title).toContain("can't be changed or deleted");
		expect(messageFor(err("not_superadmin")).title).toContain("admin account");
	});

	it("network names the engine URL and the compose hint", () => {
		const m = messageFor(new ApiError({ status: 0, code: "network", detail: "" }), { ragUrl: "http://127.0.0.1:8000" });
		expect(m.title).toContain("http://127.0.0.1:8000");
		expect(m.body).toContain("docker compose ps");
		expect(m.body).toContain("RAG_CORS_ORIGINS");
	});

	it("weak_password lists every reason with the policy numbers", () => {
		const m = messageFor(err("weak_password", { reasons: ["too_short", "common", "contains_email", "too_long"] }), {
			policy: { min_length: 12, max_length: 64 },
		});
		expect(m.body).toContain("at least 12 characters");
		expect(m.body).toContain("at most 64 characters");
		expect(m.body).toContain("too common");
		expect(m.body).toContain("email name");
		expect(weakPasswordReason("too_short")).toBe("Use at least 10 characters.");
	});

	it("uses the live {retry} text for lockouts and rate limits", () => {
		expect(messageFor(err("account_locked"), { retry: "45 seconds" }).title).toBe(
			"Too many tries. Login is paused for 45 seconds.",
		);
		expect(messageFor(err("rate_limited", {}, 120)).title).toBe("Too many attempts. Try again in 2 minutes.");
	});

	it("signup_closed shows the operator detail and contact link", () => {
		const m = messageFor(
			new ApiError({
				status: 403,
				code: "signup_closed",
				detail: "Contact us for pricing.",
				extra: { contact_url: "https://wa.me/91" },
			}),
		);
		expect(m.title).toBe("Contact us for pricing.");
		expect(m.action?.to).toBe("https://wa.me/91");
	});

	it("falls back by status for uncoded errors", () => {
		expect(messageFor(new ApiError({ status: 403, code: null, detail: "" })).title).toBe(
			"You don't have access to this.",
		);
		expect(messageFor(new ApiError({ status: 404, code: null, detail: "" })).title).toContain("Not found");
		expect(messageFor(new ApiError({ status: 409, code: null, detail: "Name taken" })).title).toBe("Name taken");
		expect(messageFor(new ApiError({ status: 503, code: null, detail: "" })).title).toContain("(503)");
	});

	it("question_cap_reached uses the support e-mail and an en-IN date", () => {
		const m = messageFor(err("question_cap_reached", { used: 1000, limit: 1000, resets_at: "2026-10-01T00:00:00Z" }), {
			support: "support@sahayak.ai",
		});
		expect(m.title).toContain("1000 of 1000");
		expect(m.title).toContain("Oct 2026");
		expect(m.title).toContain("support@sahayak.ai");
	});
});

describe("formatDuration / formatClock", () => {
	it("formats countdowns", () => {
		expect(formatDuration(1)).toBe("1 second");
		expect(formatDuration(45)).toBe("45 seconds");
		expect(formatDuration(60)).toBe("1 minute");
		expect(formatDuration(61)).toBe("2 minutes");
		expect(formatDuration(900)).toBe("15 minutes");
		expect(formatClock(42)).toBe("0:42");
		expect(formatClock(60)).toBe("1:00");
	});
});

// OS3
describe("no licence messages", () => {
	it("has no licence_ code, and WhatsApp's public address refusal says what the engine says", () => {
		expect(MESSAGE_CODES.filter((k) => k.startsWith("license_"))).toEqual([]);
		const m = messageFor(err("whatsapp_needs_public_address"), { admin: true });
		expect(m.title).toBe("server detail");
	});
});
// /OS3

// L5a demo
describe("demo refusals", () => {
	it("the four demo codes say what the engine says", () => {
		expect(messageFor(err("demo_read_only", {})).title).toBe(
			"This is a live demo, so this action is switched off. Everything else works, and the demo is reset every night.",
		);
		expect(messageFor(err("demo_mode_off", {})).title).toBe("Demo mode is off on this server.");
		expect(messageFor(err("demo_reset_running", {})).title).toBe(
			"A demo reset is already running. Try again in a few minutes.",
		);
		expect(messageFor(err("demo_unavailable", {})).title).toBe(
			"The demo reset cannot start right now because the queue server did not answer. Try again shortly.",
		);
	});
});
// /L5a demo

// LP1 landing
const LANDING_CATALOG = [
	"landing_revision_conflict",
	"landing_translation_missing",
	"landing_needs_address",
	"landing_address_invalid",
	"landing_nothing_published",
	"landing_asset_missing",
	"landing_asset_in_use",
	"landing_asset_not_found",
	"landing_asset_too_large",
	"landing_asset_type",
	"landing_storage_unavailable",
	"landing_version_not_found",
	"landing_preset_not_found",
	"landing_generate_unavailable",
	"landing_generate_no_content",
	"landing_generate_failed",
	"landing_not_operator_kb",
	"landing_contact_rate_limited",
	"landing_contact_unavailable",
	"landing_contact_invalid",
];

describe("the public website's errors", () => {
	it("every code has a plain sentence and avoids the engine's internal words", () => {
		for (const code of LANDING_CATALOG) {
			expect(MESSAGE_CODES, code).toContain(code);
			const m = messageFor(err(code, {}, 30));
			expect(m.title.length, code).toBeGreaterThan(10);
			expect(m.title, code).not.toMatch(/corpus|tenant|ledger|slug/i);
		}
	});

	it("a stale save and a missing language say what to do", () => {
		expect(messageFor(err("landing_revision_conflict")).title).toBe(
			"Someone saved this website in another tab. Reload to see their changes.",
		);
		expect(messageFor(err("landing_translation_missing")).title).toBe("server detail");
	});

	it("an oversized file repeats the limits the server named", () => {
		const e = new ApiError({
			status: 413,
			code: "landing_asset_too_large",
			detail: "That file is too large. Videos can be up to 25 MB, pictures up to 5 MB.",
			extra: {},
			retryAfterS: null,
		});
		expect(messageFor(e).title).toContain("25 MB");
	});

	it("the contact limit shows the wait", () => {
		const m = messageFor(err("landing_contact_rate_limited", {}, 90), { retry: "2 minutes" });
		expect(m.title).toContain("2 minutes");
		expect(m.title).toContain("WhatsApp");
	});
});
// /LP1 landing
