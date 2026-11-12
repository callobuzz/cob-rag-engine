import { useQuery } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { cobStrings as t } from "@/lib/strings/cob";
import { fill } from "@/lib/strings/en";
import { cobConversationsQuery, useDeleteCobConversation } from "./api";

const when = (iso: string) =>
	new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(
		new Date(iso),
	);

/** The chat history drawer: own chats of the last 30 days, open one or delete it (plain confirm). */
export function CobHistory({
	currentId,
	onOpen,
	onDeleted,
}: {
	currentId: string | null;
	onOpen: (id: string) => void;
	onDeleted: (id: string) => void;
}) {
	const q = useQuery(cobConversationsQuery);
	const del = useDeleteCobConversation();

	if (q.isPending) {
		return (
			<div role="status" aria-busy="true" className="grid gap-2 p-3">
				<span className="sr-only">{t.historyLoading}</span>
				<Skeleton className="h-12 w-full" />
				<Skeleton className="h-12 w-full" />
			</div>
		);
	}
	if (q.isError) {
		return (
			<p role="alert" className="m-0 p-4 text-[13px] text-destructive">
				{t.historyFailed}
			</p>
		);
	}
	const items = q.data.items;
	if (!items.length) {
		return <p className="m-0 p-4 text-[13px] text-muted-foreground">{t.historyEmpty}</p>;
	}
	return (
		<ul aria-label={t.history} className="m-0 grid list-none gap-1 p-2">
			{items.map((c) => {
				const title = c.title?.trim() || t.historyUntitled;
				return (
					<li key={c.id} className="flex items-center gap-1 rounded-md hover:bg-accent">
						<button
							type="button"
							aria-label={fill(t.historyOpen, { title })}
							aria-current={c.id === currentId ? "true" : undefined}
							onClick={() => onOpen(c.id)}
							className="grid min-w-0 flex-1 gap-0.5 rounded-md px-2 py-2 text-left focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
						>
							<span className="truncate text-[13px] font-semibold">{title}</span>
							<span className="text-[11.5px] text-muted-foreground">
								{when(c.updated_at)} · {fill(t.historyMessages, { n: c.message_count })}
							</span>
						</button>
						<button
							type="button"
							aria-label={fill(t.historyDelete, { title })}
							disabled={del.isPending}
							onClick={() => {
								if (!window.confirm(t.historyDeleteConfirm)) return;
								del.mutate(c.id, {
									onSuccess: () => onDeleted(c.id),
									onError: () => toast.error(t.historyFailed),
								});
							}}
							className="grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground hover:text-destructive focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
						>
							<Trash2 className="size-4" aria-hidden />
						</button>
					</li>
				);
			})}
		</ul>
	);
}
