import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { legalStrings as t } from "@/lib/strings/legal";
import { type LegalSettings, useSaveLegalSettings } from "./api";

const orNull = (text: string): string | null => (text.trim() === "" ? null : text.trim());

/** The grievance officer, the city the Terms name, the website, and the footer switch (P3). */
export function LegalSettingsCard({ settings }: { settings: LegalSettings }) {
	const id = useId();
	const save = useSaveLegalSettings();
	const [name, setName] = useState(settings.grievance_name ?? "");
	const [email, setEmail] = useState(settings.grievance_email ?? "");
	const [address, setAddress] = useState(settings.grievance_address ?? "");
	const [city, setCity] = useState(settings.jurisdiction_city ?? "");
	const [website, setWebsite] = useState(settings.website ?? "");
	const [footer, setFooter] = useState(settings.show_in_footer);
	const [error, setError] = useState<string | null>(null);

	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		try {
			await save.mutateAsync({
				grievance_name: orNull(name),
				grievance_email: orNull(email),
				grievance_address: orNull(address),
				jurisdiction_city: orNull(city),
				website: orNull(website),
				show_in_footer: footer,
			});
			toast.success(t.settings.saved);
		} catch (err) {
			const apiErr = toApiError(err);
			setError(apiErr.status === 422 ? t.settings.invalid : messageFor(apiErr).title);
		}
	};

	return (
		<form onSubmit={(e) => void submit(e)} noValidate data-testid="legal-settings">
			<Panel title={t.settings.title} sub={t.settings.sub}>
				<div className="grid gap-4 p-4">
					{error ? <FormAlert>{error}</FormAlert> : null}
					<div className="grid gap-4 min-[600px]:grid-cols-2">
						<Field id={`${id}-name`} label={t.settings.name}>
							<Input
								id={`${id}-name`}
								autoComplete="off"
								value={name}
								maxLength={120}
								onChange={(e) => setName(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-email`} label={t.settings.email}>
							<Input
								id={`${id}-email`}
								type="email"
								autoComplete="off"
								value={email}
								onChange={(e) => setEmail(e.target.value)}
							/>
						</Field>
					</div>
					<Field id={`${id}-address`} label={t.settings.address}>
						<Input id={`${id}-address`} value={address} maxLength={300} onChange={(e) => setAddress(e.target.value)} />
					</Field>
					<div className="grid gap-4 min-[600px]:grid-cols-2">
						<Field id={`${id}-city`} label={t.settings.city} hint={t.settings.cityHint}>
							<Input
								id={`${id}-city`}
								value={city}
								maxLength={80}
								aria-describedby={`${id}-city-hint`}
								onChange={(e) => setCity(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-site`} label={t.settings.website} hint={t.settings.websiteHint}>
							<Input
								id={`${id}-site`}
								inputMode="url"
								value={website}
								aria-describedby={`${id}-site-hint`}
								onChange={(e) => setWebsite(e.target.value)}
							/>
						</Field>
					</div>
					<div className="flex items-start gap-3">
						<Switch id={`${id}-footer`} checked={footer} onCheckedChange={setFooter} />
						<div className="grid gap-0.5">
							<label htmlFor={`${id}-footer`} className="text-[13.5px] font-semibold">
								{t.settings.footer}
							</label>
							<small className="text-[12.5px] text-muted-foreground">{t.settings.footerHint}</small>
						</div>
					</div>
					<p className="m-0 text-[12.5px] text-muted-foreground">{t.settings.companyNote}</p>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={save.isPending} pendingLabel={t.settings.saving}>
						{t.settings.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
