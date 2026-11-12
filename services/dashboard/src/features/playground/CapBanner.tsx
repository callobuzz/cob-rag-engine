import { Link } from "@tanstack/react-router";
import { Gauge } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import type { ApiError } from "@/lib/api/errors";
import { usePrincipal, useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { playgroundStrings as t } from "@/lib/strings/playground";

/**
 * `question_cap_reached` (A6) shown inline instead of an answer: "Monthly question limit reached
 * ({used} of {limit}). It resets on {date}. Contact {support}." with "See plan" for tenants.
 */
export function CapBanner({ error }: { error: ApiError }) {
	const principal = usePrincipal();
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	const msg = messageFor(error, { ragUrl, support: supportEmail });
	return (
		<div
			role="alert"
			data-testid="cap-banner"
			className="flex flex-wrap items-start gap-3 rounded-md border border-[color-mix(in_srgb,var(--warning)_35%,var(--border))] bg-warning-soft p-3 text-[13px] text-foreground"
		>
			<Gauge className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
			<p className="m-0 min-w-0 flex-1">{msg.title}</p>
			{principal?.view === "tenant" ? (
				<Link to="/plan" className={buttonVariants({ size: "sm", variant: "outline" })}>
					{t.common.seePlan}
				</Link>
			) : null}
		</div>
	);
}
