import { CircleAlert, CircleCheck, CirclePause, Gauge } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fairUseStrings } from "@/lib/strings/pipelines";
import type { FairUseState } from "./api";

const MAP: Record<FairUseState, { variant: "ok" | "warn" | "bad"; icon: typeof Gauge }> = {
	ok: { variant: "ok", icon: CircleCheck },
	near: { variant: "warn", icon: CircleAlert },
	throttled: { variant: "warn", icon: Gauge },
	stopped: { variant: "bad", icon: CirclePause },
};

/** The fair-use state of a business: OK, Near limit, Slowed down, Paused for fair use (icon and words). */
export function StateBadge({ state }: { state: FairUseState }) {
	const m = MAP[state] ?? MAP.ok;
	const Icon = m.icon;
	return (
		<Badge variant={m.variant} data-testid="fair-use-state" data-state={state}>
			<Icon aria-hidden />
			{fairUseStrings.states[state] ?? state}
		</Badge>
	);
}
