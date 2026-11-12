import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { toApiError } from "@/lib/api/errors";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { cn } from "@/lib/utils";
import { KbErrorAlert } from "../../kbs/KbErrorAlert";
import { templatesQuery, useDraftFromTemplate } from "../api";
import { TEMPLATE_KEYS, type TemplateKey } from "../schemas";
import type { AssistantState } from "../types";

/**
 * The eight business templates as a radio group (arrow keys move the choice). Using one replaces
 * the draft, so an existing draft is confirmed first; the live version is never touched.
 */
export function TemplatePicker({
	kb,
	state,
	open,
	onOpenChange,
}: {
	kb: string;
	state: AssistantState;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	const q = useQuery({ ...templatesQuery, enabled: open });
	const use = useDraftFromTemplate(kb);
	const keepId = useId();
	const [picked, setPicked] = useState<TemplateKey | null>(null);
	const [keep, setKeep] = useState(true);
	const [confirm, setConfirm] = useState(false);
	const [error, setError] = useState<unknown>(null);
	const current = (state.draft ?? state.published)?.config.identity.business_type;
	const items = q.data ?? [];
	const keys = items.length ? items.map((i) => i.key) : [...TEMPLATE_KEYS];
	const selected: TemplateKey | null = picked ?? current ?? keys[0] ?? null;

	useEffect(() => {
		if (open) {
			setPicked(null);
			setError(null);
		}
	}, [open]);

	const run = async () => {
		if (!selected) return;
		setError(null);
		try {
			await use.mutateAsync({
				template: selected,
				keep_identity: keep,
				base_revision: state.draft?.draft_revision ?? 0,
			});
			setConfirm(false);
			onOpenChange(false);
			toast.success(t.templates.usedToast);
		} catch (e) {
			setConfirm(false);
			setError(toApiError(e));
		}
	};

	const move = (dir: 1 | -1) => {
		if (!selected) return;
		const i = keys.indexOf(selected);
		const next = keys[(i + dir + keys.length) % keys.length];
		if (next) {
			setPicked(next);
			document.getElementById(`tpl-${next}`)?.focus();
		}
	};

	return (
		<>
			<Dialog open={open} onOpenChange={onOpenChange}>
				<DialogContent className="max-w-[720px]">
					<DialogHeader>
						<DialogTitle>{t.templates.title}</DialogTitle>
						<DialogDescription>{t.templates.sub}</DialogDescription>
					</DialogHeader>
					{error ? <KbErrorAlert error={error} /> : null}
					{q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
					{q.isPending ? <p className="m-0 text-[13px] text-muted-foreground">{t.templates.loading}</p> : null}
					<div
						role="radiogroup"
						aria-label={t.templates.groupLabel}
						className="grid max-h-[50vh] gap-2 overflow-y-auto min-[620px]:grid-cols-2"
					>
						{items.map((it) => {
							const on = it.key === selected;
							return (
								// biome-ignore lint/a11y/useSemanticElements: a card with radio semantics and arrow-key movement
								<button
									key={it.key}
									id={`tpl-${it.key}`}
									type="button"
									role="radio"
									aria-checked={on}
									tabIndex={on ? 0 : -1}
									onClick={() => setPicked(it.key)}
									onKeyDown={(e) => {
										if (e.key === "ArrowDown" || e.key === "ArrowRight") {
											e.preventDefault();
											move(1);
										} else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
											e.preventDefault();
											move(-1);
										}
									}}
									className={cn(
										"grid gap-0.5 rounded-lg border p-3 text-left",
										on ? "border-primary bg-primary-soft" : "border-border bg-card hover:bg-accent",
									)}
								>
									<span className="text-[14px] font-semibold">{it.name}</span>
									<span className="text-[12.5px] text-muted-foreground">{it.description}</span>
								</button>
							);
						})}
					</div>
					<div className="flex items-center gap-2">
						<Checkbox id={keepId} checked={keep} onCheckedChange={(v) => setKeep(v === true)} />
						<label htmlFor={keepId} className="text-[13px]">
							{t.templates.keepIdentity}
						</label>
					</div>
					<DialogFooter>
						<Button variant="ghost" onClick={() => onOpenChange(false)}>
							{t.publish.cancel}
						</Button>
						<Button disabled={!selected || use.isPending} onClick={() => (state.draft ? setConfirm(true) : void run())}>
							{t.templates.use}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
			<ConfirmDialog
				open={confirm}
				onOpenChange={setConfirm}
				title={t.templates.replaceTitle}
				body={t.templates.replaceBody}
				confirmLabel={t.templates.replaceConfirm}
				pending={use.isPending}
				onConfirm={() => void run()}
			/>
		</>
	);
}
