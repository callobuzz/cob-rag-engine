import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { toApiError } from "@/lib/api/errors";
import { formatRelative } from "@/lib/format/date";
import { localModelStrings as t } from "@/lib/strings/local-llm";
import { type LocalLlmTest, localLlmQuery, useLocalLlmTest } from "./local-llm-api";

/** The engine refuses a second test within this long; the button waits as long. */
const COOLDOWN_MS = 10_000;

const seconds = (ms: number | null) => (ms === null ? "n/a" : `${(ms / 1000).toFixed(1)} s`);

/** `ok` when ready, `warn` while it is on its way, `bad` when it is down, plain when not set up. */
function tone(state: string): "ok" | "warn" | "bad" | "default" {
	if (state === "ready") return "ok";
	if (state === "pulling" || state === "starting") return "warn";
	if (state === "down") return "bad";
	return "default";
}

/**
 * The free local model on Engine info (deployer only): not set up, downloading, starting, ready
 * or not reachable, in plain words, and a "Try it" button that makes it say a few words.
 */
export function LocalModelCard() {
	const q = useQuery(localLlmQuery);
	const test = useLocalLlmTest();
	const [result, setResult] = useState<LocalLlmTest | null>(null);
	const [problem, setProblem] = useState<string | null>(null);
	const [cooling, setCooling] = useState(false);
	const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
	useEffect(
		() => () => {
			if (timer.current) clearTimeout(timer.current);
		},
		[],
	);
	const status = q.data;

	const tryIt = () => {
		setProblem(null);
		setCooling(true);
		timer.current = setTimeout(() => setCooling(false), COOLDOWN_MS);
		test.mutate(undefined, {
			onSuccess: (r) => setResult(r),
			onError: (e) => {
				setResult(null);
				const code = toApiError(e).code;
				setProblem(
					code === "local_llm_test_too_soon" ? t.tooSoon : code === "local_llm_unavailable" ? t.notReady : t.failed,
				);
			},
		});
	};

	return (
		<Panel
			title={t.title}
			sub={t.sub}
			aside={
				status ? (
					<div className="flex items-center gap-2">
						<Badge variant={tone(status.state)}>
							{status.state === "ready" ? <CircleCheck aria-hidden /> : <CircleAlert aria-hidden />}
							{t.states[status.state] ?? status.state}
						</Badge>
						{status.state === "ready" ? (
							<Button size="sm" disabled={cooling || test.isPending} onClick={tryIt}>
								{test.isPending ? t.trying : cooling ? t.waiting : t.tryIt}
							</Button>
						) : null}
					</div>
				) : null
			}
		>
			{q.isPending ? (
				<CardSkeleton lines={3} />
			) : q.isError ? (
				<div className="p-3">
					<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
				</div>
			) : status ? (
				<>
					<p role="status" className="m-0 flex items-start gap-2 border-b border-border px-4 py-3 text-[13px]">
						{status.state === "pulling" || status.state === "starting" ? (
							<Loader2 aria-hidden className="mt-0.5 size-4 shrink-0 motion-safe:animate-spin" />
						) : null}
						<span>{t.body[status.state] ?? ""}</span>
					</p>
					{status.enabled ? (
						<KeyValue
							rows={[
								{ label: t.rows.model, value: status.model },
								{
									label: t.rows.held,
									value: status.models.length ? status.models.join(", ") : t.none,
									muted: !status.models.length,
								},
								{ label: t.rows.checked, value: formatRelative(status.checked_at) },
							]}
						/>
					) : null}
					{result ? (
						<div role="status" className="grid gap-1 border-t border-border px-4 py-3 text-[13px]">
							<b>
								{result.ok
									? t.result(
											seconds(result.first_token_ms),
											result.tokens_per_s === null ? null : String(Math.round(result.tokens_per_s)),
										)
									: t.noAnswer}
							</b>
							{result.answer ? (
								<span className="text-muted-foreground">
									{t.said}: “{result.answer}”
								</span>
							) : null}
						</div>
					) : null}
					{problem ? (
						<p role="alert" className="m-0 border-t border-border px-4 py-3 text-[13px] text-destructive">
							{problem}
						</p>
					) : null}
				</>
			) : null}
		</Panel>
	);
}
