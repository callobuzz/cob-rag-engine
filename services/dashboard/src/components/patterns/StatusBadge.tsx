import { CircleCheck, CircleDashed, CirclePause, CircleX, Clock } from "lucide-react";
import { Badge } from "@/components/ui/badge";

/** Status as icon + text (never colour alone). */
export type Status = "active" | "pending" | "suspended" | "failed" | "completed" | "running" | "queued" | string;

const MAP: Record<string, { variant: "ok" | "warn" | "bad" | "info" | "default"; icon: typeof Clock; label: string }> =
	{
		active: { variant: "ok", icon: CircleCheck, label: "Active" },
		completed: { variant: "ok", icon: CircleCheck, label: "Completed" },
		pending: { variant: "warn", icon: Clock, label: "Pending" },
		queued: { variant: "default", icon: CircleDashed, label: "Queued" },
		running: { variant: "info", icon: CircleDashed, label: "Running" },
		suspended: { variant: "bad", icon: CirclePause, label: "Suspended" },
		failed: { variant: "bad", icon: CircleX, label: "Failed" },
	};

export function StatusBadge({ status, label }: { status: Status; label?: string }) {
	const m = MAP[status] ?? { variant: "default" as const, icon: CircleDashed, label: status };
	const Icon = m.icon;
	return (
		<Badge variant={m.variant}>
			<Icon aria-hidden />
			{label ?? m.label}
		</Badge>
	);
}
