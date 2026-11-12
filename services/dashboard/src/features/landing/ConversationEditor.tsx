import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import type { LText } from "./draft";
import { LeadFields, type Rec } from "./fields";
import { LocalizedField } from "./LocalizedField";
import { VISITOR_LINE_MAX } from "./sections";

const MAX_TURNS = 6;
const MIN_TURNS = 2;

/**
 * The sample conversation in the top of the page: who says what, the source chip under an
 * assistant line, and an optional lead card. Lines alternate visitor, assistant, visitor, ... (the
 * engine requires it), so a line is added at the end with the next speaker and only the last line
 * can be removed.
 */
export function ConversationEditor({
	value,
	onChange,
	path,
	limitBase,
}: {
	value: Rec;
	onChange: (next: Rec) => void;
	path: string;
	limitBase: string;
}) {
	const turns = (value.turns as Rec[] | undefined) ?? [];
	const base = `${limitBase}.conversation`;
	const set = (patch: Rec) => onChange({ ...value, ...patch });
	const setTurn = (i: number, patch: Rec) =>
		set({ turns: turns.map((turn, j) => (j === i ? { ...turn, ...patch } : turn)) });
	const nextRole = turns.length % 2 === 0 ? "visitor" : "assistant";

	return (
		<fieldset className="grid gap-4 rounded-sm border border-border p-3" data-testid="conversation-editor">
			<legend className="px-1 text-[13px] font-semibold">{t.conversation.title}</legend>
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.conversation.about}</p>
			<div className="grid gap-4 min-[861px]:grid-cols-2">
				<LocalizedField
					path={`${path}.assistant_name`}
					label={t.conversation.assistantName}
					value={value.assistant_name as LText}
					onChange={(v) => set({ assistant_name: v ?? { en: "" } })}
					limitKey={`${base}.assistant_name`}
				/>
				<LocalizedField
					path={`${path}.business_name`}
					label={t.conversation.businessName}
					value={value.business_name as LText}
					onChange={(v) => set({ business_name: v ?? { en: "" } })}
					limitKey={`${base}.business_name`}
				/>
			</div>
			<div className="grid gap-3">
				{turns.map((turn, i) => {
					const visitor = turn.role === "visitor";
					return (
						// biome-ignore lint/suspicious/noArrayIndexKey: lines have no id; their place is their identity
						<div key={i} className="grid gap-3 rounded-sm bg-secondary/40 p-3" data-testid={`turn-${i}`}>
							<div className="flex items-center justify-between gap-2">
								<strong className="text-[12.5px] text-foreground-2">
									{fill(t.conversation.line, {
										n: i + 1,
										who: visitor ? t.conversation.visitor : t.conversation.assistant,
									})}
								</strong>
								{i === turns.length - 1 && turns.length > MIN_TURNS ? (
									<Button
										type="button"
										size="icon-sm"
										variant="ghost"
										aria-label={fill(t.conversation.removeLine, { n: i + 1 })}
										onClick={() => set({ turns: turns.slice(0, -1) })}
									>
										<Trash2 aria-hidden />
									</Button>
								) : null}
							</div>
							<LocalizedField
								path={`${path}.turns[${i}].text`}
								label={t.conversation.text}
								value={turn.text as LText}
								long
								onChange={(v) => setTurn(i, { text: v ?? { en: "" } })}
								limitKey={`${base}.turns.text`}
								maxOverride={visitor ? VISITOR_LINE_MAX : undefined}
							/>
							{visitor ? null : (
								<LocalizedField
									path={`${path}.turns[${i}].source`}
									label={t.conversation.source}
									value={turn.source as LText | null}
									onChange={(v) => setTurn(i, { source: v })}
									limitKey={`${base}.turns.source`}
									hint={t.conversation.sourceHint}
								/>
							)}
						</div>
					);
				})}
				{turns.length < MAX_TURNS ? (
					<div>
						<Button
							type="button"
							size="sm"
							variant="outline"
							onClick={() => set({ turns: [...turns, { role: nextRole, text: { en: "" }, source: null }] })}
						>
							<Plus aria-hidden />
							{nextRole === "visitor" ? t.conversation.addVisitor : t.conversation.addAssistant}
						</Button>
					</div>
				) : null}
			</div>
			<LeadFields
				optional
				label={t.conversation.leadCard}
				value={(value.lead as Rec | null | undefined) ?? null}
				onChange={(v) => set({ lead: v })}
				path={`${path}.lead`}
				limitBase={`${base}.lead`}
			/>
		</fieldset>
	);
}
