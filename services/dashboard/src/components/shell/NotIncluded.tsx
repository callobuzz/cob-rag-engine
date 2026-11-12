import { Lock } from "lucide-react";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { planFeatureStrings as t } from "@/lib/strings/plan-features";

/**
 * "Not included in your plan": shown where a deep link lands on a part the deployer's plan hides.
 * It names who to ask (the deployer's support contact when one is set) and sends no request.
 */
export function NotIncluded({ className }: { className?: string }) {
	const { supportEmail } = useBranding();
	return (
		<section
			data-testid="not-in-plan"
			className={className ?? "grid justify-items-start gap-3.5 rounded-lg border border-border bg-card p-8"}
		>
			<span className="grid size-[52px] place-items-center rounded-[14px] bg-secondary text-muted-foreground">
				<Lock className="size-[26px]" aria-hidden />
			</span>
			<h2 className="text-2xl font-bold">{t.notIncludedTitle}</h2>
			<p className="m-0 max-w-[560px] text-foreground-2">
				{fill(t.notIncludedBody, { support: supportEmail || t.supportFallback })}
			</p>
		</section>
	);
}
