import { useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { type SettingsPatch, settingsOf, useUpdateSettings, type WhatsApp } from "./api";

const TEMPLATE = /^[a-z0-9_]{1,512}$/;
const LANGUAGE = /^[a-z]{2,3}(_[A-Za-z]{2})?$/;

/** What is wrong with the settings the way the engine will see them (null = nothing). */
export function settingsProblem(s: {
	pause: number;
	followEnabled: boolean;
	template: string;
	language: string;
	after: number;
}): Partial<Record<"pause" | "template" | "language" | "after", string>> {
	const out: Partial<Record<"pause" | "template" | "language" | "after", string>> = {};
	if (!Number.isInteger(s.pause) || s.pause < 1 || s.pause > 72) out.pause = "1 to 72";
	if (!Number.isInteger(s.after) || s.after < 1 || s.after > 72) out.after = "1 to 72";
	if (s.followEnabled) {
		if (!TEMPLATE.test(s.template)) out.template = t.settings.followUp.incomplete;
		if (!LANGUAGE.test(s.language)) out.language = t.settings.followUp.incomplete;
	}
	return out;
}

/**
 * How it works (TV6): switch the number off or on, how long the assistant stays quiet after you
 * reply, and the opt-in follow-up (off by default, with a plain warning that Meta must approve the
 * template). Owners edit; a member sees the values, locked.
 */
export function SettingsCard({ kb, data }: { kb: string; data: WhatsApp }) {
	const id = useId();
	const update = useUpdateSettings(kb);
	const s = settingsOf(data);
	const [enabled, setEnabled] = useState(data.status !== "disabled");
	const [pause, setPause] = useState(String(s.human_pause_hours));
	const [followEnabled, setFollowEnabled] = useState(s.follow_up.enabled);
	const [template, setTemplate] = useState(s.follow_up.template_name ?? "");
	const [language, setLanguage] = useState(s.follow_up.language ?? "");
	const [after, setAfter] = useState(String(s.follow_up.after_hours));
	const [shown, setShown] = useState(false);
	const problems = settingsProblem({
		pause: Number(pause),
		followEnabled,
		template: template.trim(),
		language: language.trim(),
		after: Number(after),
	});
	const hasProblem = Object.keys(problems).length > 0;

	const save = (e: React.FormEvent) => {
		e.preventDefault();
		setShown(true);
		if (hasProblem) return;
		const body: SettingsPatch = {
			enabled,
			human_pause_hours: Number(pause),
			follow_up: {
				enabled: followEnabled,
				...(template.trim() ? { template_name: template.trim() } : {}),
				...(language.trim() ? { language: language.trim() } : {}),
				after_hours: Number(after),
			},
		};
		update.mutate(body, {
			onSuccess: () => toast.success(t.settings.saved),
			onError: (err) => toast.error(messageFor(toApiError(err)).title),
		});
	};

	const err = (k: keyof typeof problems) => (shown ? (problems[k] ?? null) : null);

	return (
		<Panel title={t.settings.title}>
			<form onSubmit={save} noValidate className="grid gap-4 p-4" data-testid="wa-settings">
				<div className="flex items-start justify-between gap-3">
					<div className="grid gap-0.5">
						<label htmlFor={`${id}-on`} className="text-[13px] font-semibold">
							{t.settings.enabled}
						</label>
						<small className="text-muted-foreground">{t.settings.enabledHelp}</small>
					</div>
					<OwnerGate action="whatsapp.manage">
						<Switch id={`${id}-on`} checked={enabled} onCheckedChange={setEnabled} />
					</OwnerGate>
				</div>
				<Field id={`${id}-pause`} label={t.settings.pause} hint={t.settings.pauseHint} error={err("pause")}>
					<div className="flex items-center gap-2">
						<OwnerGate action="whatsapp.manage">
							<Input
								id={`${id}-pause`}
								type="number"
								inputMode="numeric"
								min={1}
								max={72}
								className="w-24"
								value={pause}
								aria-invalid={err("pause") ? true : undefined}
								aria-describedby={describedBy(`${id}-pause`, { hint: true, error: !!err("pause") })}
								onChange={(e) => setPause(e.target.value)}
							/>
						</OwnerGate>
						<span>{t.settings.hours}</span>
					</div>
				</Field>

				<fieldset className="grid gap-3 rounded-md border border-border p-3">
					<legend className="px-1 text-[13px] font-semibold">{t.settings.followUp.title}</legend>
					<p className="m-0 text-[13px] text-foreground-2">{t.settings.followUp.help}</p>
					<FormAlert tone="warn">{t.settings.followUp.warning}</FormAlert>
					<label htmlFor={`${id}-follow`} className="flex items-center gap-2 text-[13px]">
						<OwnerGate action="whatsapp.manage">
							<Switch id={`${id}-follow`} checked={followEnabled} onCheckedChange={setFollowEnabled} />
						</OwnerGate>
						<span className="font-semibold">{t.settings.followUp.enabled}</span>
					</label>
					<div className="grid gap-3 sm:grid-cols-2">
						<Field
							id={`${id}-tpl`}
							label={t.settings.followUp.template}
							hint={t.settings.followUp.templateHint}
							error={err("template")}
						>
							<OwnerGate action="whatsapp.manage">
								<Input
									id={`${id}-tpl`}
									value={template}
									maxLength={512}
									aria-invalid={err("template") ? true : undefined}
									aria-describedby={describedBy(`${id}-tpl`, { hint: true, error: !!err("template") })}
									onChange={(e) => setTemplate(e.target.value)}
								/>
							</OwnerGate>
						</Field>
						<Field
							id={`${id}-lang`}
							label={t.settings.followUp.language}
							hint={t.settings.followUp.languageHint}
							error={err("language")}
						>
							<OwnerGate action="whatsapp.manage">
								<Input
									id={`${id}-lang`}
									value={language}
									maxLength={10}
									aria-invalid={err("language") ? true : undefined}
									aria-describedby={describedBy(`${id}-lang`, { hint: true, error: !!err("language") })}
									onChange={(e) => setLanguage(e.target.value)}
								/>
							</OwnerGate>
						</Field>
					</div>
					<Field id={`${id}-after`} label={t.settings.followUp.after} error={err("after")}>
						<div className="flex items-center gap-2">
							<OwnerGate action="whatsapp.manage">
								<Input
									id={`${id}-after`}
									type="number"
									inputMode="numeric"
									min={1}
									max={72}
									className="w-24"
									value={after}
									aria-invalid={err("after") ? true : undefined}
									onChange={(e) => setAfter(e.target.value)}
								/>
							</OwnerGate>
							<span>{t.settings.hours}</span>
						</div>
					</Field>
				</fieldset>
				<p className="m-0 text-[12.5px] text-muted-foreground">{t.settings.stopNote}</p>
				<div>
					<OwnerGate action="whatsapp.manage">
						<Button type="submit" variant="default" aria-disabled={update.isPending || undefined}>
							{update.isPending ? "…" : "Save"}
						</Button>
					</OwnerGate>
				</div>
			</form>
		</Panel>
	);
}
