import { CircleAlert, CircleCheck, CircleDashed, CircleHelp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { insightsStrings as t } from "@/lib/strings/insights";

/** Confidence badge thresholds (spec): ≥ 0.8 "Answered well", 0.5–0.8 amber, < 0.5 "Weak". */
export type ConfidenceLevel = "good" | "unsure" | "weak" | "none";

export function confidenceLevel(score: number | null | undefined): ConfidenceLevel {
	if (score == null || Number.isNaN(score)) return "none";
	if (score >= 0.8) return "good";
	if (score >= 0.5) return "unsure";
	return "weak";
}

const LOOK = {
	good: { variant: "ok", icon: CircleCheck },
	unsure: { variant: "warn", icon: CircleHelp },
	weak: { variant: "bad", icon: CircleAlert },
	none: { variant: "default", icon: CircleDashed },
} as const;

/** Icon + text + the score (never colour alone). */
export function ConfidenceBadge({ score }: { score: number | null | undefined }) {
	const level = confidenceLevel(score);
	const { variant, icon: Icon } = LOOK[level];
	return (
		<Badge variant={variant} data-level={level}>
			<Icon aria-hidden />
			{t.badge[level]}
			{score != null && level !== "none" ? <span className="tabular-nums">· {score.toFixed(2)}</span> : null}
		</Badge>
	);
}
