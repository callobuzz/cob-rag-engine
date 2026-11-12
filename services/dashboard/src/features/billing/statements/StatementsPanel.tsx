import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Panel } from "@/features/kbs/ui";
import { planBillingStrings, adminStatementStrings as t } from "@/lib/strings/billing-statements";
import { fill } from "@/lib/strings/en";
import { adminStatementsQuery, loadAdminStatement } from "./api";
import { workspaceSlug } from "./open";
import { StatementsList } from "./StatementsList";

/**
 * "Statements" on a customer's Billing tab (BIL6, admin): every month the customer can open
 * themselves, with the same View, CSV and Download PDF.
 */
export function StatementsPanel({ tenantId, tenantName }: { tenantId: string; tenantName: string }) {
	const q = useQuery(adminStatementsQuery(tenantId));
	let body: React.ReactNode;
	if (q.isPending) body = <TableSkeleton rows={3} />;
	else if (q.isError)
		body = (
			<div className="p-4">
				<ErrorState error={q.error} title={planBillingStrings.statements.loadError} onRetry={() => void q.refetch()} />
			</div>
		);
	else if (q.data.items.length === 0)
		body = (
			<p data-testid="statements-empty" className="m-0 p-6 text-foreground-2">
				{t.empty}
			</p>
		);
	else {
		const currency = q.data.items[0]?.currency ?? "INR";
		const slug = workspaceSlug(tenantName);
		body = (
			<StatementsList
				items={q.data.items}
				currency={currency}
				source={{
					load: (month, format) => loadAdminStatement(tenantId, month, format),
					fileName: (month) => `statement-${slug}-${month}.csv`,
				}}
			/>
		);
	}
	return (
		<Panel title={t.title} sub={fill(t.lead, { name: tenantName })}>
			<div data-testid="customer-statements">{body}</div>
		</Panel>
	);
}
