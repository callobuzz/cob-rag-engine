import { type QueryClient, queryOptions, useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect } from "react";
import type { PublicBranding } from "@/lib/api/accounts-types";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import { foregroundOn, parseHex, readableOnAll } from "./contrast";

/** --background, --card, --muted, --secondary, --accent per theme (globals.css). */
export const LIGHT_SURFACES = ["#f5f7fb", "#ffffff", "#eef2f8", "#f4f7fb", "#edf2f9"];
export const DARK_SURFACES = ["#07111d", "#0d1a2a", "#0a1726", "#122235", "#16293f"];

/**
 * GET /api/v1/public/branding, fetched once at app start before sign-in (root route
 * beforeLoad → ensureQueryData). Drives product name, logo, brand colour, support contact,
 * sign-up availability and the permanent "cob-rag-engine by Callobuzz" line.
 */

export const POWERED_BY = { text: "cob-rag-engine by Callobuzz" } as const; // L1 release: text only, never linked

export const DEFAULT_BRAND_COLOR = "#2f8cff";

export interface ResolvedBranding {
	productName: string;
	logoUrl: string | null;
	primaryColor: string;
	supportEmail: string | null;
	contactText: string;
	contactUrl: string | null;
	signupMode: "open" | "approval" | "closed";
	/** A3: email sign-in available. Missing (older engine) or branding failed → false. */
	accountsEnabled: boolean;
	/** A4: sign-up form usable (mode not closed, accounts on, e-mail configured). */
	signupAvailable: boolean;
	/** RAG_ENV=dev: addresses on reserved names (.local, .test, ...) are accepted. Missing → false (the production rule). */
	specialUseEmailsAllowed: boolean;
	/** COB6: "Ask COB" is offered on the sign-in pages (engine switches and the operator's setting). Missing → false. */
	cobPublic: boolean;
	// LP1 landing
	/** The deployer's public website is live at the root of this address (sign-in pages may link back). Missing → false. */
	landing: boolean;
	// /LP1 landing
	passwordPolicy: { min_length: number; max_length: number };
	/** null: the operator hid the line in Branding. A failed branding call keeps the built-in line. */
	poweredBy: { text: string } | null;
	/** True when the branding call failed and these are the built-in defaults. */
	failed: boolean;
}

export const DEFAULT_POLICY = { min_length: 10, max_length: 128 };

/** Only http(s) URLs may become an href/src (A7 security note). */
export function safeHttpUrl(value: string | null | undefined): string | null {
	if (!value) return null;
	try {
		const u = new URL(value);
		return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
	} catch {
		return null;
	}
}

export function resolveBranding(data: PublicBranding | undefined, failed: boolean): ResolvedBranding {
	const color = data?.primary_color && parseHex(data.primary_color) ? data.primary_color : DEFAULT_BRAND_COLOR;
	const pb = data?.powered_by;
	// null from the engine = hidden by the operator in Branding; anything unusable keeps the line
	const poweredBy =
		data && pb === null
			? null
			: pb && typeof pb.text === "string" && pb.text.trim()
				? { text: pb.text }
				: { ...POWERED_BY };
	const policy = data?.password_policy;
	return {
		productName: data?.product_name?.trim() || "cob-rag-engine",
		logoUrl: safeHttpUrl(data?.logo_url),
		primaryColor: color,
		supportEmail: data?.support_email || null,
		contactText: data?.contact_text || "Contact us for pricing.",
		contactUrl: safeHttpUrl(data?.contact_url),
		signupMode: data?.signup_mode ?? "approval",
		accountsEnabled: data?.accounts_enabled ?? false,
		signupAvailable: data?.signup_available ?? false,
		specialUseEmailsAllowed: data?.special_use_emails_allowed ?? false,
		cobPublic: data?.cob_public ?? false,
		// LP1 landing
		landing: data?.landing?.enabled ?? false,
		// /LP1 landing
		passwordPolicy: policy && policy.min_length > 0 && policy.max_length >= policy.min_length ? policy : DEFAULT_POLICY,
		poweredBy,
		failed,
	};
}

export const brandingQuery = queryOptions({
	queryKey: qk.branding,
	queryFn: () => unwrap(api().GET("/api/v1/public/branding")) as Promise<PublicBranding>,
	staleTime: 60_000,
	retry: 1,
	// A failure at start-up already fell back to defaults; do not refetch-and-flicker on mount.
	retryOnMount: false,
});

/** For route guards: branding from the cache (the root route already loaded it). */
export function brandingFromCache(queryClient: QueryClient): ResolvedBranding {
	const state = queryClient.getQueryState(brandingQuery.queryKey);
	return resolveBranding(queryClient.getQueryData(brandingQuery.queryKey), state?.status === "error");
}

export function useBranding(): ResolvedBranding {
	const q = useQuery(brandingQuery);
	return resolveBranding(q.data, q.isError);
}

/** Applies the brand colour to CSS variables and the operator logo to the favicon. */
export function BrandingProvider({ children }: { children: ReactNode }) {
	const b = useBranding();
	useEffect(() => {
		const root = document.documentElement.style;
		root.setProperty("--brand", b.primaryColor);
		root.setProperty("--brand-foreground", foregroundOn(b.primaryColor));
		// Links sit on the page, cards, inputs and hover rows: readable on all of them.
		root.setProperty("--brand-text-light", readableOnAll(b.primaryColor, LIGHT_SURFACES));
		root.setProperty("--brand-text-dark", readableOnAll(b.primaryColor, DARK_SURFACES));
	}, [b.primaryColor]);
	useEffect(() => {
		const link = document.getElementById("favicon");
		if (link instanceof HTMLLinkElement) link.href = b.logoUrl ?? "/favicon.svg";
	}, [b.logoUrl]);
	return <>{children}</>;
}
