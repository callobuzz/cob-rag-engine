import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useUpdateWidgetKey, useWidgetKeys } from "@/features/widget/api";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import { fill } from "@/lib/strings/en";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { cn } from "@/lib/utils";
import { type Onboarding, onboardingQuery, POLL_GIVE_UP_MS } from "./api";
import { siteName } from "./install-guides";

const t = onboardingStrings.two.light;

/**
 * A light that turns green the moment the assistant loads on the business's website (the engine
 * records the first load from an allowed site). It looks again every few seconds while the tab is
 * visible and gives up after 15 minutes with what to check. A load from a site the key does not
 * allow yet, usually the `www` spelling, offers a button that allows it.
 */
export function ConnectionLight({ state }: { state: Onboarding }) {
	const [gaveUp, setGaveUp] = useState(false);
	const connected = state.connection.status === "connected";
	const live = useQuery({ ...onboardingQuery(!connected && !gaveUp), initialData: state });
	const current = live.data ?? state;
	const isConnected = current.connection.status === "connected";
	useEffect(() => {
		if (isConnected) return;
		const timer = setTimeout(() => setGaveUp(true), POLL_GIVE_UP_MS);
		return () => clearTimeout(timer);
	}, [isConnected]);

	const site = siteName(current.connection.origin ?? current.website);
	const tone = isConnected ? "green" : gaveUp ? "grey" : "amber";
	return (
		<div className="grid gap-3" data-testid="connection-light" data-state={isConnected ? "connected" : "waiting"}>
			<div
				role="status"
				className={cn(
					"flex items-start gap-3 rounded-md border p-3",
					isConnected ? "border-success bg-success-soft" : "border-border bg-secondary",
				)}
			>
				<span
					aria-hidden
					className={cn(
						"mt-1 size-3 shrink-0 rounded-full",
						tone === "green" ? "bg-success" : tone === "amber" ? "animate-pulse bg-warning" : "bg-muted-foreground",
					)}
				/>
				<div className="grid gap-0.5">
					<b className="text-[14px] font-semibold">
						{isConnected ? fill(t.connected, { site }) : gaveUp ? t.stopped : t.waiting}
					</b>
					{!isConnected ? (
						<span className="text-[12.5px] text-foreground-2">
							{gaveUp ? fill(t.stoppedHint, { website: siteName(current.website) }) : t.waitingHint}
						</span>
					) : null}
					{gaveUp && !isConnected ? (
						<div className="mt-1">
							<Button
								size="sm"
								variant="outline"
								onClick={() => {
									setGaveUp(false);
									void live.refetch();
								}}
							>
								{t.checkAgain}
							</Button>
						</div>
					) : null}
				</div>
			</div>
			{current.connection.refused.map((r) => (
				<RefusedSite key={r.origin} state={current} origin={r.origin} />
			))}
		</div>
	);
}

/** "We see your assistant on https://www.shreedental.in but your settings only allow another address." */
function RefusedSite({ state, origin }: { state: Onboarding; origin: string }) {
	const kb = state.kb_id ?? "";
	const keys = useWidgetKeys(kb);
	const update = useUpdateWidgetKey(kb);
	const qc = useQueryClient();
	const key = keys.data?.find((k) => k.id === state.widget_key_id);
	const site = siteName(origin);
	const allow = async () => {
		if (!key) return;
		try {
			await update.mutateAsync({ id: key.id, body: { allowed_origins: [...key.allowed_origins, origin] } });
			toast.success(t.allowed);
			await qc.invalidateQueries({ queryKey: qk.onboarding });
		} catch (e) {
			const m = messageFor(toApiError(e));
			toast.error(t.allowFailed, { description: m.title });
		}
	};
	// Allowed a moment ago: the next poll may still list it, but there is nothing left to allow.
	if (key?.allowed_origins.includes(origin)) return null;
	return (
		<div role="alert" className="grid gap-2 rounded-md border border-warning bg-warning-soft p-3 text-[13px]">
			<span>{fill(t.refusedTitle, { site })}</span>
			<div>
				<Button size="sm" disabled={!key || update.isPending} onClick={() => void allow()}>
					{fill(t.allow, { site: origin })}
				</Button>
			</div>
		</div>
	);
}
