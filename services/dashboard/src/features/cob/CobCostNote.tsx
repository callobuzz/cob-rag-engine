import { useQuery } from "@tanstack/react-query";
import { consumptionQuery } from "@/features/usage/consumption";
import { usePrincipal } from "@/lib/auth/store";
import { formatNumber } from "@/lib/format/number";
import { cobStrings as t } from "@/lib/strings/cob";
import { fill } from "@/lib/strings/en";

/**
 * "COB answers count toward your monthly questions." with the meter. Only for a workspace on the
 * operator's key with a plan that has a question limit; never for the operator view (COB is free
 * for them) or a workspace on its own key.
 */
export function CobCostNote() {
	const principal = usePrincipal();
	const customer = principal?.view === "tenant";
	const q = useQuery({ ...consumptionQuery, enabled: customer });
	const data = q.data;
	if (!customer || !data || data.view !== "operator_key" || data.question_limit === null) return null;
	return (
		<p data-testid="cob-cost-note" className="m-0 text-[12px] text-muted-foreground">
			{t.cost.note}{" "}
			<span data-testid="cob-cost-meter">
				{fill(t.cost.meter, { used: formatNumber(data.questions), limit: formatNumber(data.question_limit) })}
			</span>
		</p>
	);
}
