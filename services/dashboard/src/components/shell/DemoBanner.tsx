import { Info } from "lucide-react";
import { DemoAnswers } from "@/components/shell/DemoAnswers";
import { localResetTime, useDemoInfo } from "@/lib/branding/demo";
import { demoStrings as t } from "@/lib/strings/demo";
import { fill } from "@/lib/strings/en";

/**
 * The bar of a live demo (above the other banners): why some actions are refused and when the
 * demo is put back. Shown only when the public branding says this is a demo server, and the
 * reset time is shown in the visitor's own time zone.
 */
export function DemoBanner() {
	const demo = useDemoInfo();
	if (!demo) return null;
	return (
		<div
			role="status"
			aria-label={t.region}
			data-testid="demo-banner"
			className="flex items-center gap-3 border-b border-border bg-secondary px-4 py-2 text-[13px] text-foreground min-[861px]:px-7"
		>
			<Info className="size-4 shrink-0" aria-hidden />
			<span className="min-w-0 flex-1">
				{fill(t.banner, { time: localResetTime(demo.resets_at_utc) })} <DemoAnswers info={demo} />
			</span>
		</div>
	);
}
