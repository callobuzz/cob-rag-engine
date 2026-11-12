import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleX, RefreshCw } from "lucide-react";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { formatRelative } from "@/lib/format/date";
import { engineStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { cn } from "@/lib/utils";
import { healthQuery } from "./api";

const t = engineStrings.health;
const o = overviewStrings.health;

/** `GET /health`: each service OK / Down (icon + text), with "Check again". */
export function HealthCard() {
	const q = useQuery(healthQuery);
	const services = Object.entries(q.data?.services ?? {});
	const allOk = services.length > 0 && services.every(([, ok]) => ok);
	return (
		<Panel
			title={t.title}
			sub={q.data ? fill(t.sub, { when: formatRelative(q.dataUpdatedAt) }) : undefined}
			aside={
				<div className="flex items-center gap-2">
					{q.isError ? (
						<Badge>
							<CircleAlert aria-hidden />
							{o.couldNot}
						</Badge>
					) : q.data ? (
						<Badge variant={allOk ? "ok" : "bad"}>
							{allOk ? <CircleCheck aria-hidden /> : <CircleX aria-hidden />}
							{allOk ? o.allGood : o.degraded}
						</Badge>
					) : null}
					<Button size="sm" disabled={q.isFetching} onClick={() => void q.refetch()}>
						<RefreshCw aria-hidden className={cn(q.isFetching && "motion-safe:animate-spin")} />
						{t.again}
					</Button>
				</div>
			}
		>
			{q.isPending ? (
				<TableSkeleton rows={2} />
			) : q.isError ? (
				<p className="m-0 px-4 py-3.5 text-[13px] text-muted-foreground">{o.couldNot}</p>
			) : (
				<ul
					aria-label={t.label}
					className="m-0 grid list-none grid-cols-2 gap-2 px-4 pt-3 pb-4 min-[861px]:grid-cols-3"
				>
					{services.map(([name, ok]) => (
						<li
							key={name}
							data-testid={`engine-service-${name}`}
							className="flex items-center gap-2 rounded-sm bg-secondary px-2.5 py-2 text-[13px]"
						>
							<span
								aria-hidden
								className={cn(
									"size-2 shrink-0 rounded-full",
									ok ? "bg-success shadow-[0_0_0_3px_var(--success-soft)]" : "bg-destructive",
								)}
							/>
							<span className="min-w-0 truncate">{o.names[name] ?? name}</span>
							<span
								className={cn(
									"ml-auto font-mono text-[11.5px]",
									ok ? "text-muted-foreground" : "font-semibold text-destructive",
								)}
							>
								{ok ? o.ok : o.down}
							</span>
						</li>
					))}
				</ul>
			)}
		</Panel>
	);
}
