import { formatMoney, useCurrency } from "@/lib/format/currency";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { cn } from "@/lib/utils";
import type { QueryResponse, RetrieveResponse } from "./api";
import { costUsd, type ModelEntry, tokensFrom } from "./cost";
import { formatMs } from "./stages";

export interface OrderEntry {
	chunkId: string;
	label: string;
	/** Position in A (0-based), or null when A did not use this passage. */
	from: number | null;
	to: number;
	move: "same" | "up" | "down" | "new";
}

type Cited = Pick<
	NonNullable<QueryResponse["citations"]>[number],
	"chunk_id" | "file_name" | "section" | "external_id"
>;

const label = (c: Cited) => c.section || c.file_name || c.external_id || c.chunk_id.slice(0, 8);

/** B's passages against A's order: ↑ moved up, ↓ moved down, "new" when A didn't use it. */
export function passageMoves(a: Cited[], b: Cited[]): OrderEntry[] {
	const posA = new Map(a.map((c, i) => [c.chunk_id, i]));
	return b.map((c, i) => {
		const from = posA.get(c.chunk_id);
		return {
			chunkId: c.chunk_id,
			label: label(c),
			from: from ?? null,
			to: i,
			move: from === undefined ? "new" : from > i ? "up" : from < i ? "down" : "same",
		};
	});
}

/** Mockup `.why`: passage order, search time, confidence (delta), tokens and cost, model. */
export function WhyTheyDiffer({
	a,
	b,
	ra,
	rb,
	catalog,
}: {
	a: QueryResponse;
	b: QueryResponse;
	ra: RetrieveResponse | null;
	rb: RetrieveResponse | null;
	catalog: readonly ModelEntry[] | undefined;
}) {
	const currency = useCurrency();
	const ca = a.citations ?? [];
	const cb = b.citations ?? [];
	const moves = passageMoves(ca, cb);
	const ta = tokensFrom(a.token_usage);
	const tb = tokensFrom(b.token_usage);
	const costA = costUsd(catalog, a.model_used, ta);
	const costB = costUsd(catalog, b.model_used, tb);
	const dConf = b.confidence - a.confidence;
	const dSearch = ra && rb ? rb.latency_ms - ra.latency_ms : null;
	const money = (v: number | null) => (v == null ? "—" : `≈ ${formatMoney(v, currency)}`);
	const signed = (v: number, digits = 2) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;

	return (
		<section
			aria-labelledby="why-differ"
			className="overflow-hidden rounded-md border border-border min-[861px]:col-span-2"
		>
			<h3 id="why-differ" className="m-0 border-b border-border bg-muted px-3 py-2.5 text-[13px] font-bold">
				{t.compare.why}
			</h3>
			<dl className="m-0">
				<Row name={t.compare.order}>
					<span>{ca.map((c, i) => `[${i + 1}] ${label(c)}`).join(" · ") || "—"}</span>
					<span>
						{moves.length
							? moves.map((m, i) => (
									<span key={m.chunkId}>
										{i > 0 ? " · " : ""}
										<span
											data-move={m.move}
											className={cn(
												m.move === "up" && "font-semibold text-success",
												m.move === "down" && "font-semibold text-warning",
												m.move === "new" && "font-semibold text-primary",
											)}
										>
											[{i + 1}] {m.label}
											{m.move === "up" ? " ↑" : m.move === "down" ? " ↓" : ""}
											{m.move === "new" ? ` (${t.compare.newPassage})` : ""}
											{m.move === "up" || m.move === "down" ? (
												<span className="sr-only">
													{" "}
													{fill(t.compare.moved, {
														dir: m.move === "up" ? t.compare.up : t.compare.down,
														from: (m.from ?? 0) + 1,
													})}
												</span>
											) : null}
										</span>
									</span>
								))
							: "—"}
					</span>
				</Row>
				<Row name={t.compare.searchTime}>
					<span>{ra ? formatMs(ra.latency_ms) : "—"}</span>
					<span>
						{rb ? formatMs(rb.latency_ms) : "—"}
						{dSearch != null && dSearch !== 0 ? ` · ${dSearch > 0 ? "+" : "−"}${formatMs(Math.abs(dSearch))}` : ""}
					</span>
				</Row>
				<Row name={t.compare.confidence}>
					<span>{a.confidence.toFixed(2)}</span>
					<span className={cn(dConf > 0 && "text-success", dConf < 0 && "text-warning")}>
						{b.confidence.toFixed(2)}
						{dConf !== 0 ? ` · ${signed(dConf)}` : ` · ${t.compare.same}`}
					</span>
				</Row>
				<Row name={t.compare.tokensCost}>
					<span>
						{formatNumber(ta?.total ?? null)} · {money(costA)}
					</span>
					<span>
						{formatNumber(tb?.total ?? null)} · {money(costB)}
					</span>
				</Row>
				<Row name={t.compare.model}>
					<span className="font-mono">{a.model_used}</span>
					<span className="font-mono">{a.model_used === b.model_used ? t.compare.sameModel : b.model_used}</span>
				</Row>
			</dl>
		</section>
	);
}

function Row({ name, children }: { name: string; children: [React.ReactNode, React.ReactNode] }) {
	return (
		<div className="grid grid-cols-1 gap-1 border-b border-border px-3 py-2.5 text-[13px] last:border-b-0 min-[521px]:grid-cols-[150px_minmax(0,1fr)_minmax(0,1fr)] min-[521px]:gap-3">
			<dt className="text-muted-foreground">{name}</dt>
			<dd className="m-0 min-w-0 break-words">
				<span className="sr-only">{t.compare.a}: </span>
				{children[0]}
			</dd>
			<dd className="m-0 min-w-0 break-words">
				<span className="sr-only">{t.compare.b}: </span>
				{children[1]}
			</dd>
		</div>
	);
}
