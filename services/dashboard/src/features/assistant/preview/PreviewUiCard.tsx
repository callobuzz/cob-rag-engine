import { Markdown } from "@/lib/markdown";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { HandoffCardBlock, LeadFormBlock } from "../types";

function fieldsText(form: LeadFormBlock): string {
	return form.fields.map((f) => (f.required ? `${f.label} (${t.preview.required})` : f.label)).join(", ");
}

function LeadFormCard({ form, nested }: { form: LeadFormBlock; nested?: boolean }) {
	return (
		<section
			aria-label={nested ? undefined : t.preview.leadForm}
			data-testid={nested ? undefined : "preview-lead-form"}
			className="grid gap-1 rounded-md border border-dashed border-border bg-card p-2.5 text-[12.5px]"
		>
			<b>{nested ? fill(t.preview.callbackForm, { list: fieldsText(form) }) : t.preview.leadForm}</b>
			{nested ? null : (
				<>
					<span>{form.title}</span>
					<span>{fill(t.preview.leadFields, { list: fieldsText(form) })}</span>
					<span className="text-muted-foreground">
						<Markdown text={form.notice} inline />
					</span>
				</>
			)}
		</section>
	);
}

function HandoffCard({ card }: { card: HandoffCardBlock }) {
	return (
		<section
			aria-label={t.preview.handoffCard}
			data-testid="preview-handoff-card"
			className="grid gap-1.5 rounded-md border border-dashed border-border bg-card p-2.5 text-[12.5px]"
		>
			<b>
				{t.preview.handoffCard} · {card.open_now ? t.preview.openNow : t.preview.closedNow}
			</b>
			<Markdown text={card.message} inline />
			<ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
				{card.channels.map((c) => (
					<li key={`${c.kind}-${c.label}`}>
						<button
							type="button"
							disabled
							className="rounded-sm border border-input bg-secondary px-2 py-1 text-[12px] font-semibold opacity-80"
						>
							{c.label}
						</button>
						{c.url ? <span className="ml-1.5 break-all text-muted-foreground">{c.url}</span> : null}
					</li>
				))}
			</ul>
			{card.callback_form ? <LeadFormCard form={card.callback_form} nested /> : null}
		</section>
	);
}

/**
 * A lead form or a talk-to-a-person card as the engine sent it, drawn read-only: the data shape is
 * fixed by the reply envelope, so the lead and handoff work only restyle these cards.
 */
export function PreviewUiCard({ block }: { block: Record<string, unknown> }) {
	if (block.kind === "lead_form") return <LeadFormCard form={block as unknown as LeadFormBlock} />;
	if (block.kind === "handoff_card") return <HandoffCard card={block as unknown as HandoffCardBlock} />;
	return null;
}
