import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Segmented } from "@/components/patterns/Segmented";
import { Button } from "@/components/ui/button";
import { homeQuery } from "@/features/home/api";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { type Onboarding, onboardingQuery, suggestionsQuery } from "./api";
import { TryChat } from "./TryChat";

const t = onboardingStrings.three;

/** The progress line: "Learning your website: 23 pages read", counting up while the engine reads. */
export function learningLine(learning: Onboarding["learning"]): string {
	if (!learning) return t.learningWait;
	if (learning.first_pass_done) return fill(t.learningDone, { pages: String(learning.pages_learned) });
	if (learning.pages_learned === 1) return t.learningOne;
	return learning.pages_learned > 0 ? fill(t.learning, { pages: String(learning.pages_learned) }) : t.learningWait;
}

/**
 * Step 3: "Try your assistant". Chat with it, with questions suggested from the owner's own pages,
 * while the page count climbs. "Go to my dashboard" ends the steps.
 */
export function TryStep({
	state: initial,
	onFinish,
	onBack,
	finishing,
}: {
	state: Onboarding;
	onFinish: () => void;
	onBack: () => void;
	finishing?: boolean;
}) {
	const kb = initial.kb_id ?? "";
	const [lang, setLang] = useState<"en" | "hi-Latn">("en");
	// Looks again every few seconds while the first pass runs, so the count goes up by itself.
	const live = useQuery({
		...onboardingQuery(!initial.learning?.first_pass_done),
		initialData: initial,
		refetchInterval: (q) => (q.state.data?.learning?.first_pass_done ? false : 5_000),
	});
	const state = live.data ?? initial;
	const home = useQuery(homeQuery(kb));
	const suggestions = useQuery({ ...suggestionsQuery(kb, lang), enabled: !!kb });
	const learning = !!state.learning && !state.learning.first_pass_done;
	return (
		<div className="grid gap-5">
			<div>
				<h2 className="text-lg font-bold">{t.title}</h2>
				<p className="m-0 text-[13.5px] text-foreground-2">{t.lead}</p>
			</div>
			<p data-testid="learning-progress" role="status" className="m-0 text-[13.5px] font-semibold">
				{learningLine(state.learning)}
			</p>
			<div className="flex flex-wrap items-center gap-2">
				<span className="text-[12.5px] font-semibold">{t.suggestions}</span>
				<Segmented
					label={t.languageLabel}
					value={lang}
					options={[
						{ value: "en", label: t.english },
						{ value: "hi-Latn", label: t.hinglish },
					]}
					onChange={setLang}
				/>
			</div>
			{suggestions.isError ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">{messageFor(toApiError(suggestions.error)).title}</p>
			) : null}
			<TryChat
				kb={kb}
				name={home.data?.assistant.name ?? null}
				avatarUrl={home.data?.assistant.avatar_url ?? null}
				suggestions={suggestions.data?.items.map((i) => i.text)}
				learning={learning}
			/>
			<div className="flex flex-wrap justify-between gap-2">
				<Button variant="ghost" onClick={onBack}>
					{onboardingStrings.back}
				</Button>
				<Button onClick={onFinish} disabled={finishing}>
					{t.finish}
				</Button>
			</div>
		</div>
	);
}
