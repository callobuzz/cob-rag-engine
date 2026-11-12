import { Link } from "@tanstack/react-router";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { KeysPanel } from "@/features/keys/KeysPanel";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { type CorpusDetail, useCorpusWidgetKeys } from "../api";
import { Panel } from "../ui";

/**
 * API keys (mockup `t-apikey`): the knowledge base's named ck_ keys (create, edit, rotate, revoke;
 * the migrated single key is the "Default key"), and its widget keys read-only.
 */
export function ApiKeyTab({ kb }: { kb: CorpusDetail }) {
	return (
		<div className="grid gap-4">
			<KeysPanel owner={{ kind: "kb", id: kb.id, name: kb.name }} />
			<WidgetKeysCard kb={kb} />
		</div>
	);
}

function WidgetKeysCard({ kb }: { kb: CorpusDetail }) {
	const keys = useCorpusWidgetKeys(kb.id);
	return (
		<Panel
			title={fill(t.key.otherTitle, { name: kb.name })}
			sub={t.key.otherSub}
			aside={
				<Link to="/widget" search={{ kb: kb.id }} className={buttonVariants({ size: "sm" })}>
					{t.key.manage}
				</Link>
			}
		>
			{keys.isPending ? (
				<TableSkeleton rows={2} />
			) : keys.isError ? (
				<div className="p-4">
					<ErrorState error={keys.error} onRetry={() => void keys.refetch()} />
				</div>
			) : keys.data.length === 0 ? (
				<p className="m-0 p-4 text-[13px] text-muted-foreground">{t.key.noWidgetKeys}</p>
			) : (
				<ul className="m-0 grid list-none p-0">
					{keys.data.map((k) => (
						<li
							key={k.id}
							className="flex min-w-0 items-center gap-3 border-b border-border px-4 py-2.5 last:border-b-0"
						>
							<div className="min-w-0 flex-1">
								<div className="truncate font-semibold">
									{k.name}{" "}
									<span className="font-mono text-[12px] font-normal text-muted-foreground">
										{k.public_key.slice(0, 16)}…
									</span>
								</div>
								<div className="truncate text-[12.5px] text-muted-foreground">
									{(k.allowed_origins.length
										? k.allowed_origins.map((o) => o.replace(/^https?:\/\//, "")).join(", ")
										: t.key.anySite) +
										" · " +
										fill(t.key.limits, {
											rpm: formatNumber(k.rate_limit_per_minute),
											daily: formatNumber(k.daily_message_limit),
										})}
								</div>
							</div>
							<Badge variant={k.is_active ? "ok" : "default"}>{k.is_active ? t.key.active : t.key.paused}</Badge>
						</li>
					))}
				</ul>
			)}
		</Panel>
	);
}
