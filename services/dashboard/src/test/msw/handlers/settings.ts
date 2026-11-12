import { HttpResponse, http } from "msw";
import type { Settings } from "@/features/branding/api";
import { BASE, BRANDING } from "../data";

/**
 * A7 `GET/PATCH /admin/settings` + the public branding that follows it, stateful. PATCH applies
 * only the sent fields (`null` resets to the default) and validates like `BrandingPatch`: colour
 * pattern, http(s) URLs (`http://` refused when `prodHttp` is on, like RAG_ENV=production), e-mail.
 */
const DEFAULT_CONTACT = "Contact us for pricing.";

export function createSettingsMock() {
	const state = {
		settings: {
			signup_mode: "approval",
			branding: {
				product_name: BRANDING.product_name,
				logo_url: null,
				primary_color: BRANDING.primary_color,
				support_email: BRANDING.support_email,
				contact_text: BRANDING.contact_text,
				contact_url: BRANDING.contact_url,
				hide_powered_by: false,
			},
			cob_public_sign_in: true,
			// TV1 human, TV3 notices and support (every field the engine returns)
			trust: {
				allow_hide_human_button: false,
				require_ai_disclosure: true,
				disclosure_wording_editable: true,
				renewal_reminder_days: 7,
				price_change_notice_days: 30,
				spend_cap_max_minor: {},
				support_enabled: true,
				support_sla_hours: null,
				price_sheet: { plan_ids: [], show_add_ons: true, note: "", public_token: null },
				value_report_enabled: true,
				value_report_cc_deployer: false,
			},
			pipelines: { engine_ai_spend_per_day_usd_micro: null, engine_spend_alert_pct: 80, crawl_concurrency: null }, // PIP4
			whatsapp: { rates_minor: {}, free_service_per_month: 0 }, // TV6
			updated_at: "2026-09-29T10:00:00Z",
			updated_by: "sv@punedigital.in",
		} as Settings,
		patches: [] as unknown[],
		prodHttp: false,
	};
	const fieldError = (field: string, msg: string) =>
		HttpResponse.json({ detail: [{ loc: ["body", "branding", field], msg, type: "value_error" }] }, { status: 422 });

	const handlers = [
		http.get(`${BASE}/api/v1/admin/settings`, () => HttpResponse.json(state.settings)),
		http.patch(`${BASE}/api/v1/admin/settings`, async ({ request }) => {
			const body = (await request.json()) as Record<string, unknown>;
			state.patches.push(body);
			const b = body.branding as Record<string, unknown> | undefined;
			if (b) {
				if (typeof b.primary_color === "string" && !/^#[0-9a-fA-F]{6}$/.test(b.primary_color))
					return fieldError("primary_color", "String should match pattern");
				for (const k of ["logo_url", "contact_url"]) {
					const v = b[k];
					if (typeof v === "string" && (!/^https?:\/\//.test(v) || (state.prodHttp && v.startsWith("http://"))))
						return fieldError(k, "Value error, must be an https:// URL");
				}
				const next = { ...state.settings.branding };
				for (const [k, v] of Object.entries(b)) {
					if (v === null) {
						if (k === "product_name") next.product_name = "cob-rag-engine";
						else if (k === "primary_color") next.primary_color = "#2f8cff";
						else if (k === "contact_text") next.contact_text = DEFAULT_CONTACT;
						else Object.assign(next, { [k]: null });
					} else Object.assign(next, { [k]: v });
				}
				state.settings = { ...state.settings, branding: next };
			}
			if ("signup_mode" in body)
				state.settings = {
					...state.settings,
					signup_mode: (body.signup_mode as Settings["signup_mode"]) ?? "approval",
				};
			// PIP4: a field sent as null goes back to its default (no ceiling, 80 %, no crawl limit)
			const rails = body.pipelines as Record<string, number | null> | undefined;
			if (rails) {
				const next = { ...state.settings.pipelines } as Record<string, number | null>;
				for (const [k, v] of Object.entries(rails)) {
					next[k] = v === null ? (k === "engine_spend_alert_pct" ? 80 : null) : v;
				}
				state.settings = { ...state.settings, pipelines: next as Settings["pipelines"] };
			}
			// /PIP4
			// TV1 human
			const trust = body.trust as Record<string, unknown> | undefined;
			if (trust) {
				const next = { ...state.settings.trust } as Record<string, unknown>;
				for (const [k, v] of Object.entries(trust)) {
					if (k === "price_sheet" && v && typeof v === "object") {
						// the public link is never settable by a patch
						const { public_token: _ignored, ...rest } = v as Record<string, unknown>;
						next.price_sheet = { ...(next.price_sheet as object), ...rest };
					} else next[k] = v;
				}
				state.settings = { ...state.settings, trust: next as Settings["trust"] };
			}
			// /TV1 human
			// TV6: the fee rates replace the whole map; free allowance is its own field
			const wa = body.whatsapp as Record<string, unknown> | undefined;
			if (wa) {
				state.settings = {
					...state.settings,
					whatsapp: { ...(state.settings.whatsapp as object), ...wa } as Settings["whatsapp"],
				};
			}
			// /TV6
			if ("cob_public_sign_in" in body)
				state.settings = { ...state.settings, cob_public_sign_in: body.cob_public_sign_in !== false };
			state.settings = { ...state.settings, updated_at: "2026-10-01T10:00:00Z", updated_by: "sv@punedigital.in" };
			return HttpResponse.json(state.settings);
		}),
		http.get(`${BASE}/api/v1/public/branding`, () =>
			HttpResponse.json({
				...BRANDING,
				...state.settings.branding,
				signup_mode: state.settings.signup_mode,
				signup_available: state.settings.signup_mode !== "closed",
				cob_public: state.settings.cob_public_sign_in,
			}),
		),
	];
	return { state, handlers };
}
