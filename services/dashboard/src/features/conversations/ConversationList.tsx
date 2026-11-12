import { zodResolver } from "@hookform/resolvers/zod";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge"; // TV6
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { fill } from "@/lib/strings/en";
import { playgroundStrings } from "@/lib/strings/playground";
import { whatsappStrings } from "@/lib/strings/whatsapp"; // TV6
import { cn } from "@/lib/utils";
import {
	type Conversation,
	conversationsQuery,
	useCreateConversation,
	useDeleteConversation,
	useRenameConversation,
} from "./api";

const t = playgroundStrings.conversations;

export const titleOf = (c: Pick<Conversation, "title">) => c.title?.trim() || t.untitled;

/** TV6: the channel a chat came from, when it is not the dashboard's own (website, WhatsApp). */
export const channelLabel = (c: Pick<Conversation, "channel">): string | null =>
	c.channel &&
	c.channel !== "api" &&
	c.channel !== "conversation" &&
	c.channel !== "stream" &&
	c.channel !== "playground"
		? (whatsappStrings.badge[c.channel] ?? c.channel)
		: null;

/** Left list: title, messages, tokens, updated; New, inline rename, delete (plain confirm). */
export function ConversationList({
	kb,
	selected,
	onSelect,
}: {
	kb: string;
	selected: string | undefined;
	onSelect: (id: string | undefined) => void;
}) {
	const q = useInfiniteQuery(conversationsQuery(kb));
	const create = useCreateConversation(kb);
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	const fail = (e: unknown) => toast.error(messageFor(toApiError(e), { ragUrl, support: supportEmail }).title);
	const items = q.data?.pages.flatMap((p) => p.items) ?? [];

	return (
		<section aria-label={t.listLabel} className="grid min-w-0 content-start rounded-lg border border-border bg-card">
			<div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
				<h2 className="text-[15px] font-bold">{t.listLabel}</h2>
				<Button
					size="sm"
					variant="default"
					disabled={create.isPending}
					onClick={() =>
						create.mutate(undefined, {
							onSuccess: (c) => {
								toast.success(t.created);
								onSelect(c.id);
							},
							onError: fail,
						})
					}
				>
					<Plus aria-hidden />
					{t.new}
				</Button>
			</div>
			{q.isPending ? (
				<TableSkeleton rows={4} />
			) : q.isError ? (
				<div className="p-3">
					<ErrorState error={q.error} onRetry={() => void q.refetch()} />
				</div>
			) : items.length === 0 ? (
				<div className="grid gap-1.5 px-4 py-6">
					<b>{t.emptyListTitle}</b>
					<p className="m-0 text-[13px] text-foreground-2">{t.emptyListBody}</p>
				</div>
			) : (
				<ul className="m-0 list-none p-0">
					{items.map((c) => (
						<Row key={c.id} kb={kb} c={c} selected={c.id === selected} onSelect={onSelect} onError={fail} />
					))}
				</ul>
			)}
			{q.hasNextPage ? (
				<div className="border-t border-border p-2">
					<Button size="sm" variant="ghost" disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
						{t.loadMore}
					</Button>
				</div>
			) : null}
		</section>
	);
}

function Row({
	kb,
	c,
	selected,
	onSelect,
	onError,
}: {
	kb: string;
	c: Conversation;
	selected: boolean;
	onSelect: (id: string | undefined) => void;
	onError: (e: unknown) => void;
}) {
	const [editing, setEditing] = useState(false);
	const [confirm, setConfirm] = useState(false);
	const rename = useRenameConversation(kb);
	const del = useDeleteConversation(kb);
	const form = useAppForm<{ title: string }>({
		resolver: zodResolver(z.object({ title: z.string().trim().min(1, "Enter a title.").max(200) })),
		defaultValues: { title: c.title ?? "" },
	});
	const titleError = form.formState.errors.title?.message;
	const renameSubmit = useAppSubmit(
		form,
		async (v) => {
			const next = v.title.trim();
			await rename.mutateAsync({ id: c.id, title: next });
			setEditing(false);
			toast.success(fill(t.renamed, { title: next }));
		},
		{
			onError: (err) => {
				onError(err);
				return true;
			},
		},
	);
	const id = useId();
	const name = titleOf(c);

	return (
		<li className={cn("border-b border-border last:border-b-0", selected && "bg-primary-soft")}>
			{editing ? (
				<form className="grid gap-2 px-4 py-3" noValidate onSubmit={(e) => void renameSubmit.onSubmit(e)}>
					<label htmlFor={id} className="sr-only">
						{t.renameLabel}
					</label>
					<Input
						id={id}
						maxLength={200}
						autoFocus
						aria-invalid={!!titleError || undefined}
						aria-describedby={describedBy(id, { error: !!titleError })}
						{...form.register("title")}
						onKeyDown={(e) => {
							if (e.key === "Escape") {
								e.preventDefault();
								setEditing(false);
								form.reset({ title: c.title ?? "" });
							}
						}}
					/>
					{titleError ? (
						<small id={`${id}-error`} className="text-[12.5px] font-medium text-destructive">
							{titleError}
						</small>
					) : null}
					<div className="flex gap-2">
						<SubmitButton size="sm" variant="default" pending={renameSubmit.isSubmitting}>
							{t.save}
						</SubmitButton>
						<Button
							type="button"
							size="sm"
							variant="ghost"
							onClick={() => {
								setEditing(false);
								form.reset({ title: c.title ?? "" });
							}}
						>
							{t.cancel}
						</Button>
					</div>
				</form>
			) : (
				<div className="flex items-start gap-1 px-2 py-1.5">
					<Link
						to="/conversations"
						search={{ kb, id: c.id }}
						aria-current={selected ? "page" : undefined}
						className="grid min-w-0 flex-1 gap-0.5 rounded-sm px-2 py-1.5 text-foreground no-underline hover:bg-accent"
					>
						<span className="flex min-w-0 items-center gap-1.5">
							<span className="truncate font-semibold">{name}</span>
							{channelLabel(c) ? <Badge data-testid="channel-badge">{channelLabel(c)}</Badge> : null}
						</span>
						<span className="truncate text-xs text-muted-foreground">
							{fill(t.meta, {
								messages: formatNumber(c.message_count),
								tokens: formatNumber(c.total_tokens),
								updated: formatRelative(c.updated_at),
							})}
						</span>
					</Link>
					<Button
						size="icon-sm"
						variant="ghost"
						aria-label={fill(t.rename, { title: name })}
						title={fill(t.rename, { title: name })}
						onClick={() => {
							form.reset({ title: c.title ?? "" });
							setEditing(true);
						}}
					>
						<Pencil aria-hidden />
					</Button>
					<Button
						size="icon-sm"
						variant="ghost"
						aria-label={fill(t.delete, { title: name })}
						title={fill(t.delete, { title: name })}
						onClick={() => setConfirm(true)}
					>
						<Trash2 aria-hidden />
					</Button>
				</div>
			)}
			<ConfirmDialog
				open={confirm}
				onOpenChange={setConfirm}
				title={fill(t.deleteTitle, { title: name })}
				body={t.deleteBody}
				confirmLabel={t.deleteConfirm}
				pending={del.isPending}
				onConfirm={() =>
					del.mutate(c.id, {
						onSuccess: () => {
							setConfirm(false);
							toast.success(fill(t.deleted, { title: name }));
							if (selected) onSelect(undefined);
						},
						onError: (e) => {
							setConfirm(false);
							onError(e);
						},
					})
				}
			/>
		</li>
	);
}
