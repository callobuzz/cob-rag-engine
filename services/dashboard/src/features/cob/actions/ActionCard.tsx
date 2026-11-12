import { Loader2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { SecretReveal } from "@/components/patterns/SecretReveal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { Markdown } from "@/lib/markdown/Markdown";
import { cobStrings as t } from "@/lib/strings/cob";
import { fill } from "@/lib/strings/en";
import { CobLinkButton } from "../CobLinkButton";
import type { CobActionCardBlock } from "../types";
import { type CobActionResult, useCancelCobAction, useConfirmCobAction } from "./api";

type Phase = "pending" | "confirming" | "done" | "failed" | "cancelled" | "expired";

const RISK_STYLE: Record<CobActionCardBlock["risk"], string> = {
	low: "border-border bg-secondary text-foreground-2",
	medium: "border-warning text-warning",
	high: "border-destructive text-destructive",
};

/** "9:42" left on a card that lives ten minutes. */
export function countdown(msLeft: number): string {
	const total = Math.max(0, Math.ceil(msLeft / 1000));
	const m = Math.floor(total / 60);
	const s = total % 60;
	return `${m}:${String(s).padStart(2, "0")}`;
}

function initialPhase(block: CobActionCardBlock, now: number): Phase {
	const status = block.status ?? "pending";
	if (status === "pending") return Date.parse(block.expires_at) <= now ? "expired" : "pending";
	return status;
}

/**
 * A change COB proposes (COB5). Nothing happens until the person presses Confirm; a destructive
 * card also needs its name typed exactly, like the dashboard's own delete dialogs. The result
 * replaces the buttons in place; a rotated key is shown once and dropped from memory on close.
 * A card read back from History shows how it ended and cannot be pressed again.
 */
export function ActionCard({ block, onAskAgain }: { block: CobActionCardBlock; onAskAgain?: () => void }) {
	const confirm = useConfirmCobAction();
	const cancel = useCancelCobAction();
	const [now, setNow] = useState(() => Date.now());
	const [phase, setPhase] = useState<Phase>(() => initialPhase(block, Date.now()));
	const [typed, setTyped] = useState("");
	const [result, setResult] = useState<CobActionResult | null>(null);
	const [secret, setSecret] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [cancelError, setCancelError] = useState<string | null>(null);
	const fieldId = useId();

	const live = phase === "pending" || phase === "confirming";
	useEffect(() => {
		if (!live) return;
		const id = setInterval(() => setNow(Date.now()), 1000);
		return () => clearInterval(id);
	}, [live]);
	const msLeft = Date.parse(block.expires_at) - now;
	useEffect(() => {
		if (phase === "pending" && msLeft <= 0) setPhase("expired");
	}, [phase, msLeft]);

	const word = block.destructive ? block.confirm_name : null;
	const matches = word == null || typed.trim() === word;
	const busy = phase === "confirming";
	const suspend = block.tool.startsWith("suspend");
	const confirmLabel = block.destructive ? (suspend ? t.actions.suspend : t.actions.delete) : t.actions.confirm;

	const onConfirm = () => {
		if (!matches || busy) return;
		setError(null);
		setPhase("confirming");
		confirm.mutate(
			{ id: block.proposal_id, confirmName: word == null ? null : typed.trim() },
			{
				onSuccess: (r) => {
					setResult(r);
					setSecret(r.secret ?? null);
					setPhase(r.status === "done" ? "done" : "failed");
				},
				onError: (e) => {
					const err = toApiError(e);
					if (err.code === "cob_proposal_expired") return setPhase("expired");
					if (err.code === "confirm_mismatch") {
						setError(messageFor(err).title);
						return setPhase("pending");
					}
					// Used, not found, or the workspace / role changed: it cannot run, say why.
					setResult({
						proposal_id: block.proposal_id,
						status: "failed",
						message: messageFor(err).title,
						link: null,
						secret: null,
						http_status: err.status,
						code: err.code ?? null,
					});
					setPhase("failed");
				},
			},
		);
	};

	const onCancel = () => {
		setCancelError(null);
		cancel.mutate(block.proposal_id, {
			onSuccess: () => setPhase("cancelled"),
			onError: (e) => {
				const err = toApiError(e);
				if (err.code === "cob_proposal_expired") return setPhase("expired");
				if (err.code === "cob_proposal_used") {
					// It already ran (or was cancelled) elsewhere: never say nothing changed.
					setResult({
						proposal_id: block.proposal_id,
						status: "failed",
						message: messageFor(err).title,
						link: null,
						secret: null,
						http_status: err.status,
						code: err.code,
					});
					return setPhase("failed");
				}
				// Could not reach the engine: the card is still live, so keep it that way.
				setCancelError(messageFor(err).title);
			},
		});
	};

	const ended: Record<string, string> = {
		done: t.actions.statusDone,
		failed: t.actions.statusFailed,
		cancelled: t.actions.statusCancelled,
		expired: t.actions.statusExpired,
	};

	return (
		<section
			className="grid gap-3 rounded-lg border border-border bg-card p-3"
			data-testid="cob-action-card"
			data-phase={phase}
			aria-label={`${t.actions.card}: ${block.title}`}
		>
			<header className="flex flex-wrap items-center gap-2">
				<h3 className="m-0 text-[14px] font-bold">{block.title}</h3>
				<span
					className={`rounded-full border px-2 py-0.5 text-[11.5px] font-semibold ${RISK_STYLE[block.risk]}`}
					data-testid="cob-action-risk"
				>
					{suspend ? t.actions.riskSuspend : t.actions.risk[block.risk]}
				</span>
				{phase !== "pending" && phase !== "confirming" ? (
					<span className="ml-auto text-[12px] font-semibold text-muted-foreground">{ended[phase]}</span>
				) : null}
			</header>

			<Markdown text={block.summary} />

			{block.args.length ? (
				<dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12.5px]">
					{block.args.map((a) => (
						<div key={`${a.label}:${a.value}`} className="contents">
							<dt className="text-muted-foreground">{a.label}</dt>
							<dd className="m-0 font-medium break-words">{a.value}</dd>
						</div>
					))}
				</dl>
			) : null}

			{phase === "pending" || phase === "confirming" ? (
				<>
					{word != null ? (
						<div className="grid gap-1.5">
							<label htmlFor={fieldId} className="text-[13px] font-semibold">
								{fill(t.actions.typeToConfirm, { name: word })}
							</label>
							<Input
								id={fieldId}
								className="font-mono"
								autoComplete="off"
								value={typed}
								disabled={busy}
								onChange={(e) => setTyped(e.target.value)}
								aria-invalid={error ? true : undefined}
							/>
							{error ? (
								<small role="alert" className="text-[12.5px] font-medium text-destructive">
									{error}
								</small>
							) : null}
						</div>
					) : null}
					<div className="flex flex-wrap items-center gap-2">
						<Button
							type="button"
							size="sm"
							variant={block.destructive ? "destructive" : "brand"}
							disabled={!matches || busy}
							onClick={onConfirm}
						>
							{busy ? <Loader2 className="animate-spin" aria-hidden /> : null}
							{busy ? t.actions.confirming : confirmLabel}
						</Button>
						<Button type="button" size="sm" variant="outline" disabled={busy || cancel.isPending} onClick={onCancel}>
							{t.actions.cancel}
						</Button>
						<span className="ml-auto text-[12px] text-muted-foreground" data-testid="cob-action-countdown">
							{fill(t.actions.expires, { time: countdown(msLeft) })}
						</span>
					</div>
					{cancelError ? (
						<small role="alert" className="text-[12.5px] font-medium text-destructive">
							{cancelError}
						</small>
					) : null}
				</>
			) : null}

			{(phase === "done" || phase === "failed") && result ? (
				<div role="status" className="grid gap-2" data-testid="cob-action-result">
					{result ? (
						<>
							<div className={phase === "failed" ? "text-destructive" : undefined}>
								<span className="sr-only">{phase === "done" ? t.actions.resultDone : t.actions.resultFailed}: </span>
								<Markdown text={result.message} />
							</div>
							{phase === "failed" ? (
								<p className="m-0 text-[12.5px] text-muted-foreground">{t.actions.failedHint}</p>
							) : null}
							{secret ? (
								<div className="grid gap-2">
									<SecretReveal value={secret} label={t.actions.secretLabel} />
									<Button type="button" size="sm" variant="outline" className="w-fit" onClick={() => setSecret(null)}>
										{t.actions.secretHide}
									</Button>
								</div>
							) : result.secret ? (
								<p className="m-0 text-[12.5px] text-muted-foreground">{t.actions.secretHidden}</p>
							) : null}
							{result.link ? <CobLinkButton block={result.link} /> : null}
						</>
					) : null}
				</div>
			) : null}

			{phase === "cancelled" ? <p className="m-0 text-[13px] text-muted-foreground">{t.actions.cancelled}</p> : null}

			{phase === "expired" ? (
				<div className="flex flex-wrap items-center gap-2">
					<p role="status" className="m-0 text-[13px] text-muted-foreground">
						{t.actions.expired}
					</p>
					{onAskAgain ? (
						<Button type="button" size="sm" variant="outline" onClick={onAskAgain}>
							{t.actions.askAgain}
						</Button>
					) : null}
				</div>
			) : null}
		</section>
	);
}
