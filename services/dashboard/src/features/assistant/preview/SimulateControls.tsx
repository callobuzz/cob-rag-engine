import { useId } from "react";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Segmented } from "@/components/patterns/Segmented";
import { Input } from "@/components/ui/input";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { tabLangs } from "../defaults";
import type { LangKey } from "../schemas";
import type { Simulate } from "./stream";

type Office = "now" | "open" | "closed";

/** Pretend a page address, an open or closed office, or a language, for the next messages. */
export function SimulateControls({
	value,
	onChange,
	languages,
}: {
	value: Simulate;
	onChange: (next: Simulate) => void;
	languages: readonly LangKey[];
}) {
	const ids = { page: useId(), lang: useId() };
	const office: Office = value.office_hours ?? "now";
	return (
		<div className="grid gap-3 p-4 min-[700px]:grid-cols-3">
			<Field id={ids.page} label={t.preview.page} hint={t.preview.pageHint}>
				<Input
					id={ids.page}
					type="url"
					inputMode="url"
					aria-describedby={describedBy(ids.page, { hint: true })}
					value={value.page_url ?? ""}
					onChange={(e) => onChange({ ...value, page_url: e.target.value || null })}
				/>
			</Field>
			<div className="grid content-start gap-1.5">
				<span className="text-[13px] font-semibold">{t.preview.office}</span>
				<div>
					<Segmented<Office>
						label={t.preview.office}
						value={office}
						options={[
							{ value: "now", label: t.preview.offices.now },
							{ value: "open", label: t.preview.offices.open },
							{ value: "closed", label: t.preview.offices.closed },
						]}
						onChange={(v) => onChange({ ...value, office_hours: v === "now" ? null : v })}
					/>
				</div>
			</div>
			<Field id={ids.lang} label={t.preview.language}>
				<NativeSelect
					id={ids.lang}
					value={value.language ?? ""}
					onChange={(e) => onChange({ ...value, language: (e.target.value || null) as LangKey | null })}
				>
					<option value="">{t.preview.detect}</option>
					{tabLangs(languages).map((l) => (
						<option key={l} value={l}>
							{t.langs[l]}
						</option>
					))}
				</NativeSelect>
			</Field>
		</div>
	);
}
