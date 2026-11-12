import { Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from "@/components/ui/dialog";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { landingStrings as t } from "@/lib/strings/landing";
import { type GenerateResult, type LandingLang, useGenerate, useOperatorKbs } from "./api";
import { useDraft } from "./DraftProvider";
import { applyGenerated } from "./draft";

/** A written conversation as a small chat: what a visitor would see at the top of the page. */
function ChatPreview({ result, lang }: { result: GenerateResult; lang: LandingLang }) {
	const say = (text: Record<string, string> | null | undefined) => text?.[lang] ?? text?.en ?? "";
	return (
		<div className="grid gap-2 rounded-sm bg-secondary p-3" data-testid="generate-result">
			<strong className="text-[13px]">
				{say(result.conversation.assistant_name)} · {say(result.conversation.business_name)}
			</strong>
			{result.conversation.turns.map((turn, i) => (
				// biome-ignore lint/suspicious/noArrayIndexKey: a chat's lines are identified by their order
				<div key={i} className={turn.role === "visitor" ? "justify-self-end" : "justify-self-start"}>
					<p
						className={
							turn.role === "visitor"
								? "m-0 max-w-[32ch] rounded-lg bg-brand-soft px-3 py-2 text-[13.5px]"
								: "m-0 max-w-[36ch] rounded-lg bg-card px-3 py-2 text-[13.5px]"
						}
					>
						{say(turn.text)}
					</p>
					{turn.source ? <small className="ml-1 text-[12px] text-muted-foreground">{say(turn.source)}</small> : null}
				</div>
			))}
			{result.sources.length > 0 ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">
					{t.generate.sources}:{" "}
					{result.sources.map((s) => (s.heading ? `${s.title} › ${s.heading}` : s.title)).join(", ")}
				</p>
			) : null}
			<p className="m-0 text-[12.5px] text-muted-foreground">{result.cost_note}</p>
		</div>
	);
}

/**
 * Write the sample conversation from one of the operator's own knowledge bases. One answer from
 * the operator's own AI model; the result is shown for review and changes nothing until it is used,
 * and even then only the draft in memory.
 */
export function GenerateDialog() {
	const draft = useDraft();
	const kbs = useOperatorKbs();
	const generate = useGenerate();
	const [open, setOpen] = useState(false);
	const [corpus, setCorpus] = useState("");
	const [lang, setLang] = useState<LandingLang>(draft.defaultLanguage);
	const [result, setResult] = useState<GenerateResult | null>(null);
	const [error, setError] = useState<string | null>(null);

	const hero = draft.content.home.find((s) => s.type === "hero");
	const heroConversation = hero && "conversation" in hero ? hero.conversation : null;
	const useIt =
		result && heroConversation
			? applyGenerated(heroConversation, result.conversation, lang, draft.defaultLanguage)
			: null;

	const close = (next: boolean) => {
		setOpen(next);
		if (!next) {
			setResult(null);
			setError(null);
		}
	};

	const run = async () => {
		setError(null);
		setResult(null);
		try {
			setResult(
				await generate.mutateAsync({ corpus_id: corpus, language: lang, preset: heroConversation?.preset ?? null }),
			);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};

	const apply = () => {
		if (!useIt) return;
		draft.update((d) => {
			const target = d.home.find((s) => s.type === "hero");
			if (target && "conversation" in target) target.conversation = useIt;
		});
		toast.success(t.generate.used);
		close(false);
	};

	return (
		<Dialog open={open} onOpenChange={close}>
			<DialogTrigger asChild>
				<Button type="button" variant="outline" data-testid="generate-open">
					<Sparkles aria-hidden />
					{t.generate.open}
				</Button>
			</DialogTrigger>
			<DialogContent className="sm:max-w-xl">
				<DialogHeader>
					<DialogTitle>{t.generate.title}</DialogTitle>
					<DialogDescription>{t.generate.lead}</DialogDescription>
				</DialogHeader>
				<p className="m-0 text-[13px] font-medium" data-testid="generate-cost">
					{t.generate.cost}
				</p>
				{kbs.isPending ? null : kbs.kbs.length === 0 ? (
					<p className="m-0 text-[13px] text-muted-foreground">{t.generate.noKbs}</p>
				) : (
					<div className="grid gap-3 min-[600px]:grid-cols-2">
						<div className="grid gap-1.5">
							<label htmlFor="generate-kb" className="text-[13px] font-semibold">
								{t.generate.kb}
							</label>
							<NativeSelect id="generate-kb" value={corpus} onChange={(e) => setCorpus(e.target.value)}>
								<option value="">{t.generate.kbChoose}</option>
								{kbs.kbs.map((kb) => (
									<option key={kb.id} value={kb.id}>
										{kb.name}
									</option>
								))}
							</NativeSelect>
						</div>
						<div className="grid gap-1.5">
							<label htmlFor="generate-lang" className="text-[13px] font-semibold">
								{t.generate.language}
							</label>
							<NativeSelect id="generate-lang" value={lang} onChange={(e) => setLang(e.target.value as LandingLang)}>
								{draft.languages.map((l) => (
									<option key={l} value={l}>
										{t.langs[l]}
									</option>
								))}
							</NativeSelect>
						</div>
					</div>
				)}
				{error ? (
					<p role="alert" className="m-0 text-[13px] text-destructive">
						{error}
					</p>
				) : null}
				{result ? (
					<>
						<h3 className="m-0 text-[14px] font-semibold">{t.generate.resultTitle}</h3>
						<ChatPreview result={result} lang={lang} />
						{useIt ? null : (
							<p role="note" className="m-0 text-[12.5px] text-warning">
								{t.generate.lineMismatch}
							</p>
						)}
					</>
				) : null}
				<DialogFooter>
					{result ? (
						<>
							<Button type="button" variant="ghost" data-testid="generate-discard" onClick={() => close(false)}>
								{t.generate.discard}
							</Button>
							<Button type="button" variant="brand" data-testid="generate-use" disabled={!useIt} onClick={apply}>
								{t.generate.use}
							</Button>
						</>
					) : (
						<Button
							type="button"
							variant="brand"
							data-testid="generate-run"
							disabled={corpus === "" || generate.isPending}
							onClick={() => void run()}
						>
							{generate.isPending ? t.generate.running : t.generate.run}
						</Button>
					)}
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
