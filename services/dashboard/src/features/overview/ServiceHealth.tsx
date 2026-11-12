import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleX } from "lucide-react";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Panel } from "@/features/kbs/ui";
import { formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { cn } from "@/lib/utils";
import { healthQuery } from "./api";

const t = overviewStrings.health;

/**
 * Admin: `/health` services as OK / Down with an icon and text (never colour alone). The endpoint
 * returns booleans only, so no per-service latency (AUDIT #18).
 */
export function ServiceHealth() {
	const q = useQuery(healthQuery);
	const services = Object.entries(q.data?.services ?? {});
	const allOk = services.length > 0 && services.every(([, ok]) => ok);
	return (
		<Panel
			title={t.title}
			sub={q.data ? fill(t.sub, { when: formatRelative(q.dataUpdatedAt) }) : undefined}
			aside={
				q.isError ? (
					<Badge>
						<CircleAlert aria-hidden />
						{t.couldNot}
					</Badge>
				) : q.data ? (
					<Badge variant={allOk ? "ok" : "bad"}>
						{allOk ? <CircleCheck aria-hidden /> : <CircleX aria-hidden />}
						{allOk ? t.allGood : t.degraded}
					</Badge>
				) : null
			}
		>
			{q.isPending ? (
				<TableSkeleton rows={2} />
			) : q.isError ? (
				<p className="m-0 px-4 py-3.5 text-[13px] text-muted-foreground">{t.couldNot}</p>
			) : (
				<ul aria-label={t.title} className="m-0 grid list-none grid-cols-2 gap-2 px-4 pt-3 pb-4">
					{services.map(([name, ok]) => (
						<li
							key={name}
							data-testid={`service-${name}`}
							className="flex items-center gap-2 rounded-sm bg-secondary px-2.5 py-2 text-[13px]"
						>
							<span
								aria-hidden
								className={cn(
									"size-2 shrink-0 rounded-full",
									ok ? "bg-success shadow-[0_0_0_3px_var(--success-soft)]" : "bg-destructive",
								)}
							/>
							<span className="min-w-0 truncate">{t.names[name] ?? name}</span>
							<span
								className={cn(
									"ml-auto font-mono text-[11.5px]",
									ok ? "text-muted-foreground" : "font-semibold text-destructive",
								)}
							>
								{ok ? t.ok : t.down}
							</span>
						</li>
					))}
				</ul>
			)}
		</Panel>
	);
}
