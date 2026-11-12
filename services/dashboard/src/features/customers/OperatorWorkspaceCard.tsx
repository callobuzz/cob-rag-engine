import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useSwitchScope } from "@/components/shell/ScopeSwitcher";
import { Button, buttonVariants } from "@/components/ui/button";
import { operatorWorkspaceQuery } from "@/features/kbs/api";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { operatorWorkspace } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { formatNumber } from "@/lib/format/number";
import { customersStrings, operatorStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { kbsText, OperatorBadge, PlanBadge } from "./CustomerRow";
import { RestoreWorkspaceCard } from "./RestoreWorkspaceCard";

/**
 * The operator's own workspace, pinned above the customer list in its own small card: it is not a
 * customer (the lists, tabs and counts below leave it out) and it cannot be suspended or declined,
 * so no lifecycle buttons live here. "Open workspace" switches into it (a session sign-in only).
 */
export function OperatorWorkspaceCard() {
	const q = useQuery(operatorWorkspaceQuery);
	const principal = usePrincipal();
	const switchScope = useSwitchScope();
	const ws = q.data;
	if (!ws) return q.isSuccess ? <RestoreWorkspaceCard /> : null;
	const canOpen = operatorWorkspace(principal) !== null;
	return (
		<section
			aria-label={t.cardTitle}
			data-testid="operator-card"
			className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card p-3.5"
		>
			<KbAvatar kb={ws} size={36} />
			<div className="min-w-0 flex-1 basis-[260px]">
				<div className="flex flex-wrap items-center gap-2">
					<Link
						to="/admin/customers/$tenantId"
						params={{ tenantId: ws.id }}
						className="font-semibold text-foreground no-underline hover:underline"
					>
						{ws.name}
					</Link>
					<OperatorBadge />
					<PlanBadge tenant={ws} />
				</div>
				<p className="m-0 mt-0.5 max-w-[640px] text-[12.5px] text-muted-foreground">{t.cardBody}</p>
				<small className="mt-1 block font-mono text-[12px] text-foreground-2">
					{fill(customersStrings.list.cardLine, {
						kbs: kbsText(ws),
						questions: formatNumber(ws.questions_this_month),
					})}
				</small>
			</div>
			<div className="flex flex-wrap gap-1.5">
				{canOpen ? (
					<Button
						size="sm"
						variant="default"
						aria-label={fill(t.openLabel, { name: ws.name })}
						onClick={() => void switchScope("workspace")}
					>
						{t.open}
					</Button>
				) : null}
				<Link
					to="/admin/customers/$tenantId"
					params={{ tenantId: ws.id }}
					aria-label={fill(t.detailsLabel, { name: ws.name })}
					className={buttonVariants({ size: "sm" })}
				>
					{t.details}
				</Link>
			</div>
		</section>
	);
}
