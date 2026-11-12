import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Panel } from "@/features/kbs/ui";
import { KeysPanel } from "@/features/keys/KeysPanel";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import { formatNumber } from "@/lib/format/number";
import { workspaceStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";

/**
 * Settings → Workspace (tenant view): the workspace itself and its API keys (D7). The keys are
 * the workspace's `tk_` keys, any number, each named and with a permission (`/tenants/me/keys`);
 * signed in with one of them, the table marks it and warns before it is rotated or revoked.
 */
export function WorkspaceSection() {
	const me = useQuery({
		queryKey: qk.tenants.me,
		queryFn: () => unwrap(api().GET("/api/v1/tenants/me")),
	});
	const w = me.data;
	return (
		<>
			<Panel title={t.card.title}>
				{me.isPending ? (
					<TableSkeleton rows={3} />
				) : me.isError ? (
					<div className="p-4">
						<ErrorState error={me.error} title={t.card.error} onRetry={() => void me.refetch()} />
					</div>
				) : (
					<KeyValue
						rows={[
							{ label: t.card.name, value: w?.name ?? "" },
							{ label: t.card.status, value: <StatusBadge status={w?.status ?? "active"} /> },
							{
								label: t.card.plan,
								value: (
									<span>
										{w?.plan_name}{" "}
										<Link to="/plan" className="font-sans font-semibold text-brand-text">
											{t.card.planLink}
										</Link>
									</span>
								),
							},
							{
								label: t.card.kbs,
								value: fill(t.card.kbsOf, {
									used: formatNumber(w?.corpus_count ?? 0),
									limit: w?.effective_max_corpora == null ? "∞" : formatNumber(w.effective_max_corpora),
								}),
							},
						]}
					/>
				)}
			</Panel>
			<KeysPanel owner={{ kind: "workspace", name: w?.name ?? "" }} />
		</>
	);
}
