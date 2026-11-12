import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useId } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { FormAlert } from "@/components/auth/FormAlert";
import { Checkbox } from "@/components/ui/checkbox";
import { infoQuery } from "@/lib/api/queries";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { type CorpusDetail, embeddingCatalogQuery } from "../../kbs/api";
import { catalogEntry, effectiveEmbedding } from "../../kbs/embedding";
import { Panel } from "../../kbs/ui";
import { ChoiceControl, SelectControl, SwitchControl, useFieldError } from "../controls";
import { SectionShell } from "../SectionShell";
import { type FullConfig, LANG_KEYS, type LangKey, languageSchema } from "../schemas";
import type { AssistantState } from "../types";
import { useDraftSection } from "../useDraftSection";

type LanguageForm = FullConfig["language"];

const opts = <T extends string>(map: Record<T, string>) =>
	(Object.keys(map) as T[]).map((value) => ({ value, label: map[value] }));

function SupportedLanguages() {
	const id = useId();
	const { control, setValue } = useFormContext<LanguageForm>();
	const supported = useWatch({ control, name: "supported" }) ?? [];
	const error = useFieldError("supported");
	const toggle = (lang: LangKey, on: boolean) => {
		const next = on ? [...supported, lang] : supported.filter((l) => l !== lang);
		setValue(
			"supported",
			LANG_KEYS.filter((l) => next.includes(l)),
			{ shouldDirty: true, shouldValidate: true },
		);
	};
	return (
		<fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
			<legend className="mb-1 p-0 text-[13px] font-semibold">{t.language.supported}</legend>
			<small className="text-[12.5px] text-muted-foreground">{t.language.supportedHint}</small>
			<div className="grid gap-1.5 min-[620px]:grid-cols-2">
				{LANG_KEYS.map((l) => (
					<div key={l} className="flex items-center gap-2">
						<Checkbox
							id={`${id}-${l}`}
							checked={supported.includes(l)}
							onCheckedChange={(v) => toggle(l, v === true)}
						/>
						<label htmlFor={`${id}-${l}`} className="text-[13.5px]">
							{t.langLong[l]}
						</label>
					</div>
				))}
			</div>
			{error ? <small className="text-[12.5px] font-medium text-destructive">{error}</small> : null}
		</fieldset>
	);
}

/** Say so when the search model is not documented as handling Hindi (no model id is named). */
function MultilingualWarning({ kb }: { kb: CorpusDetail }) {
	const { control } = useFormContext<LanguageForm>();
	const supported = useWatch({ control, name: "supported" }) ?? [];
	const catalog = useQuery(embeddingCatalogQuery);
	const info = useQuery(infoQuery);
	if (!supported.includes("hi") && !supported.includes("hi-Latn")) return null;
	if (!catalog.data || !info.data) return null;
	const model = effectiveEmbedding(kb, info.data);
	const entry = catalogEntry(catalog.data, model);
	const text = entry
		? entry.multilingual === false
			? t.language.englishOnlyWarning
			: null
		: model
			? t.language.unknownWarning
			: null;
	if (!text) return null;
	return (
		<FormAlert tone="warn">
			<span>{text} </span>
			<Link
				to="."
				search={(prev: Record<string, unknown>) => ({ ...prev, tab: "embedding", section: undefined })}
				className="font-semibold underline"
			>
				{t.language.embeddingLink}
			</Link>
		</FormAlert>
	);
}

/** Languages (design 4.3): what the assistant understands and how it replies. */
export function LanguageSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const section = useDraftSection(kb.id, state, "language", languageSchema);
	const supported = useWatch({ control: section.form.control, name: "supported" }) ?? [];
	const langOptions = supported.map((l) => ({ value: l as string, label: t.langLong[l] }));
	const docsOptions = LANG_KEYS.map((l) => ({ value: l as string, label: t.langLong[l] }));
	return (
		<SectionShell section={section} notice={<MultilingualWarning kb={kb} />}>
			<Panel title={t.language.title} sub={t.language.sub}>
				<div className="grid gap-4 p-4">
					<SupportedLanguages />
					<SelectControl name="primary" label={t.language.primary} options={langOptions} />
					<SwitchControl name="auto_detect" label={t.language.autoDetect} />
					<ChoiceControl<LanguageForm, keyof typeof t.language.replyPolicies>
						name="reply_policy"
						label={t.language.replyPolicy}
						options={opts(t.language.replyPolicies)}
					/>
					<ChoiceControl<LanguageForm, keyof typeof t.language.scriptPolicies>
						name="script_policy"
						label={t.language.scriptPolicy}
						options={opts(t.language.scriptPolicies)}
					/>
					<SelectControl name="documents_language" label={t.language.documents} options={docsOptions} />
					<SwitchControl name="translate_queries" label={t.language.translate} hint={t.language.translateHint} />
				</div>
			</Panel>
		</SectionShell>
	);
}
