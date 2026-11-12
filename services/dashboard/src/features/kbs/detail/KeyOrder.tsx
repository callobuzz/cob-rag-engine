import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { Panel } from "../ui";

/** Mockup `.order`: which key pays for an answer, first one found wins. */
export function KeyOrder({ tenant }: { tenant: string }) {
	return (
		<Panel title={t.model.orderTitle} sub={t.model.orderSub}>
			<ol className="m-0 grid p-4">
				{t.model.order.map((o, i) => (
					<li
						key={o.b}
						className="grid grid-cols-[28px_minmax(0,1fr)] gap-2.5 border-b border-dashed border-border py-2.5 last:border-b-0"
					>
						<span
							aria-hidden
							className="grid size-6 place-items-center rounded-full bg-primary-soft text-xs font-bold text-primary"
						>
							{i + 1}
						</span>
						<div>
							<b className="block">{fill(o.b, { tenant })}</b>
							<span className="text-[13px] text-foreground-2">{fill(o.t, { tenant })}</span>
						</div>
					</li>
				))}
			</ol>
		</Panel>
	);
}
