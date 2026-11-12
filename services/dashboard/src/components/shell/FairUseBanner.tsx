import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Gauge, TriangleAlert, X } from "lucide-react";
import { useState } from "react";
import { buttonVariants } from "@/components/ui/button";
import { ownBillingQuery } from "@/features/billing/addons/ActiveAddOns";
import { type FairUseOwnerView, fairUseMineQuery } from "@/features/fair-use/api";
import { fairUseStateSentence } from "@/features/fair-use/sentences";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { fairUseOwnerStrings as t } from "@/lib/strings/pipelines";
import { cn } from "@/lib/utils";

const DISMISS_KEY = "cob.fair-use-near-dismissed";

function dismissedThisSession(): boolean {
	try {
		return window.sessionStorage.getItem(DISMISS_KEY) === "1";
	} catch {
		return false;
	}
}

/** The limit closest to its edge (the highest share among those near or past it). */
function worstMetric(view: FairUseOwnerView) {
	return [...view.metrics].filter((m) => m.level !== "ok").sort((a, b) => b.pct - a.pct)[0];
}

/** The sentence for a state, in plain words and without any amount of the deployer's AI cost. */
export function fairUseSentence(view: FairUseOwnerView, now?: Date): string | null {
	return fairUseStateSentence(view.state, worstMetric(view), now);
}

/**
 * Fair-use banner, a business's own view only: its answers are slower or paused because a limit was
 * passed, it is close to one, or (when its plan keeps answering) its balance ran out and answers now
 * use the free, slower assistant. Members read the same words. A balance pause that stops answers is
 * the billing banner's, so this one stays out of its way; only "near" can be dismissed, for the session.
 */
export function FairUseBanner() {
	const principal = usePrincipal();
	const { productName } = useBranding();
	const inWorkspace = principal?.view === "tenant" && !principal.tenant?.isOperator;
	const fair = useQuery({ ...fairUseMineQuery, enabled: inWorkspace });
	const billing = useQuery({ ...ownBillingQuery, enabled: inWorkspace });
	const [dismissed, setDismissed] = useState(dismissedThisSession);
	const view = fair.data;
	if (!inWorkspace || !view) return null;
	const isMember = principal?.role === "member";
	const account = billing.data;
	const balanceOut = !!account && !account.exempt && account.paused === true;

	let text: string | null;
	let tone: "warn" | "bad" | "info";
	let testId: string;
	if (balanceOut && view.billing_block !== "downgrade_profile") {
		// The balance pause that stops answers is the billing banner's alone.
		return null;
	} else if (view.answers_paused) {
		// The plan answers on the free local model and it is not ready: every visitor sees the pause message.
		text = fill(t.localNotReady, { product: productName });
		tone = "bad";
		testId = "fair-use-banner-local";
	} else if (balanceOut) {
		// Answers keep coming, slower, only when the plan says so. A limit past its hard line pauses them anyway.
		if (view.state === "stopped") {
			text = fairUseSentence(view);
			tone = "bad";
			testId = "fair-use-banner-stopped";
		} else {
			text = t.billing;
			tone = "warn";
			testId = "fair-use-banner-billing";
		}
	} else {
		text = fairUseSentence(view);
		tone = view.state === "stopped" ? "bad" : view.state === "near" ? "info" : "warn";
		testId = `fair-use-banner-${view.state}`;
	}
	if (!text) return null;
	if (view.state === "near" && !balanceOut && dismissed) return null;
	const Icon = tone === "bad" ? TriangleAlert : Gauge;
	return (
		<div
			{...{
				role: tone === "bad" ? "alert" : "status",
				"aria-label": fill(t.region, { product: productName }),
			}}
			data-testid={testId}
			data-state={view.state}
			className={cn(
				"flex flex-wrap items-start gap-3 border-b px-4 py-2.5 text-[13px] text-foreground min-[861px]:px-7",
				tone === "bad" && "border-[color-mix(in_srgb,var(--destructive)_35%,var(--border))] bg-destructive-soft",
				tone === "warn" && "border-[color-mix(in_srgb,var(--warning)_35%,var(--border))] bg-warning-soft",
				tone === "info" && "border-border bg-secondary",
			)}
		>
			<Icon
				className={cn(
					"mt-0.5 size-4 shrink-0",
					tone === "bad" ? "text-destructive" : tone === "warn" ? "text-warning" : "text-foreground-2",
				)}
				aria-hidden
			/>
			<p className="m-0 min-w-0 flex-1">{text}</p>
			{isMember ? null : (
				<Link to="/plan" className={buttonVariants({ size: "sm", variant: "outline" })}>
					{t.seePlan}
				</Link>
			)}
			{view.state === "near" && !balanceOut ? (
				<button
					type="button"
					aria-label={t.dismiss}
					className="rounded-sm p-1 text-foreground-2 hover:text-foreground"
					onClick={() => {
						try {
							window.sessionStorage.setItem(DISMISS_KEY, "1");
						} catch {
							// storage blocked: dismiss for this render only
						}
						setDismissed(true);
					}}
				>
					<X className="size-4" aria-hidden />
				</button>
			) : null}
		</div>
	);
}
