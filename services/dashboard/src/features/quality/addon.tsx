import { useQuery } from "@tanstack/react-query";
import { Panel } from "@/features/kbs/ui";
import { infoQuery } from "@/lib/api/queries";
import { qualityEvalsStrings } from "@/lib/strings/quality-evals";

const t = qualityEvalsStrings.addon;

/**
 * Is the answer-quality evaluation add-on (RAGAS + LangChain) installed on the engine?
 * `GET /info` `features.eval_ragas`. True while loading and for an engine too old to say, so
 * nothing is disabled on a guess.
 */
export function useEvalAddon(): boolean {
	const info = useQuery(infoQuery);
	return info.data?.features?.eval_ragas !== false;
}

/** The card shown when the add-on is missing: what it unlocks, what still works, how to turn it on. */
export function AddonMissingCard() {
	return (
		<Panel title={t.title} sub={t.sub}>
			<div data-testid="eval-addon-missing" className="grid gap-3 px-4 py-3 text-[13px]">
				<p className="m-0">{t.needs}</p>
				<p className="m-0 text-muted-foreground">{t.works}</p>
				<div className="grid gap-1">
					<b>{t.howTitle}</b>
					<ul className="m-0 grid list-disc gap-1 pl-5">
						<li>
							{t.pip} <code>{t.pipCmd}</code>
						</li>
						<li>
							{t.docker} <code>{t.dockerCmd}</code>
						</li>
					</ul>
				</div>
			</div>
		</Panel>
	);
}
