import { useState } from "react";
import { toast } from "sonner";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { landingStrings as t } from "@/lib/strings/landing";
import { type AskStatus, useConnectAsk, useDisconnectAsk, useOperatorKbs } from "./api";
import { useDraft } from "./DraftProvider";
import type { LText } from "./draft";
import { LocalizedField } from "./LocalizedField";

/** One plain sentence for each state the connection can be in. */
export function askSentence(status: AskStatus): string {
	return t.ask.status[status];
}

/**
 * "Ask it yourself": the button that lets a visitor try your real assistant on the page. It needs
 * one of your own knowledge bases and a key that allows this website's address; connecting makes
 * or reuses that key, and never changes a key you made for another site.
 */
export function AskCard() {
	const draft = useDraft();
	const kbs = useOperatorKbs();
	const connect = useConnectAsk();
	const disconnect = useDisconnectAsk();
	const [corpus, setCorpus] = useState(draft.state.ask.corpus_id ?? "");
	const [error, setError] = useState<string | null>(null);
	const { status } = draft.state.ask;
	const ask = draft.content.ask ?? { enabled: false, label: { en: "Ask it yourself" } };
	const noAddress = draft.state.address_effective == null;

	const run = async (action: () => Promise<unknown>, done: string) => {
		setError(null);
		try {
			await action();
			toast.success(done);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};

	return (
		<section
			aria-label={t.ask.title}
			className="grid content-start gap-3 rounded-lg border border-border bg-card p-4"
			data-testid="ask-card"
		>
			<div className="grid gap-0.5">
				<h2 className="m-0 text-[15px] font-bold">{t.ask.title}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">{t.ask.about}</p>
			</div>
			<div className="flex items-center gap-3">
				<Switch
					id="landing-ask-on"
					checked={ask.enabled}
					onCheckedChange={(v) =>
						draft.update((d) => {
							d.ask = { ...(d.ask ?? ask), enabled: v };
						})
					}
				/>
				<label htmlFor="landing-ask-on" className="text-[13px] font-semibold">
					{t.ask.show}
				</label>
			</div>
			<LocalizedField
				path="ask.label"
				label={t.ask.label}
				value={ask.label as LText}
				limitKey="ask.label"
				onChange={(v) =>
					draft.update((d) => {
						d.ask = { ...(d.ask ?? ask), label: v ?? { en: "" } };
					})
				}
			/>
			<p
				data-testid="ask-status"
				data-status={status}
				className={
					status === "ready" ? "m-0 text-[13px] font-medium text-success" : "m-0 text-[13px] text-foreground-2"
				}
			>
				{askSentence(status)}
			</p>
			{kbs.isPending ? null : kbs.kbs.length === 0 ? (
				<p className="m-0 text-[13px] text-muted-foreground">{t.ask.noKbs}</p>
			) : (
				<div className="grid gap-1.5">
					<label htmlFor="landing-ask-kb" className="text-[13px] font-semibold">
						{t.ask.kb}
					</label>
					<div className="flex flex-wrap gap-2">
						<NativeSelect
							id="landing-ask-kb"
							className="min-w-48 flex-1"
							value={corpus}
							onChange={(e) => setCorpus(e.target.value)}
						>
							<option value="">{t.generate.kbChoose}</option>
							{kbs.kbs.map((kb) => (
								<option key={kb.id} value={kb.id}>
									{kb.name}
								</option>
							))}
						</NativeSelect>
						<Button
							type="button"
							variant="outline"
							data-testid="ask-connect"
							disabled={corpus === "" || noAddress || connect.isPending}
							onClick={() => void run(() => connect.mutateAsync(corpus), t.ask.connected)}
						>
							{status === "ready" ? t.ask.reconnect : t.ask.connect}
						</Button>
						{status !== "not_connected" ? (
							<Button
								type="button"
								variant="ghost"
								data-testid="ask-disconnect"
								disabled={disconnect.isPending}
								onClick={() => void run(() => disconnect.mutateAsync(), t.ask.disconnected)}
							>
								{t.ask.disconnect}
							</Button>
						) : null}
					</div>
					{noAddress ? <small className="text-[12.5px] text-muted-foreground">{t.ask.needAddress}</small> : null}
				</div>
			)}
			{error ? (
				<p role="alert" className="m-0 text-[13px] text-destructive">
					{error}
				</p>
			) : null}
			<small className="text-[12.5px] text-muted-foreground">{t.ask.how}</small>
		</section>
	);
}
