import { CircleCheck, CircleMinus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Panel } from "@/features/kbs/ui";
import type { EngineInfo } from "@/lib/api/accounts-types";
import { engineStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";

const t = engineStrings.features;

function OnOff({ on, text }: { on: boolean; text?: string }) {
	return (
		<Badge variant={on ? "ok" : "default"}>
			{on ? <CircleCheck aria-hidden /> : <CircleMinus aria-hidden />}
			{text ?? (on ? t.on : t.off)}
		</Badge>
	);
}

/** "local model, LLM · default local model, on by default". */
export function rerankerText(r: EngineInfo["features"]["reranker"] | undefined): string {
	if (!r) return t.none;
	const list = [r.local ? t.local : null, r.llm ? t.llm : null].filter(Boolean).join(", ");
	if (!list) return t.none;
	const def = r.default_provider === "llm" ? t.llm : t.local;
	return fill(t.rerankerAvail, { list, def, on: r.enabled_by_default ? t.onByDefault : t.offByDefault });
}

/** Features from `GET /info` (switched in .env), each as an icon + text badge. */
export function FeaturesCard({ features }: { features: EngineInfo["features"] }) {
	const tr = features.transcription;
	const ws = features.web_search;
	const rr = features.reranker;
	const rows: { label: string; value: React.ReactNode }[] = [
		{ label: t.portal, value: <OnOff on={!!features.content_portal} /> },
		{ label: t.streaming, value: <OnOff on={!!features.streaming} /> },
		{
			label: t.transcription,
			value: (
				<OnOff
					on={!!tr?.enabled}
					text={tr?.enabled && tr.provider ? fill(t.via, { provider: tr.provider }) : undefined}
				/>
			),
		},
		{
			label: t.reranker,
			value: (
				<span className="grid gap-1">
					<OnOff on={!!(rr?.local || rr?.llm)} />
					<span className="text-[12.5px] text-foreground-2">{rerankerText(rr)}</span>
				</span>
			),
		},
		{
			label: t.webSearch,
			value: (
				<OnOff
					on={!!ws?.enabled}
					text={ws?.enabled && ws.provider ? fill(t.via, { provider: ws.provider }) : undefined}
				/>
			),
		},
		{ label: t.accounts, value: <OnOff on={!!features.accounts} /> },
	];
	return (
		<Panel title={t.title} sub={t.sub}>
			<dl className="m-0 grid">
				{rows.map((r) => (
					<div
						key={r.label}
						className="grid items-center gap-1 border-b border-border px-4 py-2.5 text-[13px] last:border-b-0 min-[861px]:grid-cols-[170px_minmax(0,1fr)] min-[861px]:gap-3"
					>
						<dt className="text-foreground-2">{r.label}</dt>
						<dd className="m-0">{r.value}</dd>
					</div>
				))}
			</dl>
		</Panel>
	);
}
