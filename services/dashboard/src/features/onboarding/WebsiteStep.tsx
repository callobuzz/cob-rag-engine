import { type FormEvent, useId, useState } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { HoursEditor, type HoursRow, hoursProblem } from "@/features/assistant-setup/HoursEditor";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { fill } from "@/lib/strings/en";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { AvatarUpload } from "./AvatarUpload";
import { type BusinessType, type Onboarding, type StartOnboarding, uploadAvatar, useStartOnboarding } from "./api";

const t = onboardingStrings.one;
const TYPES = Object.keys(t.type.options) as BusinessType[];

/** What the engine said about a refused picture, in the owner's words. */
export function avatarProblem(e: unknown): string {
	const err = toApiError(e);
	if (err.code === "avatar_too_large") return onboardingStrings.errors.avatarTooLarge;
	if (err.code === "avatar_invalid") return onboardingStrings.errors.avatarInvalid;
	return messageFor(err).title;
}

/** A website address is something with a dot and no spaces; the engine does the real check. */
export const looksLikeWebsite = (v: string) => /^\S+\.\S+$/.test(v.trim());

/**
 * Step 1: "What's your website?" Type the address, name the assistant, choose its picture, say what
 * kind of business it is, and add a WhatsApp number and opening hours. Continue sets up the
 * knowledge base, the assistant, the widget key and website learning in one go.
 */
export function WebsiteStep({ state, onDone }: { state: Onboarding; onDone: () => void }) {
	const principal = usePrincipal();
	const business = principal?.tenant?.name ?? "";
	const start = useStartOnboarding();
	const ids = { site: useId(), name: useId(), type: useId(), wa: useId() };
	const [website, setWebsite] = useState(state.website?.replace(/^https?:\/\//, "") ?? "");
	const [name, setName] = useState("");
	const [type, setType] = useState<BusinessType>("generic_support");
	const [whatsapp, setWhatsapp] = useState("");
	const [hours, setHours] = useState<HoursRow[]>([]);
	const [file, setFile] = useState<File | null>(null);
	const [siteError, setSiteError] = useState<string | null>(null);
	const [pictureError, setPictureError] = useState<string | null>(null);
	const [failure, setFailure] = useState<unknown>(null);
	const hoursError = hoursProblem(hours);

	const submit = async (e?: FormEvent) => {
		e?.preventDefault();
		setFailure(null);
		setPictureError(null);
		if (!looksLikeWebsite(website)) {
			setSiteError(t.website.required);
			return;
		}
		setSiteError(null);
		if (hoursError) return;
		const body: StartOnboarding = {
			website: website.trim(),
			business_type: type,
			...(name.trim() ? { assistant_name: name.trim() } : {}),
			...(whatsapp.trim() ? { whatsapp: whatsapp.trim() } : {}),
			...(hours.length ? { hours: hours.map((h) => ({ day: h.day, open: h.open, close: h.close })) } : {}),
		};
		try {
			const out = await start.mutateAsync(body);
			if (file && out.kb_id) {
				// The knowledge base exists now, so the picture can go up and be added to the assistant.
				try {
					const avatarUrl = await uploadAvatar(out.kb_id, file);
					await start.mutateAsync({ ...body, avatar_url: avatarUrl });
				} catch (pictureFailure) {
					setPictureError(`${avatarProblem(pictureFailure)} ${t.pictureRetry}`);
					return;
				}
			}
			onDone();
		} catch (error) {
			if (isApiError(error) && error.code === "website_url_invalid") setSiteError(messageFor(error).title);
			else setFailure(error);
		}
	};

	const problem = failure ? messageFor(toApiError(failure)) : null;
	return (
		<form onSubmit={(e) => void submit(e)} className="grid gap-5" noValidate>
			<div>
				<h2 className="text-lg font-bold">{t.title}</h2>
				<p className="m-0 text-[13.5px] text-foreground-2">{t.lead}</p>
			</div>
			<div className="grid gap-1.5">
				<Label htmlFor={ids.site} className="font-semibold">
					{t.website.label}
				</Label>
				<Input
					id={ids.site}
					value={website}
					onChange={(e) => {
						setWebsite(e.target.value);
						if (siteError) setSiteError(null);
					}}
					placeholder={t.website.placeholder}
					inputMode="url"
					autoComplete="url"
					autoCapitalize="none"
					spellCheck={false}
					aria-invalid={siteError ? true : undefined}
					aria-describedby={`${ids.site}-hint`}
				/>
				<small id={`${ids.site}-hint`} className="text-[12.5px] text-muted-foreground">
					{t.website.hint}
				</small>
				{siteError ? (
					<small className="text-[12.5px] font-medium text-destructive" role="alert">
						{siteError}
					</small>
				) : null}
			</div>
			<div className="grid gap-1.5">
				<Label htmlFor={ids.name} className="font-semibold">
					{t.name.label}
				</Label>
				<Input
					id={ids.name}
					value={name}
					onChange={(e) => setName(e.target.value)}
					maxLength={40}
					placeholder={business ? fill(t.name.defaultName, { business }).slice(0, 40) : t.name.fallback}
					aria-describedby={`${ids.name}-hint`}
				/>
				<small id={`${ids.name}-hint`} className="text-[12.5px] text-muted-foreground">
					{t.name.hint}
				</small>
			</div>
			<div className="grid gap-1.5">
				<span className="text-sm font-semibold">{t.avatar.label}</span>
				<AvatarUpload
					current={null}
					file={file}
					onPick={(f) => {
						setFile(f);
						setPictureError(null);
					}}
					onRemove={() => setFile(null)}
					busy={start.isPending}
					error={pictureError}
				/>
			</div>
			<div className="grid gap-1.5">
				<Label htmlFor={ids.type} className="font-semibold">
					{t.type.label}
				</Label>
				<NativeSelect
					id={ids.type}
					value={type}
					onChange={(e) => setType(e.target.value as BusinessType)}
					aria-describedby={`${ids.type}-hint`}
				>
					{TYPES.map((k) => (
						<option key={k} value={k}>
							{t.type.options[k]}
						</option>
					))}
				</NativeSelect>
				<small id={`${ids.type}-hint`} className="text-[12.5px] text-muted-foreground">
					{t.type.hint}
				</small>
			</div>
			<div className="grid gap-1.5">
				<Label htmlFor={ids.wa} className="font-semibold">
					{t.whatsapp.label}
				</Label>
				<Input
					id={ids.wa}
					value={whatsapp}
					onChange={(e) => setWhatsapp(e.target.value)}
					placeholder={t.whatsapp.placeholder}
					inputMode="tel"
					autoComplete="tel"
					maxLength={20}
					aria-describedby={`${ids.wa}-hint`}
				/>
				<small id={`${ids.wa}-hint`} className="text-[12.5px] text-muted-foreground">
					{t.whatsapp.hint}
				</small>
			</div>
			<div className="grid gap-1.5">
				<span className="text-sm font-semibold">{t.hours.label}</span>
				<small className="text-[12.5px] text-muted-foreground">{t.hours.hint}</small>
				<HoursEditor value={hours} onChange={setHours} error={hoursError} idPrefix="onboarding-hours" />
			</div>
			{problem ? (
				<div role="alert" className="grid gap-1 rounded-md bg-destructive-soft p-3 text-[13px] text-destructive">
					<b className="font-semibold">{problem.title}</b>
					{problem.body ? <span>{problem.body}</span> : null}
					<div>
						<Button type="button" size="sm" variant="outline" onClick={() => void submit()}>
							{t.tryAgain}
						</Button>
					</div>
				</div>
			) : null}
			<div className="flex justify-end">
				<Button type="submit" disabled={start.isPending}>
					{start.isPending ? t.working : t.continue}
				</Button>
			</div>
		</form>
	);
}
