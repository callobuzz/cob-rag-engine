import type { OwnBillingView } from "@/lib/api/billing-types";
import { planBillingStrings } from "@/lib/strings/billing-statements";
import { loadOwnStatement } from "../billing/statements/api";
import { workspaceSlug } from "../billing/statements/open";
import { StatementsList } from "../billing/statements/StatementsList";
import { Panel } from "../kbs/ui";

const s = planBillingStrings.statements;

/**
 * "Statements" (BIL6, owners): the last twelve months from the account view, each with View, CSV
 * and Download PDF. Nothing to open yet shows when the first charge or top-up is still to come.
 */
export function StatementsCard({ view, tenantName }: { view: OwnBillingView; tenantName: string }) {
	const items = view.statements ?? [];
	return (
		<Panel title={s.title} sub={s.lead}>
			{items.length === 0 ? (
				<p data-testid="statements-empty" className="m-0 p-5 text-[13.5px] text-foreground-2">
					{s.empty}
				</p>
			) : (
				<StatementsList
					items={items}
					currency={view.currency}
					source={{
						load: loadOwnStatement,
						fileName: (month) => `statement-${workspaceSlug(tenantName)}-${month}.csv`,
					}}
				/>
			)}
		</Panel>
	);
}
