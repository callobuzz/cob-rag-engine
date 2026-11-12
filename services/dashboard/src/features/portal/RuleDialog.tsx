import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId } from "react";
import { Controller } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { portal, type Rule, type RuleCreate, useRuleMutation } from "./api";
import { portalMessage } from "./messages";

export const RULE_TYPES = ["mime_type", "size", "confidence", "always"] as const;
export type RuleType = (typeof RULE_TYPES)[number];
const d = t.ruleDialog;

const number = (v: string) => (v.trim() === "" ? null : Number(v));

/** The fields each rule type needs (the engine's `_CONDITION_REQUIREMENTS`), checked before sending. */
export const ruleSchema = z
	.object({
		rule_type: z.enum(RULE_TYPES),
		mime_types: z.string(),
		size_gt_mb: z.string(),
		size_lt_mb: z.string(),
		confidence_below: z.string(),
		action: z.enum(["stage", "direct_ingest"]),
		priority: z.string(),
		enabled: z.boolean(),
	})
	.superRefine((v, ctx) => {
		if (v.rule_type === "mime_type" && splitTypes(v.mime_types).length === 0) {
			ctx.addIssue({ code: "custom", path: ["mime_types"], message: d.mimeRequired });
		}
		if (v.rule_type === "size") {
			const gt = number(v.size_gt_mb);
			const lt = number(v.size_lt_mb);
			if (gt === null && lt === null) ctx.addIssue({ code: "custom", path: ["size_gt_mb"], message: d.sizeRequired });
			for (const [key, n] of [
				["size_gt_mb", gt],
				["size_lt_mb", lt],
			] as const) {
				if (n !== null && (!Number.isFinite(n) || n < 0))
					ctx.addIssue({ code: "custom", path: [key], message: d.sizeBad });
			}
		}
		if (v.rule_type === "confidence") {
			const c = number(v.confidence_below);
			if (c === null || !Number.isFinite(c) || c < 0 || c > 1) {
				ctx.addIssue({ code: "custom", path: ["confidence_below"], message: d.confidenceBad });
			}
		}
		const p = Number(v.priority);
		if (!/^\d+$/.test(v.priority.trim()) || p > 10000) {
			ctx.addIssue({ code: "custom", path: ["priority"], message: d.priorityBad });
		}
	});
export type RuleForm = z.infer<typeof ruleSchema>;

export function splitTypes(text: string): string[] {
	return [
		...new Set(
			text
				.split(/[,\s]+/)
				.map((s) => s.trim())
				.filter(Boolean),
		),
	];
}

/** Form values → the create/update body: only the condition keys of the chosen type. */
export function toRuleBody(v: RuleForm): RuleCreate {
	const condition: Record<string, unknown> = {};
	if (v.rule_type === "mime_type") {
		const types = splitTypes(v.mime_types);
		if (types.length === 1) condition.mime_type = types[0];
		else condition.mime_types = types;
	}
	if (v.rule_type === "size") {
		const gt = number(v.size_gt_mb);
		const lt = number(v.size_lt_mb);
		if (gt !== null) condition.size_gt_mb = gt;
		if (lt !== null) condition.size_lt_mb = lt;
	}
	if (v.rule_type === "confidence") condition.confidence_below = number(v.confidence_below);
	return { rule_type: v.rule_type, condition, action: v.action, priority: Number(v.priority), enabled: v.enabled };
}

function fromRule(r: Rule | null): RuleForm {
	const c = (r?.condition ?? {}) as Record<string, unknown>;
	const types = Array.isArray(c.mime_types) ? c.mime_types.map(String) : c.mime_type ? [String(c.mime_type)] : [];
	const s = (x: unknown) => (typeof x === "number" ? String(x) : "");
	return {
		rule_type: (RULE_TYPES as readonly string[]).includes(r?.rule_type ?? "")
			? (r?.rule_type as RuleType)
			: "mime_type",
		mime_types: types.join(", "),
		size_gt_mb: s(c.size_gt_mb),
		size_lt_mb: s(c.size_lt_mb),
		confidence_below: s(c.confidence_below),
		action: r?.action === "direct_ingest" ? "direct_ingest" : "stage",
		priority: String(r?.priority ?? 100),
		enabled: r?.enabled ?? true,
	};
}

/** Create or edit a routing rule (rule type → its condition fields, action, priority 0–10000, enabled). */
export function RuleDialog({
	open,
	onOpenChange,
	kb,
	rule,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	kb: string;
	/** The rule being edited; null creates one. */
	rule: Rule | null;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
				{open ? <RuleFormBody kb={kb} rule={rule} onDone={() => onOpenChange(false)} /> : null}
			</DialogContent>
		</Dialog>
	);
}

function RuleFormBody({ kb, rule, onDone }: { kb: string; rule: Rule | null; onDone: () => void }) {
	const save = useRuleMutation(kb, (body: RuleCreate) =>
		rule ? portal.updateRule(kb, rule.id, body) : portal.createRule(kb, body),
	);
	const ids = {
		type: useId(),
		mime: useId(),
		gt: useId(),
		lt: useId(),
		conf: useId(),
		action: useId(),
		priority: useId(),
		enabled: useId(),
	};
	const form = useAppForm<RuleForm>({ resolver: zodResolver(ruleSchema), defaultValues: fromRule(rule) });
	const errs = form.formState.errors;
	const type = form.watch("rule_type");
	useEffect(() => {
		form.setFocus("rule_type");
	}, [form]);

	const submit = useAppSubmit(form, async (v) => {
		await save.mutateAsync(toRuleBody(v));
		toast.success(rule ? d.updated : d.created);
		onDone();
	});

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{rule ? d.editTitle : d.createTitle}</DialogTitle>
				<DialogDescription>{d.lead}</DialogDescription>
			</DialogHeader>
			{submit.error ? (
				<FormAlert>
					{submit.error.status === 422
						? submit.error.detail || portalMessage(submit.error).title
						: portalMessage(submit.error).title}
				</FormAlert>
			) : null}
			<Field id={ids.type} label={d.type}>
				<NativeSelect id={ids.type} {...form.register("rule_type")}>
					{RULE_TYPES.map((k) => (
						<option key={k} value={k}>
							{t.rules.types[k]}
						</option>
					))}
				</NativeSelect>
			</Field>
			{type === "mime_type" ? (
				<Field id={ids.mime} label={d.mimeTypes} hint={d.mimeHint} error={errs.mime_types?.message}>
					<Input
						id={ids.mime}
						className="font-mono"
						autoComplete="off"
						placeholder="application/pdf, text/html"
						aria-invalid={!!errs.mime_types}
						aria-describedby={describedBy(ids.mime, { hint: true, error: !!errs.mime_types })}
						{...form.register("mime_types")}
					/>
				</Field>
			) : null}
			{type === "size" ? (
				<div className="grid gap-4 min-[600px]:grid-cols-2">
					<Field id={ids.gt} label={d.sizeGt} error={errs.size_gt_mb?.message}>
						<Input
							id={ids.gt}
							type="number"
							inputMode="decimal"
							min={0}
							step="any"
							aria-invalid={!!errs.size_gt_mb}
							aria-describedby={describedBy(ids.gt, { error: !!errs.size_gt_mb })}
							{...form.register("size_gt_mb")}
						/>
					</Field>
					<Field id={ids.lt} label={d.sizeLt} error={errs.size_lt_mb?.message}>
						<Input
							id={ids.lt}
							type="number"
							inputMode="decimal"
							min={0}
							step="any"
							aria-invalid={!!errs.size_lt_mb}
							aria-describedby={describedBy(ids.lt, { error: !!errs.size_lt_mb })}
							{...form.register("size_lt_mb")}
						/>
					</Field>
				</div>
			) : null}
			{type === "confidence" ? (
				<Field id={ids.conf} label={d.confidence} hint={d.confidenceHint} error={errs.confidence_below?.message}>
					<Input
						id={ids.conf}
						type="number"
						inputMode="decimal"
						min={0}
						max={1}
						step="0.05"
						aria-invalid={!!errs.confidence_below}
						aria-describedby={describedBy(ids.conf, { hint: true, error: !!errs.confidence_below })}
						{...form.register("confidence_below")}
					/>
				</Field>
			) : null}
			{type === "always" ? <FormAlert tone="info">{d.alwaysNote}</FormAlert> : null}
			<div className="grid gap-4 min-[600px]:grid-cols-[minmax(0,1fr)_160px]">
				<Field id={ids.action} label={d.action}>
					<NativeSelect id={ids.action} {...form.register("action")}>
						<option value="stage">{t.rules.actions.stage}</option>
						<option value="direct_ingest">{t.rules.actions.direct_ingest}</option>
					</NativeSelect>
				</Field>
				<Field id={ids.priority} label={d.priority} hint={d.priorityHint} error={errs.priority?.message}>
					<Input
						id={ids.priority}
						type="number"
						inputMode="numeric"
						min={0}
						max={10000}
						step={1}
						aria-invalid={!!errs.priority}
						aria-describedby={describedBy(ids.priority, { hint: true, error: !!errs.priority })}
						{...form.register("priority")}
					/>
				</Field>
			</div>
			<div className="flex items-center gap-3">
				<Controller
					control={form.control}
					name="enabled"
					render={({ field }) => <Switch id={ids.enabled} checked={field.value} onCheckedChange={field.onChange} />}
				/>
				<label htmlFor={ids.enabled} className="text-[13px] font-semibold">
					{d.enabled}
				</label>
			</div>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked} pendingLabel={d.saving}>
					{d.save}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
