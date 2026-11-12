import { zodResolver } from "@hookform/resolvers/zod";
import { useId } from "react";
import { Controller } from "react-hook-form";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { setupStrings as t } from "@/lib/strings/setup";
import { useStartSetup } from "../api";
import { ChoiceCards } from "../ChoiceCards";

export const TEMPLATE_KEYS = [
	"clinic",
	"store",
	"coaching",
	"saas",
	"real_estate",
	"government",
	"restaurant",
	"generic_support",
] as const;
export type TemplateKey = (typeof TEMPLATE_KEYS)[number];

export const typeLabel = (key: string): string => (t.start.types as Record<string, string>)[key] ?? key;

const schema = z.discriminatedUnion("path", [
	z.object({
		path: z.literal("website"),
		website_url: z
			.string()
			.trim()
			.max(2048)
			.regex(/^https?:\/\//i, t.start.urlInvalid),
		business_type: z.string().optional(),
	}),
	z.object({ path: z.literal("type"), business_type: z.enum(TEMPLATE_KEYS), website_url: z.string().optional() }),
]);
type Form = z.input<typeof schema>;

/** Step 0: a website to read, or a business type to start from. Starting replaces an unfinished setup. */
export function StartStep({
	kb,
	hasDraft,
	initialType,
	onStarted,
}: {
	kb: string;
	hasDraft: boolean;
	initialType?: boolean;
	onStarted: (sid: string) => void;
}) {
	const start = useStartSetup(kb);
	const ids = { url: useId(), type: useId() };
	const form = useAppForm<Form>({
		resolver: zodResolver(schema),
		defaultValues: { path: initialType ? "type" : "website", website_url: "", business_type: "clinic" },
	});
	const path = form.watch("path");
	const errs = form.formState.errors;
	const submit = useAppSubmit(form, async (v) => {
		const body = v.path === "website" ? { website_url: v.website_url } : { business_type: v.business_type };
		const out = await start.mutateAsync(body);
		onStarted(out.id);
	});
	const urlError = "website_url" in errs ? (errs.website_url?.message ?? null) : null;
	return (
		<form onSubmit={submit.onSubmit} noValidate className="grid gap-4 rounded-lg border border-border bg-card p-5">
			<div className="grid gap-1">
				<h2 className="m-0 text-lg font-semibold">{t.start.heading}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">{t.start.restartNote}</p>
			</div>
			{hasDraft ? (
				<p role="status" className="m-0 rounded-md bg-warning-soft p-3 text-[13px] text-warning">
					{t.start.draftWarning}
				</p>
			) : null}
			{submit.error ? <KbErrorAlert error={submit.error} /> : null}
			<Controller
				control={form.control}
				name="path"
				render={({ field }) => (
					<ChoiceCards
						legend={t.start.group}
						type="radio"
						name="setup-path"
						value={[field.value]}
						onChange={([v]) => field.onChange(v === "type" ? "type" : "website")}
						choices={[
							{ value: "website", label: t.start.website, hint: t.start.websiteHint },
							{ value: "type", label: t.start.type, hint: t.start.typeHint },
						]}
					/>
				)}
			/>
			{path === "website" ? (
				<Field id={ids.url} label={t.start.urlLabel} error={urlError}>
					<Input
						id={ids.url}
						type="url"
						autoComplete="url"
						placeholder={t.start.urlPlaceholder}
						aria-invalid={urlError ? true : undefined}
						aria-describedby={describedBy(ids.url, { error: !!urlError })}
						{...form.register("website_url")}
					/>
				</Field>
			) : (
				<Controller
					control={form.control}
					name="business_type"
					render={({ field }) => (
						<ChoiceCards
							legend={t.start.typeLabel}
							type="radio"
							name="setup-type"
							value={[String(field.value ?? "clinic")]}
							onChange={([v]) => field.onChange(v)}
							choices={TEMPLATE_KEYS.map((k) => ({ value: k, label: typeLabel(k) }))}
						/>
					)}
				/>
			)}
			<div className="flex justify-end">
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.start.starting}
				>
					{t.start.continue}
				</SubmitButton>
			</div>
		</form>
	);
}
