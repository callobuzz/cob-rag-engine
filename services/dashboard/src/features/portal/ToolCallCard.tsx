import { Wand2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { usePrincipal } from "@/lib/auth/store";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { actorFields, type ExecuteResult, portal, type ToolCall, useWorkspaceMutation } from "./api";
import { portalMessage } from "./messages";

const ch = t.chat;

/** The arguments in words, with chunk ids shortened to their chunk numbers when known. */
function argumentRows(call: ToolCall, numberOf: (id: string) => number | null): [string, string][] {
	return Object.entries(call.arguments ?? {}).map(([k, v]) => {
		const show = (x: unknown): string => {
			if (typeof x === "string") {
				const n = numberOf(x);
				return n ? fill(t.chunks.chunk, { n }) : x;
			}
			if (Array.isArray(x)) return x.map(show).join(", ");
			return JSON.stringify(x);
		};
		return [k.replace(/_/g, " "), show(v)];
	});
}

/** A change the AI proposed, with "Apply" (`POST …/chat/execute`); needs the edit lock. */
export function ToolCallCard({
	workspaceId,
	call,
	canApply,
	numberOf,
}: {
	workspaceId: string;
	call: ToolCall;
	canApply: boolean;
	numberOf: (chunkId: string) => number | null;
}) {
	const principal = usePrincipal();
	const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
	const run = useWorkspaceMutation(workspaceId, () => portal.execute(workspaceId, call, actorFields(principal)));
	const apply = () =>
		run.mutate(undefined, {
			onSuccess: (r: ExecuteResult) =>
				setResult({ ok: r.success, text: fill(r.success ? ch.applied : ch.failed, { message: r.message }) }),
			onError: (e) => setResult({ ok: false, text: fill(ch.failed, { message: portalMessage(e, "chat").title }) }),
		});
	return (
		<section
			aria-label={`${ch.proposed}: ${ch.tools[call.tool_name] ?? call.tool_name}`}
			data-testid="tool-call"
			className="grid gap-2 rounded-md border border-border bg-muted p-3"
		>
			<div className="flex flex-wrap items-center gap-2">
				<Wand2 className="size-4 text-primary" aria-hidden />
				<b className="text-[13px]">{ch.tools[call.tool_name] ?? call.tool_name}</b>
				<span className="text-[12px] text-muted-foreground">{ch.proposed}</span>
			</div>
			<dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 text-[12.5px]">
				{argumentRows(call, numberOf).map(([k, v]) => (
					<div key={k} className="contents">
						<dt className="text-muted-foreground">{k}</dt>
						<dd className="m-0 break-words whitespace-pre-wrap">{v}</dd>
					</div>
				))}
			</dl>
			<div className="flex flex-wrap items-center gap-3">
				<Button
					size="sm"
					variant="default"
					disabled={!canApply || run.isPending || result?.ok === true}
					onClick={apply}
				>
					{run.isPending ? ch.applying : ch.apply}
				</Button>
				{!canApply ? <span className="text-[12.5px] text-muted-foreground">{ch.needLock}</span> : null}
				{result ? (
					<span role="status" className={result.ok ? "text-[12.5px] text-success" : "text-[12.5px] text-destructive"}>
						{result.text}
					</span>
				) : null}
			</div>
		</section>
	);
}
