import { useQuery } from "@tanstack/react-query";
import type { EngineInfo } from "@/lib/api/accounts-types";
import { infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";

/**
 * What the deployer's plan shows a business (BIZ3). `GET /info` carries the caller's plan flags in
 * `features.dashboard` (null = everything is shown: the operator view, API keys, the operator's own
 * workspace). Every flag means "shown" except `widget_code_only`, which is a restriction. The
 * engine refuses the configuration routes of a hidden feature as well (`feature_not_in_plan`);
 * this only decides what the menu and the tabs offer.
 */
export type DashboardFeatures = NonNullable<EngineInfo["features"]["dashboard"]>;

/** A configuration area a plan can hide. `widget_settings` is the widget page's full form. */
export type Gate = "models" | "retrieval" | "keys_agents" | "webhooks" | "quality" | "widget_settings";
/** What a menu item can be gated by: the engine's gates plus two navigation-only flags. */
export type NavGate = Gate | "api_docs" | "usage_details";

export interface DashboardFeatureView {
	/** Whether the plan lets the person see this area. Always true when nothing is restricted. */
	shown: (gate: NavGate) => boolean;
	/** The business dashboard (six plain items, Advanced folded away) rather than the operator's. */
	simple: boolean;
	/** The plan wants the widget page to show only the code to paste and the allowed sites. */
	codeOnlyWidget: boolean;
	/** The whole Advanced group is hidden by the plan. */
	advancedHidden: boolean;
}

const FLAG_OF: Record<Exclude<NavGate, "widget_settings">, keyof DashboardFeatures> = {
	models: "models",
	retrieval: "retrieval",
	keys_agents: "keys_agents",
	webhooks: "webhooks",
	quality: "quality",
	api_docs: "api_docs",
	usage_details: "usage_details",
};

/** The pure rule, for the nav and its tests: `features` is `/info`'s `features.dashboard`. */
export function featureView(features: DashboardFeatures | null | undefined, simple: boolean): DashboardFeatureView {
	const f = features ?? null;
	return {
		simple,
		codeOnlyWidget: f?.widget_code_only === true,
		advancedHidden: f?.advanced === false,
		shown: (gate) => {
			if (!f) return true;
			if (gate === "widget_settings") return f.widget_code_only !== true;
			return f.advanced !== false && f[FLAG_OF[gate]] !== false;
		},
	};
}

/** The plan's dashboard flags for the signed-in person, from `GET /info`. */
export function useDashboardFeatures(): DashboardFeatureView {
	const principal = usePrincipal();
	const info = useQuery(infoQuery);
	return featureView(info.data?.features.dashboard, principal?.view === "tenant");
}
