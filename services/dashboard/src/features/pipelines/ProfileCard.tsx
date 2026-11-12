import { Copy, Pencil, Star, Trash2, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fill } from "@/lib/strings/en";
import { pipelinesStrings } from "@/lib/strings/pipelines";
import type { Profile } from "./api";
import { shortModel, usedByText } from "./format";

const t = pipelinesStrings.card;

export type ProfileAction = "edit" | "clone" | "default" | "delete";

/** One profile: what it answers with, how it searches, its priority and who uses it. */
export function ProfileCard({
	profile: p,
	onAction,
}: {
	profile: Profile;
	onAction: (a: ProfileAction, p: Profile) => void;
}) {
	const c = p.config;
	const used = usedByText(p.plan_count, p.tenant_count);
	const model =
		c.answer_route === "local"
			? `${t.localModel}${c.answer_model ? ` (${shortModel(c.answer_model)})` : ""}`
			: c.answer_model
				? shortModel(c.answer_model)
				: `${t.engineModel} (${shortModel(p.resolved.answer_model)})`;
	const whyId = `why-${p.id}`;
	return (
		<li className="grid content-start gap-3 rounded-lg border border-border bg-card p-4" aria-label={p.name}>
			<div className="flex flex-wrap items-center gap-2">
				<h3 className="m-0 text-[15px] font-bold">{p.name}</h3>
				{p.is_default ? <Badge variant="info">{t.default}</Badge> : null}
				{p.builtin_key ? <Badge>{t.builtIn}</Badge> : null}
			</div>
			<p className="m-0 text-[12.5px] text-muted-foreground">{fill(t.shownAs, { label: p.label })}</p>
			{p.description ? <p className="m-0 text-[13px] text-foreground-2">{p.description}</p> : null}
			{p.available ? null : (
				<p className="m-0 flex items-start gap-1.5 text-[12.5px] font-medium text-warning">
					<TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
					{t.unavailable}
				</p>
			)}
			<dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
				<dt className="text-muted-foreground">{t.answersWith}</dt>
				<dd className="m-0 font-medium">{model}</dd>
				<dt className="text-muted-foreground">{t.search}</dt>
				<dd className="m-0">{t.reranker[c.reranker] ?? c.reranker}</dd>
				<dt className="text-muted-foreground">{t.priority}</dt>
				<dd className="m-0">{t.priorities[c.priority] ?? c.priority}</dd>
				<dt className="text-muted-foreground">{t.usedBy}</dt>
				<dd className="m-0">{used ?? t.usedByNothing}</dd>
			</dl>
			<div className="flex flex-wrap items-center gap-1.5">
				<Button size="sm" aria-label={`${t.edit} ${p.name}`} onClick={() => onAction("edit", p)}>
					<Pencil aria-hidden />
					{t.edit}
				</Button>
				<Button size="sm" variant="ghost" aria-label={`${t.clone} ${p.name}`} onClick={() => onAction("clone", p)}>
					<Copy aria-hidden />
					{t.clone}
				</Button>
				{p.is_default ? null : (
					<Button
						size="sm"
						variant="ghost"
						aria-label={`${t.makeDefault} ${p.name}`}
						onClick={() => onAction("default", p)}
					>
						<Star aria-hidden />
						{t.makeDefault}
					</Button>
				)}
				<Button
					size="sm"
					variant="ghost"
					disabled={p.is_default}
					aria-label={`${t.delete} ${p.name}`}
					aria-describedby={p.is_default ? whyId : undefined}
					onClick={() => onAction("delete", p)}
				>
					<Trash2 aria-hidden />
					{t.delete}
				</Button>
			</div>
			{p.is_default ? (
				<small id={whyId} className="text-[12px] text-muted-foreground">
					{t.deleteDefaultWhy}
				</small>
			) : null}
		</li>
	);
}
