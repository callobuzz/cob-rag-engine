import { Markdown } from "@/lib/markdown";
import { leadsStrings as t } from "@/lib/strings/leads";
import type { LeadDetail } from "./api";

/** The chat the lead came from. The visitor's words are plain text; the assistant's are Markdown. */
export function LeadTranscript({ messages }: { messages: LeadDetail["transcript"] }) {
	const rows = messages ?? [];
	return (
		<section aria-labelledby="lead-chat" className="grid gap-2">
			<h3 id="lead-chat" className="text-[13px] font-semibold">
				{t.transcript.title}
			</h3>
			{rows.length === 0 ? (
				<p className="m-0 text-[13px] text-muted-foreground">{t.transcript.empty}</p>
			) : (
				<ol className="m-0 grid list-none gap-2 p-0">
					{rows.map((m) => (
						<li
							key={`${m.created_at}-${m.role}-${m.content.slice(0, 24)}`}
							className="grid gap-0.5 rounded-md border border-border bg-card px-3 py-2 text-[13.5px]"
						>
							<span className="font-mono text-[10.5px] tracking-[.08em] text-muted-foreground uppercase">
								{m.role === "user" ? t.transcript.visitor : t.transcript.assistant}
							</span>
							{m.role === "user" ? (
								<p className="m-0 whitespace-pre-wrap">{m.content}</p>
							) : (
								<Markdown text={m.content} sources={[]} copyable={false} />
							)}
						</li>
					))}
				</ol>
			)}
		</section>
	);
}
