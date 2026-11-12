import { Check, Minus } from "lucide-react";
import { Panel } from "@/features/kbs/ui";
import { teamStrings as t } from "@/lib/strings/accounts";

const t2 = t.roles;

function Cell({ v }: { v: "yes" | "no" | "view" }) {
	if (v === "view") return <span className="text-[12.5px] text-foreground-2">{t2.view}</span>;
	return v === "yes" ? (
		<span className="inline-flex text-success">
			<Check className="size-4" aria-hidden />
			<span className="sr-only">{t2.yes}</span>
		</span>
	) : (
		<span className="inline-flex text-muted-foreground">
			<Minus className="size-4" aria-hidden />
			<span className="sr-only">{t2.no}</span>
		</span>
	);
}

/** "What each role can do" (mockup `.roles-tbl`, the design's role table verbatim). */
export function RolesTable() {
	return (
		<Panel title={t2.title} sub={t2.sub} className="self-start">
			<table className="w-full border-collapse text-[13px]">
				<caption className="sr-only">{t2.caption}</caption>
				<thead>
					<tr>
						<th
							scope="col"
							className="density-cell border-b border-border text-left font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase"
						>
							{t2.cols.action}
						</th>
						<th
							scope="col"
							className="density-cell w-[76px] border-b border-border text-center font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase"
						>
							{t2.cols.owner}
						</th>
						<th
							scope="col"
							className="density-cell w-[76px] border-b border-border text-center font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase"
						>
							{t2.cols.member}
						</th>
					</tr>
				</thead>
				<tbody>
					{t2.rows.map(([action, owner, member]) => (
						<tr key={action} className="border-b border-border last:border-b-0">
							<th scope="row" className="density-cell text-left font-normal">
								{action}
							</th>
							<td className="density-cell text-center">
								<Cell v={owner} />
							</td>
							<td className="density-cell text-center">
								<Cell v={member} />
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</Panel>
	);
}
