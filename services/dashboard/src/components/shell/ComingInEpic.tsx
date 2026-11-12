import { pageById } from "@/lib/nav";
import { en, fill } from "@/lib/strings/en";
import { PageHeader } from "./PageHeader";

/** Placeholder for a route a later epic builds (mockup `.soon`). */
export function ComingInEpic({ page, epic, title }: { page: string; epic?: string; title?: string }) {
	const meta = pageById(page);
	const e = epic ?? meta.epic;
	return (
		<>
			<PageHeader title={title ?? meta.label} description={meta.summary} />
			<section
				data-testid="coming-in-epic"
				data-epic={e}
				className="grid items-center gap-6 rounded-lg border border-border bg-card px-8 py-10 min-[861px]:grid-cols-2"
			>
				<div>
					<h2 className="text-xl font-bold">{en.shell.comingTitle}</h2>
					<p className="mt-2 mb-0 text-foreground-2">{fill(en.shell.comingLead, { epic: e })}</p>
				</div>
				<ul className="m-0 grid list-disc gap-1.5 pl-[18px] text-foreground-2">
					{meta.items.map((x) => (
						<li key={x}>{x}</li>
					))}
				</ul>
			</section>
		</>
	);
}
