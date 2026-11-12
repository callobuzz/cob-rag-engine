import { LayoutTemplate } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from "@/components/ui/dialog";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { landingStrings as t } from "@/lib/strings/landing";
import { type PresetDetail, type PresetSummary, usePreset, usePresets } from "./api";
import { useDraft } from "./DraftProvider";
import { mergePreset, mergeTexts, PRESET_PARTS, type PresetPart } from "./draft";

/**
 * Six starting examples by kind of business. Choosing one asks which parts it may replace (all
 * ticked) and merges them into the draft in memory: nothing is saved until "Save draft".
 */
export function PresetDialog() {
	const draft = useDraft();
	const presets = usePresets();
	const load = usePreset();
	const [open, setOpen] = useState(false);
	const [chosen, setChosen] = useState<{ summary: PresetSummary; detail: PresetDetail } | null>(null);
	const [parts, setParts] = useState<PresetPart[]>([...PRESET_PARTS]);
	const [error, setError] = useState<string | null>(null);

	const close = (next: boolean) => {
		setOpen(next);
		if (!next) {
			setChosen(null);
			setParts([...PRESET_PARTS]);
			setError(null);
		}
	};

	const choose = async (summary: PresetSummary) => {
		setError(null);
		try {
			// one request per language the site offers; the texts are put side by side
			const langs = draft.languages.filter((l) => summary.languages.includes(l));
			const wanted = langs.length > 0 ? langs : [draft.defaultLanguage];
			const loaded = await Promise.all(wanted.map((lang) => load.mutateAsync({ key: summary.key, lang })));
			const detail = loaded.reduce((acc, next) => mergeTexts(acc, next) as PresetDetail);
			setChosen({ summary, detail });
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};

	const apply = () => {
		if (!chosen) return;
		if (parts.length === 0) return setError(t.preset.none);
		draft.replace(mergePreset(draft.content, chosen.detail, parts));
		toast.success(t.preset.applied);
		close(false);
	};

	return (
		<Dialog open={open} onOpenChange={close}>
			<DialogTrigger asChild>
				<Button type="button" variant="outline" data-testid="preset-open">
					<LayoutTemplate aria-hidden />
					{t.preset.open}
				</Button>
			</DialogTrigger>
			<DialogContent className="sm:max-w-xl">
				<DialogHeader>
					<DialogTitle>{t.preset.title}</DialogTitle>
					<DialogDescription>{t.preset.lead}</DialogDescription>
				</DialogHeader>
				{error ? (
					<p role="alert" className="m-0 text-[13px] text-destructive">
						{error}
					</p>
				) : null}
				{chosen ? (
					<fieldset className="grid gap-2" data-testid="preset-parts">
						<legend className="mb-1 text-[13px] font-semibold">
							{t.preset.whichParts} ({chosen.summary.name})
						</legend>
						{PRESET_PARTS.map((part) => (
							<label key={part} htmlFor={`preset-part-${part}`} className="flex items-center gap-2 text-[13.5px]">
								<Checkbox
									id={`preset-part-${part}`}
									checked={parts.includes(part)}
									onCheckedChange={(on) => setParts(on ? [...parts, part] : parts.filter((p) => p !== part))}
								/>
								{t.preset.parts[part]}
							</label>
						))}
					</fieldset>
				) : presets.isPending ? (
					<p className="m-0 text-[13px] text-muted-foreground">…</p>
				) : presets.isError ? (
					<p className="m-0 text-[13px] text-destructive">{t.preset.loadError}</p>
				) : (
					<ul className="m-0 grid list-none gap-2 p-0 min-[600px]:grid-cols-2">
						{presets.data.map((p) => (
							<li key={p.key} className="grid gap-2 rounded-sm border border-border p-3">
								<strong className="text-[14px]">{p.name}</strong>
								<Button
									type="button"
									size="sm"
									variant="outline"
									data-testid={`preset-use-${p.key}`}
									disabled={load.isPending}
									aria-label={`${t.preset.use}: ${p.name}`}
									onClick={() => void choose(p)}
								>
									{t.preset.use}
								</Button>
							</li>
						))}
					</ul>
				)}
				{chosen ? (
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => setChosen(null)}>
							{t.preset.back}
						</Button>
						<Button type="button" variant="brand" data-testid="preset-apply" onClick={apply}>
							{t.preset.apply}
						</Button>
					</DialogFooter>
				) : null}
			</DialogContent>
		</Dialog>
	);
}
