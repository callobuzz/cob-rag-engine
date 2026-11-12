import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { cn } from "@/lib/utils";
import { type Thread, threadsQuery } from "./api";
import { ChatView } from "./ChatView";

/** The small badges a chat row can carry (never colour alone: each has its words). */
export function chatBadges(c: Pick<Thread, "unread_count" | "bot_paused_until" | "opted_out" | "window_open">) {
	const out: { key: string; label: string; variant: "info" | "warn" | "default" }[] = [];
	if (c.unread_count > 0)
		out.push({ key: "unread", label: fill(t.chats.unread, { n: c.unread_count }), variant: "info" });
	if (c.bot_paused_until) out.push({ key: "paused", label: t.chats.paused, variant: "warn" });
	if (c.opted_out) out.push({ key: "stopped", label: t.chats.stopped, variant: "default" });
	if (!c.window_open) out.push({ key: "window", label: t.chats.windowClosed, variant: "default" });
	return out;
}

/** The chats list with "Unread only" and the chat beside it (TV6). */
export function ChatsList({ kb }: { kb: string }) {
	const id = useId();
	const [unread, setUnread] = useState(false);
	const [selected, setSelected] = useState<string | null>(null);
	const q = useQuery(threadsQuery(kb, unread));
	const items = q.data?.items ?? [];

	return (
		<section aria-label={t.chats.title} className="grid min-w-0 gap-3" data-testid="wa-chats">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<h2 className="m-0 text-[15px] font-bold">{t.chats.title}</h2>
				<label htmlFor={id} className="flex items-center gap-2 text-[13px]">
					<Checkbox id={id} checked={unread} onCheckedChange={(v) => setUnread(v === true)} />
					{t.chats.unreadOnly}
				</label>
			</div>
			<div className="grid gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
				<div className="min-w-0 rounded-lg border border-border bg-card">
					{q.isPending ? (
						<TableSkeleton rows={4} />
					) : q.isError ? (
						<div className="p-3">
							<ErrorState error={q.error} title={t.chats.loadError} onRetry={() => void q.refetch()} />
						</div>
					) : items.length === 0 ? (
						<EmptyState title={unread ? t.chats.emptyUnread : t.chats.empty} body="" />
					) : (
						<ul className="m-0 list-none p-0">
							{items.map((c) => (
								<li key={c.id} className="border-b border-border last:border-b-0">
									<button
										type="button"
										aria-label={fill(t.chats.open, { name: c.name_masked })}
										aria-current={selected === c.id || undefined}
										onClick={() => setSelected(c.id)}
										className={cn(
											"grid w-full gap-1 px-3 py-2.5 text-left hover:bg-accent",
											selected === c.id && "bg-accent",
										)}
									>
										<span className="flex items-baseline justify-between gap-2">
											<span className="truncate text-[13.5px] font-semibold">{c.name_masked}</span>
											<small
												className="shrink-0 text-muted-foreground"
												title={c.last_inbound_at ? formatDateTime(c.last_inbound_at) : undefined}
											>
												{c.last_inbound_at ? formatRelative(c.last_inbound_at) : ""}
											</small>
										</span>
										<span className="flex flex-wrap gap-1">
											{chatBadges(c).map((b) => (
												<Badge key={b.key} variant={b.variant}>
													{b.label}
												</Badge>
											))}
										</span>
									</button>
								</li>
							))}
						</ul>
					)}
				</div>
				<div className="min-w-0">
					{selected ? (
						<ChatView key={selected} kb={kb} threadId={selected} onBack={() => setSelected(null)} />
					) : (
						<p className="m-0 text-foreground-2">{t.chats.selectOne}</p>
					)}
				</div>
			</div>
		</section>
	);
}
