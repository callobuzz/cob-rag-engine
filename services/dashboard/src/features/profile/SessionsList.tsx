import { useQuery } from "@tanstack/react-query";
import { Monitor, ShieldCheck, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import type { SessionItem } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { signOut } from "@/lib/auth/actions";
import { formatRelative } from "@/lib/format/date";
import { profileStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { sessionsQuery, useRevokeOtherSessions, useRevokeSession } from "./api";
import { parseUserAgent } from "./ua";

const t2 = t.sessions;
const ACTIVE_MS = 5 * 60_000;

function lastSeen(s: SessionItem, now = Date.now()): string {
	const at = s.last_used_at ?? s.created_at;
	if (s.current || (at && now - new Date(at).getTime() < ACTIVE_MS)) return t2.activeNow;
	return at ? fill(t2.lastUsed, { when: formatRelative(at) }) : "";
}

/**
 * "Where you're signed in" (mockup `#sessList`): the current session first and marked; others
 * "Sign out" (`DELETE /auth/sessions/{id}`); "Sign out everywhere else" (`revoke-others`). The
 * current row's "Log out" is a normal log out. Device = in-house user-agent parse; IP as is.
 */
export function SessionsList() {
	const sessions = useQuery(sessionsQuery);
	const revoke = useRevokeSession();
	const others = useRevokeOtherSessions();
	const items = [...(sessions.data?.items ?? [])].sort((a, b) => Number(b.current) - Number(a.current));
	const hasOthers = items.some((s) => !s.current);

	const fail = (e: unknown) => {
		const msg = messageFor(toApiError(e));
		toast.error(msg.title, msg.body ? { description: msg.body } : undefined);
	};
	const signOutOne = async (s: SessionItem) => {
		try {
			await revoke.mutateAsync(s.id);
			toast.success(t2.signedOutToast, { description: t2.signedOutToastBody });
		} catch (e) {
			fail(e);
		}
	};
	const signOutOthers = async () => {
		try {
			await others.mutateAsync();
			toast.success(t2.othersToast, { description: t2.othersToastBody });
		} catch (e) {
			fail(e);
		}
	};

	return (
		<Panel
			title={t2.title}
			sub={t2.sub}
			className="self-start"
			aside={
				<Button size="sm" disabled={!hasOthers || others.isPending} onClick={() => void signOutOthers()}>
					{t2.others}
				</Button>
			}
		>
			{sessions.isPending ? (
				<TableSkeleton rows={3} />
			) : sessions.isError ? (
				<div className="p-4">
					<ErrorState error={sessions.error} title={t2.error} onRetry={() => void sessions.refetch()} />
				</div>
			) : (
				<ul aria-label={t2.label} className="m-0 grid list-none p-0">
					{items.map((s) => {
						const d = parseUserAgent(s.user_agent);
						const Icon = d.mobile ? Smartphone : Monitor;
						return (
							<li
								key={s.id}
								data-testid="session"
								data-current={s.current || undefined}
								className="flex min-w-0 flex-wrap items-center gap-3 border-b border-border px-4 py-2.5 last:border-b-0"
							>
								<Icon className={s.current ? "size-4 text-primary" : "size-4 text-muted-foreground"} aria-hidden />
								<div className="min-w-0 flex-1">
									<div className="font-semibold break-words">
										{d.label}
										{s.current ? ` · ${t2.thisDevice}` : ""}
									</div>
									<div className="text-[12.5px] text-muted-foreground">
										<span className="font-mono">{s.ip ?? t2.noIp}</span> · {lastSeen(s)}
									</div>
								</div>
								{s.current ? (
									<>
										<Badge variant="ok">{t2.current}</Badge>
										<Button size="sm" variant="ghost" aria-label={t2.logOutLabel} onClick={() => void signOut(null)}>
											{t2.logOut}
										</Button>
									</>
								) : (
									<Button
										size="sm"
										variant="ghost"
										aria-label={fill(t2.signOutFor, { device: d.label })}
										disabled={revoke.isPending}
										onClick={() => void signOutOne(s)}
									>
										{t2.signOut}
									</Button>
								)}
							</li>
						);
					})}
				</ul>
			)}
			<div className="flex items-start gap-2.5 rounded-b-lg border-t border-border bg-secondary px-4 py-3 text-[13px] text-foreground-2">
				<ShieldCheck className="mt-px size-4 shrink-0 text-muted-foreground" aria-hidden />
				<span>{t2.note}</span>
			</div>
		</Panel>
	);
}
