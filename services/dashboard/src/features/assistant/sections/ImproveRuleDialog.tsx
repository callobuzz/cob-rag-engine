import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { toApiError } from "@/lib/api/errors";
import { assistantStrings as a } from "@/lib/strings/assistant";
import { useImproveRule } from "../api";

const t = a.rules.improve;

interface Props {
	kb: string;
	rule: { id: string; type: string; instruction: string };
	onAccept: (text: string) => void;
	onClose: () => void;
}

/**
 * "Improve this rule" (design 4.5): the owner's wording next to a suggestion, and any rules it may
 * clash with. Mount it to ask; nothing is saved until the person uses the suggestion, and then
 * only into the form (the section's own Save stays the one write). The suggestion is plain text.
 */
export function ImproveRuleDialog({ kb, rule, onAccept, onClose }: Props) {
	const improve = useImproveRule(kb);
	const { mutate } = improve;
	const asked = useRef(false);
	useEffect(() => {
		if (asked.current) return;
		asked.current = true;
		mutate({ instruction: rule.instruction, type: rule.type as never, rule_id: rule.id });
	}, [mutate, rule.instruction, rule.type, rule.id]);

	const err = improve.error ? toApiError(improve.error) : null;
	const suggestion = improve.data?.improved;
	const same = suggestion !== undefined && suggestion.trim() === rule.instruction.trim();
	return (
		<Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>{t.title}</DialogTitle>
					<DialogDescription>{t.body}</DialogDescription>
				</DialogHeader>
				<div className="grid gap-3 text-[13.5px]">
					<section aria-label={t.yours} className="grid gap-1">
						<h3 className="m-0 text-[12.5px] font-semibold text-muted-foreground">{t.yours}</h3>
						<p className="m-0 whitespace-pre-wrap">{rule.instruction}</p>
					</section>
					<section aria-label={t.suggested} aria-busy={improve.isPending} className="grid gap-1">
						<h3 className="m-0 text-[12.5px] font-semibold text-muted-foreground">{t.suggested}</h3>
						{improve.isPending ? (
							<p className="m-0 text-muted-foreground" role="status">
								{t.working}
							</p>
						) : null}
						{suggestion !== undefined ? (
							<p className="m-0 whitespace-pre-wrap">{same ? t.unchanged : suggestion}</p>
						) : null}
						{err ? (
							<p role="alert" className="m-0">
								{err.code === "assistant_llm_unavailable" ? t.noModel : err.detail}
							</p>
						) : null}
						{improve.data?.notes && !same ? (
							<p className="m-0 text-[12.5px] text-muted-foreground">{improve.data.notes}</p>
						) : null}
					</section>
					{improve.data?.conflicts?.length ? (
						<section aria-label={t.conflicts} className="grid gap-1">
							<h3 className="m-0 text-[12.5px] font-semibold text-muted-foreground">{t.conflicts}</h3>
							<ul className="m-0 grid gap-1 pl-5">
								{improve.data.conflicts.map((c) => (
									<li key={c.rule_id}>
										<code>{c.rule_id}</code>: {c.reason}
									</li>
								))}
							</ul>
						</section>
					) : null}
				</div>
				<DialogFooter>
					<Button type="button" variant="ghost" onClick={onClose}>
						{t.keep}
					</Button>
					<Button
						type="button"
						disabled={suggestion === undefined || same}
						onClick={() => suggestion !== undefined && onAccept(suggestion)}
					>
						{t.accept}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
