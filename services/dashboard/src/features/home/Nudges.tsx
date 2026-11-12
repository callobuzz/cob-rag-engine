import { Link } from "@tanstack/react-router";
import { X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { fill } from "@/lib/strings/en";
import { homeStrings } from "@/lib/strings/home";
import type { Home } from "./api";

const t = homeStrings.nudges;
type Kind = NonNullable<Home["nudges"]>[number]["kind"];

const KEY = (kind: Kind) => `home-nudge-dismissed:${kind}`;

/** Dismissed on this browser only; a browser that blocks storage simply shows it again. */
function wasDismissed(kind: Kind): boolean {
	try {
		return window.localStorage.getItem(KEY(kind)) === "1";
	} catch {
		return false;
	}
}

function remember(kind: Kind): void {
	try {
		window.localStorage.setItem(KEY(kind), "1");
	} catch {
		// nothing to do: it is hidden for this visit
	}
}

function Action({ kind }: { kind: Kind }) {
	const text = t[kind].action;
	const link =
		kind === "add_brochures" ? (
			<Link to="/knowledge" search={{ tab: "articles" }}>
				{text}
			</Link>
		) : kind === "no_human_path" ? (
			<Link to="/assistant">{text}</Link>
		) : (
			<Link to="/value" search={{ edit: 1 }}>
				{text}
			</Link>
		);
	return <span className="font-semibold">{link}</span>;
}

/**
 * TV5: what is worth doing next, most important first (the engine orders them). Each card can be
 * dismissed on this browser. Owners only: a member cannot change any of it.
 */
export function Nudges({ nudges }: { nudges: NonNullable<Home["nudges"]> }) {
	const [gone, setGone] = useState<Kind[]>([]);
	const shown = nudges.filter((n) => !gone.includes(n.kind) && !wasDismissed(n.kind));
	if (shown.length === 0) return null;
	return (
		<Panel title={t.title}>
			<ul className="m-0 grid list-none gap-3 p-4" data-testid="home-nudges">
				{shown.map((n) => (
					<li
						key={n.kind}
						data-testid={`nudge-${n.kind}`}
						className="flex items-start justify-between gap-3 text-[13.5px]"
					>
						<div className="grid gap-1">
							<strong>{t[n.kind].title}</strong>
							<span className="text-foreground-2">{t[n.kind].body}</span>
							<Action kind={n.kind} />
						</div>
						<Button
							size="sm"
							variant="ghost"
							aria-label={fill(t.dismiss, { title: t[n.kind].title })}
							onClick={() => {
								remember(n.kind);
								setGone((g) => [...g, n.kind]);
							}}
						>
							<X aria-hidden />
						</Button>
					</li>
				))}
			</ul>
		</Panel>
	);
}
