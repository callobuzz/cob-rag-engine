import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import type { LandingLang } from "./api";
import { useDraft } from "./DraftProvider";
import { describePath, missingIn } from "./draft";

const OPTIONAL: LandingLang[] = ["hi", "hi-Latn"];

/** Which languages the website offers, and every text a switched-on language still lacks, each with a link to fix it. */
export function LanguagesTab() {
	const draft = useDraft();
	const on = (l: LandingLang) => draft.languages.includes(l);
	const toggle = (l: LandingLang, enabled: boolean) =>
		draft.update((d) => {
			const without = d.languages.filter((x) => x !== l);
			d.languages = enabled ? [...without, l] : without;
			// keep the order English, हिंदी, Hinglish
			const order: LandingLang[] = ["en", "hi", "hi-Latn"];
			d.languages = order.filter((x) => d.languages.includes(x));
		});

	return (
		<div className="grid gap-4" data-testid="languages-tab">
			<section aria-label={t.languages.title} className="grid gap-4 rounded-lg border border-border bg-card p-4">
				<div className="grid gap-0.5">
					<h2 className="m-0 text-[15px] font-bold">{t.languages.title}</h2>
					<p className="m-0 text-[13px] text-muted-foreground">{t.languages.about}</p>
				</div>
				<div className="flex items-center gap-3">
					<Switch checked disabled aria-label={fill(t.languages.switch, { lang: t.langs[draft.defaultLanguage] })} />
					<span className="text-[13.5px] font-semibold">
						{t.langs[draft.defaultLanguage]}{" "}
						<span className="font-normal text-muted-foreground">({t.languages.main})</span>
					</span>
				</div>
				{OPTIONAL.map((l) => (
					<div key={l} className="flex items-center gap-3">
						<Switch
							checked={on(l)}
							aria-label={fill(t.languages.switch, { lang: t.langs[l] })}
							onCheckedChange={(v) => toggle(l, v)}
						/>
						<span className="text-[13.5px] font-semibold">{t.langs[l]}</span>
					</div>
				))}
			</section>
			{draft.languages
				.filter((l) => l !== draft.defaultLanguage)
				.map((l) => {
					const gaps = missingIn(draft.content, l);
					return (
						<section
							key={l}
							aria-label={fill(t.languages.missingTitle, { lang: t.langs[l] })}
							className="grid gap-3 rounded-lg border border-border bg-card p-4"
							data-testid={`missing-${l}`}
						>
							<h2 className="m-0 text-[15px] font-bold">
								{gaps.length === 0
									? fill(t.languages.complete, { lang: t.langs[l] })
									: fill(t.languages.missingCount, { n: gaps.length, lang: t.langs[l] })}
							</h2>
							{gaps.length > 0 ? (
								<ul className="m-0 grid list-none gap-1 p-0">
									{gaps.map((path) => (
										<li key={path} className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
											<span>{describePath(path, draft.content)}</span>
											<Button type="button" size="sm" variant="ghost" onClick={() => draft.goTo(path, l)}>
												{t.languages.fix}
											</Button>
										</li>
									))}
								</ul>
							) : null}
						</section>
					);
				})}
		</div>
	);
}
