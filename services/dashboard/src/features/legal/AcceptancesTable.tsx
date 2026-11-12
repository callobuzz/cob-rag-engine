import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { FormAlert } from "@/components/auth/FormAlert";
import { DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { downloadText } from "@/features/usage/csv";
import { formatDateTime } from "@/lib/format/date";
import { legalStrings as t } from "@/lib/strings/legal";
import { ACCEPTANCES_PAGE, acceptancesQuery, loadAcceptancesCsv } from "./api";

/** Who accepted which Terms or Privacy version at sign-up, with a CSV download (P3). */
export function AcceptancesTable() {
	const [page, setPage] = useState(1);
	const [error, setError] = useState<string | null>(null);
	const q = useQuery(acceptancesQuery((page - 1) * ACCEPTANCES_PAGE));
	const download = async () => {
		setError(null);
		try {
			downloadText(await loadAcceptancesCsv(), "legal-acceptances.csv");
		} catch {
			setError(t.acceptances.downloadFailed);
		}
	};
	return (
		<Panel
			title={t.acceptances.title}
			sub={t.acceptances.lead}
			aside={
				<Button variant="outline" size="sm" data-testid="acceptances-download" onClick={() => void download()}>
					<Download aria-hidden />
					{t.acceptances.download}
				</Button>
			}
		>
			<div className="grid gap-3 p-4" data-testid="acceptances">
				{error ? <FormAlert>{error}</FormAlert> : null}
				{q.isPending ? (
					<CardSkeleton lines={4} />
				) : q.isError ? (
					<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
				) : (
					<>
						<DataTable
							caption={t.acceptances.caption}
							rows={q.data.items}
							rowKey={(r) => r.id}
							empty={<p className="m-0 p-3 text-foreground-2">{t.acceptances.empty}</p>}
							columns={[
								{ id: "when", header: t.acceptances.when, cell: (r) => formatDateTime(r.accepted_at) },
								{ id: "email", header: t.acceptances.email, cell: (r) => r.email, rowHeader: true },
								{ id: "workspace", header: t.acceptances.workspace, cell: (r) => r.workspace ?? "—" },
								{ id: "page", header: t.acceptances.page, cell: (r) => r.page },
								{ id: "version", header: t.acceptances.version, cell: (r) => r.version, align: "right" },
							]}
						/>
						<Pager page={page} pageSize={ACCEPTANCES_PAGE} total={q.data.total} onPage={setPage} />
					</>
				)}
			</div>
		</Panel>
	);
}
