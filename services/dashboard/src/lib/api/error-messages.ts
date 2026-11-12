import { assistantStrings } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import { legalStrings, missingName } from "@/lib/strings/legal"; // P3 legal
import { onboardingStrings } from "@/lib/strings/onboarding"; // BIZ4
import { fairUseStrings } from "@/lib/strings/pipelines"; // PIP4
import { portalStrings } from "@/lib/strings/portal";
import { setupStrings } from "@/lib/strings/setup";
import type { ApiError } from "./errors";

const portal = portalStrings.errors;
const a = assistantStrings.errors;
const formatNumber = (n: number) => new Intl.NumberFormat("en-IN").format(n);

/**
 * Error code → plain-English message (README "Error code → UI message map"), the single source.
 * error-messages.test.ts asserts that every code in the accounts catalog
 * (docs/internal/plans/epics/accounts/README.md) plus `network` has an entry.
 */

export interface MessageContext {
	/** Engine URL, for `network`. */
	ragUrl?: string;
	/** Branding support e-mail; "your provider" when unset. */
	support?: string | null;
	/** Human text for the Retry-After countdown, e.g. "45 seconds". */
	retry?: string;
	/** Admin variant (e.g. email_not_configured adds the RAG_SMTP_* hint). */
	admin?: boolean;
	/** Password policy for weak_password reasons. */
	policy?: { min_length: number; max_length: number };
	/** Tenant name for workspace_not_active (suspended). */
	tenant?: string;
}

export interface UiMessage {
	title: string;
	body?: string;
	action?: { label: string; to?: string; id?: string };
}

type Builder = (e: ApiError, c: MessageContext) => UiMessage;

const str = (v: unknown, fallback = ""): string =>
	typeof v === "string" || typeof v === "number" ? String(v) : fallback;
const retryText = (e: ApiError, c: MessageContext): string =>
	c.retry ?? (e.retryAfterS != null ? formatDuration(e.retryAfterS) : "a moment");

export const WEAK_PASSWORD_REASONS = ["too_short", "too_long", "common", "contains_email"] as const;

export function weakPasswordReason(reason: string, policy?: { min_length: number; max_length: number }): string {
	const min = policy?.min_length ?? 10;
	const max = policy?.max_length ?? 128;
	switch (reason) {
		case "too_short":
			return `Use at least ${min} characters.`;
		case "too_long":
			return `Use at most ${max} characters.`;
		case "common":
			return "That password is too common.";
		case "contains_email":
			return "Don't use your email name in the password.";
		default:
			return "Choose a different password.";
	}
}

const TABLE: Record<string, Builder> = {
	network: (_e, c) => ({
		title: `Can't reach the engine at ${c.ragUrl ?? "the configured address"}.`,
		body: "Check it is running (docker compose ps) and that RAG_CORS_ORIGINS includes this dashboard.",
	}),
	auth_required: () => ({ title: "Please sign in." }),
	invalid_api_key: () => ({ title: "That key was not accepted.", body: "Check for a missing character." }),
	invalid_token: () => ({ title: "You were signed out. Please log in again." }),
	session_revoked: () => ({ title: "You were signed out. Please log in again." }),
	token_expired: () => ({ title: "Your session is being renewed." }),
	refresh_token_invalid: () => ({ title: "Your session ended. Please log in again." }),
	refresh_token_reused: () => ({
		title: "For your safety this session was ended because it was used from two places. Log in again.",
	}),
	accounts_not_configured: () => ({
		title: "Email sign-in isn't set up on this engine yet. Use an API key instead.",
		action: { label: "Use an API key", to: "/login/key" },
	}),
	invalid_credentials: () => ({
		title: "Email or password isn't right.",
		body: "Check both and try again, or reset your password.",
	}),
	email_not_verified: () => ({
		title: "Verify your email first.",
		action: { label: "Send a new code", to: "/verify" },
	}),
	no_workspace: () => ({
		title: "Your account isn't part of any workspace. Ask your team owner to invite you again.",
	}),
	account_locked: (e, c) => ({ title: `Too many tries. Login is paused for ${retryText(e, c)}.` }),
	rate_limited: (e, c) => ({ title: `Too many attempts. Try again in ${retryText(e, c)}.` }),
	rate_limiter_unavailable: () => ({ title: "Sign-in is briefly unavailable. Try again in a minute." }),
	invalid_code: () => ({ title: "That code isn't right. Check the latest email." }),
	code_expired: () => ({
		title: "That code has expired or was used up.",
		action: { label: "Send a new code", id: "resend" },
	}),
	weak_password: (e, c) => {
		const reasons = Array.isArray(e.extra.reasons) ? e.extra.reasons.map(String) : [];
		return {
			title: "Choose a stronger password.",
			body: reasons.map((r) => weakPasswordReason(r, c.policy)).join(" ") || undefined,
		};
	},
	session_required: () => ({ title: "This needs an email sign-in; you're using an API key." }),
	not_member: () => ({ title: "You're not a member of that workspace." }),
	email_not_configured: (_e, c) => ({
		title: "Email isn't set up on this engine, so codes and invitations can't be sent.",
		body: c.admin ? "Set RAG_SMTP_* in .env and restart the engine." : undefined,
	}),
	email_unavailable: () => ({ title: "We couldn't queue the email. Try again shortly." }),
	smtp_test_failed: (e) => ({
		title: `The mail server refused the test (${[str(e.extra.smtp_code), str(e.extra.error)].filter(Boolean).join(" ")}).`,
	}),
	signup_closed: (e) => ({
		title: e.detail || "Sign-up is closed.",
		action:
			typeof e.extra.contact_url === "string" && e.extra.contact_url
				? { label: "Contact us", to: e.extra.contact_url }
				: undefined,
	}),
	// P1 payments
	payments_disabled: () => ({ title: "Online payment is not switched on here." }),
	payments_signature_invalid: () => ({ title: "We could not confirm that payment. Nothing was added." }),
	payments_test_failed: (e) => ({
		title:
			e.extra.reason === "auth"
				? "Razorpay did not accept these keys."
				: e.extra.reason === "not_configured"
					? "Save your key id and secret first."
					: "We could not reach Razorpay. Try again in a moment.",
	}),
	payments_test_too_soon: (e, c) => ({ title: `Please wait ${retryText(e, c)} before testing again.` }),
	payments_currency_unsupported: () => ({
		title: "Online payment is in rupees only for now. Please ask your provider to add money for you.",
	}),
	payments_unavailable: () => ({
		title: "We could not reach the payment service. Nothing was charged. Please try again.",
	}),
	tax_details_required: () => ({
		title: "Please add your billing name and address first, so we can make your tax invoice.",
	}),
	invalid_gstin: () => ({ title: "That GST number doesn't look right. Please check it." }),
	gstin_state_mismatch: () => ({
		title: "This GST number belongs to another state than your company's. Check the company details.",
	}),
	invalid_key_id: () => ({ title: "That doesn't look like a Razorpay key id. It starts with rzp_test_ or rzp_live_." }),
	amount_out_of_range: (e) => ({
		title: e.detail || "That amount is outside what can be paid online.",
	}),
	order_not_found: () => ({ title: "That payment does not exist." }),
	order_not_payable: () => ({ title: "That payment was already made or has expired. Start a new one." }),
	plan_has_no_fee: () => ({ title: "Your plan has no monthly fee, so there is nothing to pay." }),
	add_on_not_sellable: () => ({ title: "That add-on is not available to buy." }),
	payment_not_found: () => ({ title: "That payment does not exist." }),
	payment_event_not_found: () => ({ title: "That delivery does not exist." }),
	payment_event_not_retryable: () => ({ title: "That delivery was already processed, so there is nothing to retry." }),
	refund_exceeds_refundable: (e) => ({
		title: e.detail || "That is more than can be refunded from this payment.",
	}),
	refund_window_closed: (e) => ({
		title: e.detail || "This payment is too old to refund through the payment service.",
	}),
	refund_failed: () => ({
		title: "The payment service could not send the refund. The amount was put back on the balance.",
	}),
	// /P1 payments
	// P2 invoices
	tax_document_not_found: () => ({ title: "That tax invoice does not exist." }),
	tax_document_pdf_pending: () => ({
		title: "The invoice is still being prepared. Please try again in a few seconds.",
	}),
	invoice_resend_too_soon: (e, c) => ({
		title: `That invoice was sent a moment ago. Try again in ${retryText(e, c)}.`,
	}),
	range_too_long: () => ({ title: "Pick a range of at most 366 days." }),
	// /P2 invoices
	// P5 plans
	byok_not_in_plan: (_e, c) => ({
		title: `Using your own AI key isn't part of your plan. It's included in Pro, or ask ${c.support ?? "your provider"} about the add-on.`,
	}),
	feature_already_included: () => ({ title: "Your plan already includes this, so there is nothing to buy." }),
	add_on_not_renewing: () => ({ title: "That purchase does not renew, so there is nothing to cancel." }),
	service_order_not_found: () => ({ title: "That order does not exist." }),
	invalid_service_order_transition: () => ({ title: "That order cannot move to that step from where it is." }),
	// /P5 plans
	// LP1 landing: the public website's editor, uploads and contact form
	landing_revision_conflict: () => ({
		title: "Someone saved this website in another tab. Reload to see their changes.",
	}),
	landing_translation_missing: (e) => ({
		title: e.detail || "Some texts are missing in one of your languages. Fill them in or switch that language off.",
	}),
	landing_needs_address: () => ({
		title: "Set your website's address (or RAG_PUBLIC_URL) before switching the site on.",
	}),
	landing_address_invalid: () => ({
		title: "Use your website's address like https://www.example.com, with no page after it.",
	}),
	landing_nothing_published: () => ({ title: "Publish your website once before switching it on." }),
	landing_asset_missing: () => ({
		title: "A picture or video this page uses was deleted. Choose another one.",
	}),
	landing_asset_in_use: () => ({
		title: "This file is used on your website. Remove it from the page first.",
	}),
	landing_asset_not_found: () => ({ title: "That file was not found." }),
	landing_asset_too_large: (e) => ({
		title: e.detail || "That file is too large. Videos can be up to 25 MB, pictures up to 5 MB.",
	}),
	landing_asset_type: () => ({ title: "Use an MP4 or WebM video, or a PNG, JPEG or WebP picture." }),
	landing_storage_unavailable: () => ({
		title: "File storage is unavailable, so nothing was saved. Try again shortly.",
	}),
	landing_version_not_found: () => ({ title: "That version does not exist." }),
	landing_preset_not_found: () => ({ title: "That starting example does not exist." }),
	landing_generate_unavailable: () => ({
		title: "No AI model is set up for this knowledge base, so a conversation can't be written.",
	}),
	landing_generate_no_content: () => ({
		title: "This knowledge base has nothing to read yet. Add your website or a brochure first.",
	}),
	landing_generate_failed: () => ({ title: "The AI model did not answer. Try again." }),
	landing_not_operator_kb: () => ({
		title: "Choose one of your own knowledge bases (not a customer's).",
	}),
	landing_contact_rate_limited: (e, c) => ({
		title: `You have sent a few messages already. Please wait ${retryText(e, c)}, or reach us on WhatsApp.`,
	}),
	landing_contact_unavailable: () => ({
		title: "The form is not working right now. Please e-mail or WhatsApp us instead.",
	}),
	landing_contact_invalid: () => ({ title: "Please check the form and try again." }),
	// /LP1 landing
	// P3 legal
	legal_page_not_found: () => ({ title: legalStrings.errors.notFound }),
	legal_draft_conflict: () => ({ title: legalStrings.errors.draftConflict }),
	legal_review_required: () => ({ title: legalStrings.errors.reviewRequired }),
	legal_placeholders_missing: (e) => ({
		title: legalStrings.errors.placeholdersMissing.replace(
			"{missing}",
			(Array.isArray(e.extra.missing) ? e.extra.missing : []).map((n) => missingName(String(n))).join(", "),
		),
	}),
	legal_acceptance_required: () => ({ title: legalStrings.errors.acceptanceRequired }),
	legal_version_changed: () => ({ title: legalStrings.errors.versionChanged }),
	// /P3 legal
	// TV4 value
	value_month_invalid: (e) => ({
		title: e.detail || "That month isn't available for the value report.",
	}),
	// /TV4 value
	// TV3 no surprises
	price_change_notice_too_short: (e) => ({
		title: e.detail || "That date is too soon: businesses must be told before a higher price starts.",
	}),
	price_change_not_lower: (e) => ({
		title: e.detail || "This change makes some businesses pay more, so it has to be scheduled.",
	}),
	scheduled_pricing_not_found: () => ({ title: "There is no scheduled price change for this plan any more." }),
	pending_change_not_found: () => ({ title: "This business has no pending change any more." }),
	agreement_reason_required: (e) => ({
		title: e.detail || "Write at least 10 characters about how the business asked for this.",
	}),
	spend_cap_too_high: (e) => ({
		title: e.detail || "That is above the highest cap your provider allows.",
	}),
	spend_cap_not_billed: () => ({ title: "Your plan does not charge for usage, so there is nothing to cap." }),
	support_disabled: () => ({
		title: "Support requests are switched off. Please use the contact details your provider gave you.",
	}),
	support_request_not_found: () => ({ title: "That support request does not exist." }),
	support_rate_limited: (e) => ({
		title: e.detail || "You have sent the most requests allowed in a day. Add to an open request, or try again later.",
	}),
	price_sheet_not_found: () => ({ title: "That price list link is not valid." }),
	// /TV3 no surprises
	// TV2 reference
	reference_invalid: (e) => ({ title: e.detail || "That doesn't look like a reference. Type it like SD-4F7K." }),
	reference_not_found: (e) => ({
		title: e.detail || "No conversation with that reference in your knowledge bases.",
	}),
	// /TV2 reference
	workspace_not_active: (e, c) =>
		e.extra.status === "suspended"
			? { title: `${c.tenant ?? "This workspace"} is paused` }
			: { title: "We'll activate your workspace soon" },
	invalid_status_transition: (e) => ({
		title: `That change isn't possible from the current status (${str(e.extra.from, "?")} → ${str(e.extra.to, "?")}). Refresh and try again.`,
	}),
	confirm_mismatch: () => ({ title: "The name doesn't match exactly." }),
	owner_required: () => ({ title: "Only workspace owners can do this. Ask an owner." }),
	key_permission_denied: (e) => ({
		title: `This key can't do that: it has ${str(e.extra.permission, "limited")} permission and this needs ${str(e.extra.required, "more")}.`,
		body: "Use a key with more permission, or raise this key's permission on its knowledge base's Keys tab.",
	}),
	key_not_found: () => ({ title: "That key no longer exists. Refresh the list." }),
	key_limit_reached: (e) => ({
		title: `A key holder can have at most ${str(e.extra.limit, "50")} keys. Revoke one you no longer use.`,
	}),
	key_name_required: () => ({ title: "Give the key a name." }),
	invalid_permission: () => ({ title: "Choose Read, Write or Full." }),
	confirmation_required: (e) => ({
		title: "Deleting with a key needs the exact name.",
		body: typeof e.extra.confirm_name === "string" ? `Type "${e.extra.confirm_name}" to confirm.` : undefined,
	}),
	last_owner: () => ({
		title: "A workspace always keeps at least one owner. Make someone else an owner first.",
	}),
	already_member: () => ({ title: "That person is already in this workspace." }),
	invitation_invalid: () => ({ title: "This invitation link has expired or was already used. Ask for a new one." }),
	invitation_email_mismatch: (e) => ({
		title: `This invitation is for ${str(e.extra.email, "someone else")}. Log out and log in as that person.`,
	}),
	account_exists: () => ({ title: "You already have an account. Log in to accept." }),
	superadmin_email: () => ({ title: "That address belongs to the operator's admin account." }),
	plan_not_found: () => ({ title: "Plan not found." }),
	plan_inactive: () => ({ title: "That plan is switched off; pick another." }),
	plan_in_use: (e) => ({ title: `${str(e.extra.tenant_count, "Some")} customers use this plan; move them first.` }),
	plan_is_default: () => ({ title: "The default plan can't be deleted or switched off." }),
	plan_name_taken: () => ({ title: "A plan with that name exists." }),
	operator_workspace_protected: () => ({
		title: "Your own workspace can't be suspended.",
		body: "It is the account you run this engine from. You can still delete it, with an extra confirmation.",
	}),
	operator_confirmation_required: () => ({
		title: "Tick the box to confirm you want to delete your own workspace.",
	}),
	plan_not_assignable: () => ({
		title: "The Internal plan only belongs to your own workspace. Pick another plan.",
	}),
	plan_internal_locked: () => ({
		title: "The Internal plan is built in and can't be changed or deleted.",
	}),
	not_superadmin: () => ({ title: "Only the operator's admin account can switch views." }),
	kb_limit_reached: (e) => ({
		title: `Your plan allows ${str(e.extra.limit, "a limited number of")} knowledge bases.`,
		action: { label: "See plan", to: "/plan" },
	}),
	document_limit_reached: (e) => ({
		title: `Your plan allows ${str(e.extra.limit, "a limited number of")} documents (${str(e.extra.used, "?")} used; this adds ${str(e.extra.adding, "?")}).`,
	}),
	widget_key_limit_reached: (e) => ({
		title: `Your plan allows ${str(e.extra.limit, "a limited number of")} website widget keys.`,
		action: { label: "See plan", to: "/plan" },
	}),
	question_cap_reached: (e, c) => {
		const resets = typeof e.extra.resets_at === "string" ? new Date(e.extra.resets_at) : null;
		const when =
			resets && !Number.isNaN(resets.getTime())
				? resets.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })
				: "the start of next month";
		return {
			title: `Monthly question limit reached (${str(e.extra.used, "?")} of ${str(e.extra.limit, "?")}). It resets on ${when}. Contact ${c.support || "your provider"}.`,
		};
	},
	tenant_required: () => ({ title: "Pick the customer this knowledge base belongs to." }),
	// BIL1 providers
	provider_unknown: () => ({ title: "That provider is not one the engine tracks. Reload the page and try again." }),
	provider_refresh_too_soon: () => ({
		title: "Try again in a minute. Providers can be refreshed once a minute.",
	}),
	// The engine says which rule was broken ("A manual balance has no key"); show that.
	invalid_provider_settings: (e) => ({
		title: e.detail || "Those provider settings do not fit together. Check the balance, the date and the key.",
	}),
	// /BIL1 providers
	// BIL2: every billing error code (BIL3 to BIL6 use theirs without touching this file)
	billing_disabled: () => ({ title: "Billing is turned off on this engine." }),
	tenant_not_found: () => ({ title: "That customer no longer exists. Go back to the list and pick another." }),
	pricing_locked: () => ({
		title: "The Internal plan is your own workspace's and is never billed, so it has no pricing.",
	}),
	currency_locked: () => ({
		title: "This customer already has transactions, so the currency can no longer change.",
	}),
	currency_mismatch: (e) => ({
		title: "That does not match the currency of the customer's balance.",
		body: e.extra.account_currency
			? `Their balance is in ${str(e.extra.account_currency)}. Use a plan and prices in ${str(e.extra.account_currency)}.`
			: "Customers keep one currency once they have transactions.",
	}),
	billing_exempt: () => ({
		title: "This workspace is never billed, so only its notes can be edited.",
	}),
	idempotency_conflict: () => ({
		title: "That entry was already recorded with a different amount. Reload and check before trying again.",
	}),
	refund_exceeds_balance: () => ({ title: "A refund cannot be larger than the customer's available balance." }),
	billing_paused: () => ({
		title: "Answers are paused because the balance has run out.",
		body: "Top up the balance, or change the pause setting on the customer's Billing tab.",
	}),
	add_on_not_found: () => ({ title: "That add-on no longer exists." }),
	add_on_name_taken: () => ({ title: "Another add-on already has that name." }),
	add_on_inactive: () => ({ title: "That add-on is switched off, so it cannot be applied." }),
	add_on_in_use: () => ({
		title: "This add-on has been given to customers, so it cannot be deleted. Switch it off instead.",
	}),
	insufficient_balance: () => ({ title: "The balance is too low to pay for that add-on." }),
	// P6 plan choice
	plan_change_pending_from_provider: () => ({
		title: "A change your provider set up is waiting to start.",
		body: "Please contact them to change your plan.",
	}),
	plan_change_needs_billing: () => ({
		title: "This plan has a fee, and payments are not set up here.",
		body: "Please contact us to move to it.",
	}),
	plan_not_ready: () => ({ title: "This plan's price is not set yet. Please contact us." }),
	plan_change_renewal_due: () => ({
		title: "Your plan is renewing right now.",
		body: "Please try again in an hour.",
	}),
	// /P6 plan choice
	grant_not_found: () => ({ title: "That add-on grant was not found." }),
	grant_already_revoked: () => ({ title: "That add-on was already removed." }),
	grant_not_revocable: () => ({
		title: "A credit pack cannot be removed. Correct the balance with an adjustment instead.",
	}),
	statement_not_found: () => ({ title: "There is no statement for that month." }),
	topup_request_too_soon: (e, c) => ({
		title: `You asked for a top-up a moment ago. Try again in ${retryText(e, c)}.`,
	}),
	// /BIL2
	// PIP4: pipeline profiles, answer lanes, fair use and the local model
	profile_not_found: () => ({ title: "That pipeline profile no longer exists. Reload the page and try again." }),
	profile_name_taken: () => ({ title: "Another pipeline profile already has that name." }),
	profile_is_default: () => ({
		title: "This is the default profile, so it can't be deleted.",
		body: "Make another profile the default first.",
	}),
	profile_in_use: (e) => {
		const plans = Number(e.extra.plans ?? 0);
		const tenants = Number(e.extra.tenants ?? 0);
		const parts = [
			plans > 0 ? `${plans} plan${plans === 1 ? "" : "s"}` : null,
			tenants > 0 ? `${tenants} business${tenants === 1 ? "" : "es"}` : null,
		].filter(Boolean);
		return {
			title: parts.length ? `Used by ${parts.join(" and ")}.` : "Plans or businesses still use this profile.",
			body: "Move them to another profile first.",
		};
	},
	// The engine names the rule that does not fit ("A ranking service needs a model name…").
	invalid_profile: (e) => ({
		title: e.detail || "Those profile settings do not fit together. Check the highlighted fields.",
	}),
	answers_busy: () => ({
		title: "The assistant is answering as many questions as it can right now.",
		body: "Try again in a moment.",
	}),
	fair_use_limit: (e, c) => {
		const what = fairUseStrings.ownerWhat[str(e.extra.metric)] ?? "what your plan allows";
		const resets = typeof e.extra.resets_at === "string" ? new Date(e.extra.resets_at) : null;
		const when =
			resets && !Number.isNaN(resets.getTime())
				? resets.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })
				: null;
		return {
			title: `You've reached your limit for ${what}.`,
			body: when ? `It starts again on ${when}. Try again in ${retryText(e, c)}.` : `Try again in ${retryText(e, c)}.`,
		};
	},
	storage_limit_reached: (e) => ({
		title: `That upload would pass your storage limit of ${str(e.extra.limit_mb, "your plan")} MB.`,
		body: "Delete documents you no longer need, or ask for a bigger plan.",
		action: { label: "See plan", to: "/plan" },
	}),
	invalid_fair_use: (e) => ({
		title: e.detail || "Those fair-use settings do not fit together. Check the highlighted fields.",
	}),
	local_llm_unavailable: () => ({
		title: "The free local model is not ready.",
		body: "Check the Engine page: it may still be starting or downloading the model.",
	}),
	local_llm_test_too_soon: (e, c) => ({ title: `Try the test again in ${retryText(e, c)}.` }),
	// /PIP4
	// Streamed answers: a failure before the first source arrives as an `error` event with these codes.
	retrieval_failed: () => ({
		title: "The assistant couldn't search this knowledge base just now. Try again in a moment.",
	}),
	query_invalid: () => ({ title: "That question couldn't be processed. Try rewording it." }),
	// Conversation layer: assistant configuration (C2) and the preview (C3).
	assistant_not_found: () => ({ title: a.notFound }),
	assistant_draft_missing: () => ({ title: a.draftMissing }),
	assistant_draft_conflict: () => ({ title: a.draftConflict }),
	assistant_version_not_found: () => ({ title: a.versionNotFound }),
	assistant_rule_limit: (e) => ({
		title: fill(a.ruleLimit, { limit: str(e.extra.limit, "30"), enabled: str(e.extra.enabled, "more") }),
	}),
	assistant_rule_budget: (e) => ({
		title: fill(a.ruleBudget, {
			budget: formatNumber(Number(e.extra.budget ?? 1200)),
			used: formatNumber(Number(e.extra.used ?? 0)),
		}),
	}),
	assistant_prompt_budget: (e) => ({
		title: fill(a.promptBudget, {
			budget: formatNumber(Number(e.extra.budget ?? 0)),
			used: formatNumber(Number(e.extra.used ?? 0)),
		}),
	}),
	assistant_reference_invalid: (e) => ({ title: fill(a.referenceInvalid, { path: str(e.extra.path, "a setting") }) }),
	assistant_schema_unsupported: () => ({ title: a.schemaUnsupported }),
	template_not_found: () => ({ title: a.templateNotFound }),
	encryption_not_configured: (_e, c) => ({
		title: a.encryption,
		body: c.admin ? a.encryptionAdmin : a.encryptionTenant,
	}),
	system_document: () => ({ title: a.systemDocument }),
	assistant_disabled: () => ({ title: a.disabled }),
	assistant_llm_unavailable: () => ({ title: a.llmUnavailable }),
	assistant_llm_failed: () => ({ title: a.llmFailed }),
	// Guided setup (C8); the C2 publish codes and `assistant_llm_unavailable` above are shared.
	setup_session_not_found: () => ({ title: setupStrings.errors.notFound }),
	setup_step_invalid: () => ({ title: setupStrings.errors.stepInvalid }),
	// Leads, consent and knowledge-base webhooks (C4). `encryption_not_configured` is shared with C2.
	lead_not_found: () => ({ title: "This lead was deleted or is not yours to see." }),
	export_too_large: (e) => ({
		title: `Too many leads to export at once (limit ${formatNumber(Number(e.extra.limit ?? 50000))}). Narrow the dates or status.`,
	}),
	lead_field_invalid: (e) => ({
		title: e.extra.reason === "not_member" ? "Pick someone from this workspace." : "Please check this field.",
	}),
	lead_invalid_phone: () => ({ title: "Enter a valid mobile number." }),
	lead_invalid_email: () => ({ title: "Enter a valid e-mail address." }),
	lead_consent_required: () => ({ title: "Tick the box to agree before sending." }),
	leads_disabled: () => ({ title: "This assistant isn't taking details right now." }),
	widget_session_invalid: () => ({ title: "This chat has expired. Refresh the page and ask again." }),
	lead_rate_limited: (e, c) => ({ title: `Too many tries. Please try again in ${retryText(e, c)}.` }),
	withdraw_token_invalid: () => ({
		title: "This link has expired or is not valid.",
		body: "Contact the business directly to have your details removed.",
	}),
	webhook_event_not_allowed: () => ({
		title: "Lead and handoff events go only to a knowledge base's own webhooks.",
	}),
	webhook_url_not_allowed: () => ({
		title: "That address can't be used. Use an https address on the public internet.",
	}),
	// Handoffs (C5).
	handoff_not_found: () => ({ title: "This handoff no longer exists." }),
	handoff_disabled: () => ({ title: "Talking to a person is switched off for this assistant." }),
	handoff_channel_unavailable: () => ({ title: "That way of reaching the business isn't available right now." }),
	callback_window_invalid: () => ({ title: "Pick a time window in the next 14 days, ending after it starts." }),
	handoff_rate_limited: (e, c) => ({ title: `Too many tries. Please try again in ${retryText(e, c)}.` }),
	webhook_not_found: () => ({ title: "That webhook no longer exists. Refresh the list." }),
	// Golden sets, test runs, the review inbox (C7).
	golden_set_not_found: () => ({ title: "That golden set no longer exists. Refresh the list." }),
	golden_set_limit: (e) => ({
		title: `A golden set holds at most ${formatNumber(Number(e.extra.limit ?? 200))} questions.`,
	}),
	golden_set_is_default: () => ({ title: "The default golden set can't be deleted." }),
	eval_run_not_found: () => ({ title: "That run no longer exists." }),
	eval_run_in_progress: () => ({ title: "This golden set is already running. Wait for it to finish." }),
	eval_cost_unconfirmed: () => ({ title: "Confirm the estimated cost to start this run." }),
	eval_compare_invalid: () => ({ title: "Pick 2 to 4 finished runs of the same golden set." }),
	eval_criterion_not_found: () => ({ title: "That criterion no longer exists. Refresh the list." }),
	eval_criterion_exists: () => ({ title: "A criterion with that name already exists." }),
	eval_sample_not_found: () => ({ title: "That question is no longer in the run." }),
	eval_judge_unavailable: () => ({ title: "The judge model can't be reached. Check the judge settings and key." }),
	eval_judge_key_required: () => ({ title: "A custom judge address needs its own judge key." }),
	eval_settings_invalid: () => ({ title: "Those test settings aren't valid for this knowledge base." }),
	eval_thresholds_invalid: () => ({ title: "Pass marks must be between 0 and 100 %, for known scores only." }),
	review_item_not_found: () => ({ title: "That item was already handled or has expired. Refresh the inbox." }),
	review_payload_required: () => ({ title: "Fill in what to add before saving." }),
	review_base_revision_required: () => ({ title: "Reload the assistant, then try again." }),
	// Content Portal codes (D11; finalize-specific wording in features/portal/messages.ts).
	review_exists: () => ({ title: portal.review_exists }),
	version_not_active: () => ({ title: portal.version_not_active }),
	wrong_workspace_kind: () => ({ title: portal.wrong_workspace_kind }),
	workspace_locked: (e) => ({
		title: fill(portal.workspace_locked, { holder: lockHolder(e.detail) ?? portal.someone }),
	}),
	nothing_to_finalize: () => ({ title: portal.nothing_to_finalize }),
	finalize_failed: (e) => ({ title: fill(portal.finalize_failed, { stage: str(e.extra.stage, "publishing") }) }),
	document_busy: () => ({ title: portal.document_busy }),
	review_changed: () => ({ title: portal.review_changed }),
	reviewed_document_deleted: () => ({ title: portal.reviewed_document_deleted }),
	parent_chunk_derived: () => ({ title: portal.parent_chunk_derived }),
	// COB, the built-in assistant (every code of the COB catalog; COB2 adds the block once).
	system_resource_protected: () => ({
		title: "That belongs to COB, the built-in assistant, and can't be changed.",
	}),
	cob_docs_syncing: () => ({ title: "COB is reading the docs for the first time. Try again in a minute." }),
	cob_disabled: () => ({ title: "COB is switched off on this engine." }),
	cob_proposal_not_found: () => ({ title: "COB no longer has that action. Ask again." }),
	cob_proposal_used: () => ({ title: "That action was already done or cancelled." }),
	cob_proposal_expired: () => ({ title: "That action expired after 10 minutes. Ask COB again." }),
	cob_proposal_scope_changed: () => ({
		title: "Your workspace or your role changed since COB made that card. Ask again.",
	}),
	job_not_retryable: (e) => ({ title: e.detail || "That upload can't be run again." }),
	cob_public_disabled: () => ({ title: "Ask COB isn't available right now." }),
	cob_public_rate_limited: (e, c) =>
		e.extra.scope === "daily"
			? { title: "COB has answered a lot of questions today. Try again tomorrow." }
			: { title: `You've asked a lot of questions. Try again in ${retryText(e, c)}.` },
	cob_public_unavailable: () => ({ title: "COB is briefly unavailable. Try again in a minute." }),
	// COB9: platform docs
	cob_builtin_readonly: () => ({
		title: "That page comes with the engine and updates itself. You can add your own pages next to it.",
	}),
	invalid_audience: () => ({ title: "Choose who can see the page: only you, your customers or everyone." }),
	docs_page_not_found: () => ({ title: "That help page doesn't exist." }),
	docs_search_unavailable: () => ({ title: "Search is briefly unavailable. Try again in a minute." }),
	conversation_not_found: () => ({ title: "That conversation no longer exists." }),
	// BIZ3
	feature_not_in_plan: (e) => ({ title: e.detail || "This isn't included in your plan." }),
	article_not_found: () => ({ title: "That article isn't in this knowledge base any more." }),
	article_kind_changed: (e) => ({ title: e.detail || "An article can't become a question and answer." }),
	// /BIZ3
	// BIZ1
	website_learning_disabled: () => ({
		title: "Learning from your website is switched off for your plan or this knowledge base.",
	}),
	website_url_invalid: () => ({ title: "Enter your website like shreedental.in." }),
	website_blocked: () => ({
		title: "We can't reach that website from here.",
		body: "Check the address, and that the site is public.",
	}),
	website_not_allowed: () => ({
		title: "Add this website to your widget first.",
		body: "Your assistant only reads the websites you list as allowed sites of your website widget.",
	}),
	website_reread_too_soon: (e, c) => ({ title: `You can read your website again in ${retryText(e, c)}.` }),
	website_source_not_found: () => ({ title: "That website isn't in this knowledge base any more." }),
	website_page_not_found: () => ({ title: "That page isn't in this knowledge base any more." }),
	// /BIZ1
	// OS3
	whatsapp_needs_public_address: (e) => ({
		title: e.detail || "WhatsApp needs a public https address.",
	}),
	// /OS3
	// L5a demo
	demo_read_only: () => ({
		title:
			"This is a live demo, so this action is switched off. Everything else works, and the demo is reset every night.",
	}),
	demo_mode_off: () => ({ title: "Demo mode is off on this server." }),
	demo_reset_running: () => ({ title: "A demo reset is already running. Try again in a few minutes." }),
	demo_unavailable: () => ({
		title: "The demo reset cannot start right now because the queue server did not answer. Try again shortly.",
	}),
	// /L5a demo
	// TV6 whatsapp
	whatsapp_not_enabled: () => ({ title: "WhatsApp isn't set up on this engine yet. Ask your provider." }),
	whatsapp_not_in_plan: () => ({ title: "WhatsApp isn't part of your plan. Ask your provider to add it." }),
	whatsapp_not_connected: () => ({ title: "No WhatsApp number is connected yet." }),
	whatsapp_number_in_use: () => ({ title: "That number is already used for another knowledge base." }),
	whatsapp_signup_failed: (e) => ({
		title: "Meta didn't finish connecting the number.",
		body: e.extra.step ? `It stopped at: ${str(e.extra.step)}. Please try again.` : "Please try again.",
	}),
	whatsapp_token_invalid: () => ({ title: "Meta didn't accept that token for that number." }),
	whatsapp_id_invalid: () => ({ title: "Those ids should be digits only." }),
	whatsapp_window_closed: () => ({
		title: "More than 24 hours have passed since this customer wrote.",
		body: "WhatsApp only lets you write freely within 24 hours. You can reply when they write again.",
	}),
	whatsapp_thread_not_found: () => ({ title: "That chat isn't here any more." }),
	whatsapp_send_failed: () => ({ title: "WhatsApp didn't accept the message. Please try again." }),
	whatsapp_document_invalid: () => ({ title: "Only PDF, JPG or PNG files of this knowledge base can be sent." }),
	whatsapp_follow_up_incomplete: () => ({
		title: "Add the template name and language before switching the follow-up on.",
	}),
	// /TV6 whatsapp
	// BIZ4
	avatar_invalid: () => ({ title: onboardingStrings.errors.avatarInvalid }),
	avatar_too_large: () => ({ title: onboardingStrings.errors.avatarTooLarge }),
	avatar_not_found: () => ({ title: onboardingStrings.errors.avatarNotFound }),
	onboarding_failed: (e) => ({ title: e.detail || "We couldn't finish setting up. Please try again." }),
	// /BIZ4
};

/** The lock holder named in a portal lock refusal ("… locked by 'priya@shree-labs.in' …"). */
export function lockHolder(detail: string): string | null {
	return /locked by '([^']+)'/.exec(detail)?.[1] ?? null;
}

export const MESSAGE_CODES: readonly string[] = Object.keys(TABLE);

function fallback(e: ApiError): UiMessage {
	if (e.status === 401) return { title: "Please sign in." };
	if (e.status === 403) return { title: "You don't have access to this." };
	if (e.status === 404) return { title: "Not found — it may have been deleted." };
	if (e.status === 409 || e.status === 422) return { title: e.detail || "That request wasn't accepted." };
	if (e.status >= 500) return { title: `The engine had a problem (${e.status}). Try again.` };
	return { title: e.detail || "Something went wrong." };
}

export function messageFor(e: ApiError, context: MessageContext = {}): UiMessage {
	const builder = e.code ? TABLE[e.code] : undefined;
	return builder ? builder(e, context) : fallback(e);
}

/** "45 seconds", "1 minute", "3 minutes" (rounded up), "1 hour 5 minutes". */
export function formatDuration(totalSeconds: number): string {
	const s = Math.max(0, Math.ceil(totalSeconds));
	if (s < 60) return `${s} ${s === 1 ? "second" : "seconds"}`;
	const minutes = Math.ceil(s / 60);
	if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
	const h = Math.floor(minutes / 60);
	const m = minutes % 60;
	return `${h} ${h === 1 ? "hour" : "hours"}${m ? ` ${m} ${m === 1 ? "minute" : "minutes"}` : ""}`;
}

/** "0:42" clock text for resend buttons. */
export function formatClock(totalSeconds: number): string {
	const s = Math.max(0, Math.ceil(totalSeconds));
	return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
