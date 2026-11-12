import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { MessageSquare, MessagesSquare } from "lucide-react";
import { useCallback } from "react";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { PageHeader } from "@/components/shell/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import type { Corpus } from "@/lib/api/queries";
import { fill } from "@/lib/strings/en";
import { playgroundStrings } from "@/lib/strings/playground";
import { ConversationList } from "./ConversationList";
import { ConversationView } from "./ConversationView";

const t = playgroundStrings.conversations;
const route = getRouteApi("/_app/conversations");

/** `/conversations?kb=&id=`: the engine's conversation API (multi-turn, memory). */
export function ConversationsPage() {
	return (
		<KbScopedPage title={t.title} emptyBody={playgroundStrings.common.noKbBody}>
			{(kb) => <Conversations key={kb.id} kb={kb} />}
		</KbScopedPage>
	);
}

function Conversations({ kb }: { kb: Corpus }) {
	const { id } = route.useSearch();
	const navigate = useNavigate();
	const select = useCallback(
		(next: string | undefined) => void navigate({ to: "/conversations", search: { kb: kb.id, id: next } }),
		[navigate, kb.id],
	);
	const close = useCallback(() => select(undefined), [select]);
	return (
		<>
			<PageHeader
				title={t.title}
				description={fill(t.lead, { kb: kb.name })}
				actions={
					<Link to="/playground" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
						<MessageSquare aria-hidden />
						{t.playground}
					</Link>
				}
			/>
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[320px_minmax(0,1fr)]">
				<ConversationList kb={kb.id} selected={id} onSelect={select} />
				{id ? (
					<ConversationView key={id} kb={kb.id} tenantId={kb.tenant_id} id={id} onGone={close} />
				) : (
					<section className="grid min-h-[240px] place-content-center justify-items-center gap-2 rounded-lg border border-dashed border-input bg-card p-8 text-center">
						<MessagesSquare className="size-8 text-primary" aria-hidden />
						<h2 className="text-[17px] font-bold">{t.pickTitle}</h2>
						<p className="m-0 text-[13px] text-foreground-2">{t.pickBody}</p>
					</section>
				)}
			</div>
		</>
	);
}
