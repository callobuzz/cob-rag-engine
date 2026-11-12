import { useQuery } from "@tanstack/react-query";
import { useWatch } from "react-hook-form";
import { Switch } from "@/components/ui/switch";
import { infoQuery } from "@/lib/api/queries";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { Panel } from "../../kbs/ui";
import { templatesQuery } from "../api";
import { SelectControl, SwitchControl, TextControl } from "../controls";
import { fillPersona, STANDARD_DISCLOSURE, saysAi } from "../defaults";
import { LocalizedTextField } from "../LocalizedTextField";
import { SectionShell } from "../SectionShell";
import { identitySchema, TEMPLATE_KEYS } from "../schemas";
import type { AssistantState } from "../types";
import { useDraftSection } from "../useDraftSection";
import { BusinessCardFields } from "./BusinessCardFields";

/** Identity and the business card (design 4.1): who the assistant is and what it may quote. */
export function IdentitySection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const section = useDraftSection(kb.id, state, "identity", identitySchema);
	const templates = useQuery(templatesQuery);
	// TV5 disclosure: the deployer can lock the switch and the wording
	const info = useQuery(infoQuery);
	const { productName } = useBranding();
	const required = info.data?.features.trust?.require_ai_disclosure !== false;
	const editable = info.data?.features.trust?.disclosure_wording_editable !== false;
	// /TV5 disclosure
	const { control } = section.form;
	const [name, business, disclose, disclosure, tz] = useWatch({
		control,
		name: ["assistant_name", "business_name", "ai_disclosure", "ai_disclosure_text", "card.timezone"],
	});
	const types = templates.data?.length
		? templates.data.map((x) => ({ value: x.key as string, label: x.name }))
		: TEMPLATE_KEYS.map((k) => ({ value: k as string, label: t.templateNames[k] }));
	const vars = { assistant_name: name, business_name: business };
	// TV5 disclosure: wording with no whole-word "AI" is replaced by the standard line, so say so
	const ownText = disclosure?.en ?? "";
	const replaced = ownText.trim() !== "" && !saysAi(ownText);
	const sample = replaced ? fillPersona(STANDARD_DISCLOSURE, vars) : fillPersona(ownText, vars);
	// /TV5 disclosure
	return (
		<SectionShell section={section}>
			<Panel title={t.identity.title} sub={t.identity.sub}>
				<div className="grid gap-4 p-4">
					<TextControl name="assistant_name" label={t.identity.name} hint={t.identity.nameHint} maxLength={40} />
					<TextControl
						name="avatar_url"
						label={t.identity.avatar}
						hint={t.identity.avatarHint}
						type="url"
						inputMode="url"
					/>
					<TextControl name="business_name" label={t.identity.business} maxLength={120} />
					<SelectControl name="business_type" label={t.identity.type} hint={t.identity.typeHint} options={types} />
					<TextControl
						name="business_profile"
						label={t.identity.profile}
						hint={t.identity.profileHint}
						rows={3}
						maxLength={500}
						counter
					/>
					<TextControl name="audience" label={t.identity.audience} maxLength={200} />
					{/* TV5 disclosure */}
					{required ? (
						<div className="grid gap-1">
							<div className="flex items-center gap-2.5">
								<Switch id="ai-disclosure-locked" checked disabled aria-describedby="ai-disclosure-locked-hint" />
								<label htmlFor="ai-disclosure-locked" className="text-[13.5px] font-semibold">
									{t.identity.disclose}
								</label>
							</div>
							<small id="ai-disclosure-locked-hint" className="pl-[42px] text-[12.5px] text-muted-foreground">
								{fill(t.identity.discloseLocked, { product: productName })}
							</small>
						</div>
					) : (
						<SwitchControl name="ai_disclosure" label={t.identity.disclose} />
					)}
					{(required || disclose) && !editable ? (
						<p className="text-[13px] text-muted-foreground">
							{fill(t.identity.discloseWordingLocked, { product: productName })}{" "}
							<strong>{fillPersona(STANDARD_DISCLOSURE, vars)}</strong>
						</p>
					) : null}
					{/* /TV5 disclosure */}
					{(required || disclose) && editable ? (
						<LocalizedTextField
							name="ai_disclosure_text"
							label={t.identity.discloseText}
							max={200}
							rows={2}
							languages={section.config.language.supported}
							hint={
								<>
									{t.identity.discloseHint} {t.identity.discloseWordingRule}
									{replaced ? ` ${t.identity.discloseNotAi}` : ""}
									{sample ? ` ${fill(t.identity.discloseSample, { text: sample })}` : ""}
								</>
							}
						/>
					) : null}
				</div>
			</Panel>
			<Panel title={t.identity.cardTitle} sub={t.identity.cardSub}>
				<BusinessCardFields timezone={tz} />
			</Panel>
		</SectionShell>
	);
}
