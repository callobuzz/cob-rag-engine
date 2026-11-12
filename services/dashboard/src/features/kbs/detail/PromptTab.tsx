import { zodResolver } from "@hookform/resolvers/zod";
import { Link } from "@tanstack/react-router";
import { MessageSquare } from "lucide-react";
import { useId } from "react";
import { useWatch } from "react-hook-form";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { type CorpusDetail, useUpdateCorpus } from "../api";
import { KbErrorAlert } from "../KbErrorAlert";
import { type PromptConfigForm, promptConfigSchema, strOrNull } from "../schemas";
import { FormFoot, Panel, SettingsGrid } from "../ui";
import { fieldFromPath, useUnsavedGuard } from "./shared";

/** Reply languages (AUDIT S21): value sent as `response_language`; "" = same as the question. */
export const LANGUAGES = [
	{ value: "English", label: "English" },
	{ value: "Hindi", label: "हिन्दी (Hindi)" },
	{ value: "Marathi", label: "मराठी (Marathi)" },
	{ value: "Tamil", label: "தமிழ் (Tamil)" },
	{ value: "Telugu", label: "తెలుగు (Telugu)" },
	{ value: "Bengali", label: "বাংলা (Bengali)" },
	{ value: "Gujarati", label: "ગુજરાતી (Gujarati)" },
	{ value: "Kannada", label: "ಕನ್ನಡ (Kannada)" },
] as const;
const OTHER = "__other";

function formFrom(kb: CorpusDetail): PromptConfigForm {
	const c = kb.prompt_config ?? {};
	const lang = typeof c.response_language === "string" ? c.response_language : "";
	const known = lang === "" || LANGUAGES.some((l) => l.value === lang);
	return {
		system_prompt: typeof c.system_prompt === "string" ? c.system_prompt : "",
		constraints: typeof c.constraints === "string" ? c.constraints : "",
		language: known ? lang : OTHER,
		language_other: known ? "" : lang,
	};
}

/** Prompt (mockup `t-prompt`): members may save; only `prompt_config` is sent. */
export function PromptTab({ kb }: { kb: CorpusDetail }) {
	const update = useUpdateCorpus(kb.id);
	const ids = { sys: useId(), rules: useId(), lang: useId(), other: useId() };
	const form = useAppForm<PromptConfigForm>({ resolver: zodResolver(promptConfigSchema), defaultValues: formFrom(kb) });
	const errs = form.formState.errors;
	const dirty = form.formState.isDirty;
	const guard = useUnsavedGuard(dirty);
	const language = useWatch({ control: form.control, name: "language" });

	const submit = useAppSubmit(
		form,
		async (v) => {
			const d = form.formState.dirtyFields;
			const prompt: Record<string, string | null> = {};
			if (d.system_prompt) prompt.system_prompt = strOrNull(v.system_prompt);
			if (d.constraints) prompt.constraints = strOrNull(v.constraints);
			if (d.language || d.language_other) {
				prompt.response_language = v.language === OTHER ? strOrNull(v.language_other) : strOrNull(v.language);
			}
			if (Object.keys(prompt).length === 0) return;
			const next = await update.mutateAsync({ prompt_config: prompt });
			form.reset(formFrom(next));
			toast.success(fill(t.detail.savedToast, { what: t.prompt.savedWhat }), {
				description: "New answers use the updated instructions.",
			});
		},
		{
			fieldName: (path) => {
				const name = fieldFromPath(path, "prompt_config");
				return (name === "response_language" ? "language_other" : name) as keyof PromptConfigForm | null;
			},
		},
	);

	return (
		<SettingsGrid>
			<Panel title={t.prompt.title} sub={t.prompt.sub}>
				<form onSubmit={(e) => void submit.onSubmit(e)} noValidate>
					<div className="grid gap-4 p-4">
						{submit.error ? <KbErrorAlert error={submit.error} /> : null}
						<Field
							id={ids.sys}
							label={t.prompt.instructions}
							hint={t.prompt.instructionsHint}
							error={errs.system_prompt?.message}
						>
							<Textarea
								id={ids.sys}
								rows={6}
								placeholder={t.prompt.instructionsPlaceholder}
								aria-invalid={!!errs.system_prompt}
								aria-describedby={describedBy(ids.sys, { hint: true, error: !!errs.system_prompt })}
								{...form.register("system_prompt")}
							/>
						</Field>
						<Field id={ids.rules} label={t.prompt.rules} hint={t.prompt.rulesHint} error={errs.constraints?.message}>
							<Textarea
								id={ids.rules}
								rows={3}
								placeholder={t.prompt.rulesPlaceholder}
								aria-invalid={!!errs.constraints}
								aria-describedby={describedBy(ids.rules, { hint: true, error: !!errs.constraints })}
								{...form.register("constraints")}
							/>
						</Field>
						<div className="grid gap-4 min-[861px]:grid-cols-2">
							<Field id={ids.lang} label={t.prompt.language} hint={t.prompt.languageHint}>
								<NativeSelect
									id={ids.lang}
									aria-describedby={describedBy(ids.lang, { hint: true })}
									{...form.register("language")}
								>
									<option value="">{t.prompt.sameAsQuestion}</option>
									{LANGUAGES.map((l) => (
										<option key={l.value} value={l.value}>
											{l.label}
										</option>
									))}
									<option value={OTHER}>{t.prompt.other}</option>
								</NativeSelect>
							</Field>
							{language === OTHER ? (
								<Field
									id={ids.other}
									label={t.prompt.otherLabel}
									hint={t.prompt.otherHint}
									error={errs.language_other?.message}
								>
									<Input
										id={ids.other}
										maxLength={50}
										aria-invalid={!!errs.language_other}
										aria-describedby={describedBy(ids.other, { hint: true, error: !!errs.language_other })}
										{...form.register("language_other")}
									/>
								</Field>
							) : null}
						</div>
					</div>
					<FormFoot>
						<Button type="button" variant="ghost" disabled={!dirty} onClick={() => form.reset(formFrom(kb))}>
							{t.detail.discard}
						</Button>
						<SubmitButton
							variant="default"
							pending={submit.isSubmitting}
							locked={submit.locked}
							pendingLabel={t.detail.saving}
						>
							{t.detail.save}
						</SubmitButton>
					</FormFoot>
				</form>
			</Panel>
			<Panel title={t.prompt.tryTitle} sub={t.prompt.trySub}>
				<div className="grid gap-3 p-4">
					<p className="m-0 text-foreground-2">{t.prompt.tryBody}</p>
					<Link to="/playground" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
						<MessageSquare aria-hidden />
						{t.prompt.tryButton}
					</Link>
				</div>
			</Panel>
			{guard}
		</SettingsGrid>
	);
}
