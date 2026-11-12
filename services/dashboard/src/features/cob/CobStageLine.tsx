import { cobStrings as t } from "@/lib/strings/cob";
import type { CobStage } from "./types";

export function stageText(stage: CobStage | null): string {
	switch (stage) {
		case null:
			return t.answering;
		case "understand":
		case "search":
			return t.stage.search;
		case "verify":
		case "check":
			return t.stage.verify;
		case "write":
		case "generate":
			return t.stage.write;
		case "tool":
			return t.stage.tool;
		case "propose":
			return t.stage.propose;
		default:
			return t.stage.fallback;
	}
}

/** The live line under a streaming answer ("Searching the docs…"). Announced by the panel's region, not here. */
export function CobStageLine({ stage }: { stage: CobStage | null }) {
	return (
		<p data-testid="cob-stage" className="m-0 flex items-center gap-2 text-[12.5px] text-muted-foreground">
			<span aria-hidden="true" className="size-1.5 animate-pulse rounded-full bg-brand motion-reduce:animate-none" />
			{stageText(stage)}
		</p>
	);
}
