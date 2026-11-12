import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Clock } from "lucide-react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { Panel } from "@/features/kbs/ui";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { pendingSignupsQuery } from "./api";
import { plural } from "./kpis";
import { List, Muted, Row } from "./ui";

const t = overviewStrings.pending;

/** Admin: sign-ups waiting for activation (A4), linking to the Customers pending tab. */
export function PendingSignups() {
	const q = useQuery(pendingSignupsQuery);
	const { signupMode } = useBranding();
	const total = q.data?.total ?? 0;
	return (
		<Panel
			title={total ? fill(t.title, { n: plural(overviewStrings.signups, total, formatNumber(total)) }) : t.titleNone}
			sub={fill(t.sub, { mode: t.modes[signupMode] })}
			aside={
				total ? (
					<Badge variant="warn">
						<Clock aria-hidden />
						{t.new}
					</Badge>
				) : null
			}
		>
			{q.isPending ? (
				<TableSkeleton rows={3} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
				</div>
			) : total === 0 ? (
				<Muted>{t.none}</Muted>
			) : (
				<>
					<List label={t.titleNone}>
						{q.data.items.map((p) => (
							<Row
								key={p.id}
								testId="pending-signup"
								lead={<KbAvatar kb={p} size={28} />}
								title={p.name}
								sub={[p.owner_emails[0], formatRelative(p.created_at)].filter(Boolean).join(" · ")}
							/>
						))}
					</List>
					<div className="flex flex-wrap items-center justify-between gap-2 rounded-b-lg border-t border-border bg-secondary px-4 py-3">
						<span className="text-[12.5px] text-muted-foreground">{t.foot}</span>
						<Link
							to="/admin/customers"
							search={{ status: "pending" }}
							className={buttonVariants({ variant: "default", size: "sm" })}
						>
							{t.review}
						</Link>
					</div>
				</>
			)}
		</Panel>
	);
}
