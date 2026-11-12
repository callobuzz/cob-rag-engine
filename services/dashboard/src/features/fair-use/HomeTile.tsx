import { Link } from "@tanstack/react-router";
import { buttonVariants } from "@/components/ui/button";
import type { Home } from "@/features/home/api";
import { Panel } from "@/features/kbs/ui";
import { fairUseOwnerStrings as t } from "@/lib/strings/pipelines";
import { fairUseStateSentence } from "./sentences";

type Tile = NonNullable<Home["fair_use"]>;

/** The sentence for the Home heads-up (no amounts): near a limit, slowed down, or paused. */
export function tileSentence(tile: Tile, now?: Date): string | null {
	const worst = tile.worst_metric
		? { metric: tile.worst_metric, pct: tile.pct ?? 0, resets_at: tile.resets_at }
		: undefined;
	return fairUseStateSentence(tile.state, worst, now);
}

/** Home shows this one tile only when a limit is close or passed, so a calm page stays calm. */
export function FairUseHomeTile({ tile, owner }: { tile: Tile; owner: boolean }) {
	const text = tileSentence(tile);
	if (!text) return null;
	return (
		<Panel title={t.tile.title}>
			<div className="grid gap-3 p-4" data-testid="home-fair-use" data-state={tile.state}>
				<p className="m-0 text-[13.5px]">{text}</p>
				{owner ? (
					<div>
						<Link to="/plan" className={buttonVariants({ variant: "outline", size: "sm" })}>
							{t.tile.more}
						</Link>
					</div>
				) : null}
			</div>
		</Panel>
	);
}
