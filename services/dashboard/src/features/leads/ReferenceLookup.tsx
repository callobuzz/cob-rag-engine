import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Field } from "@/components/auth/Field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toApiError } from "@/lib/api/errors";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import { leadsStrings } from "@/lib/strings/leads";
import { type ReferenceLookup as Lookup, lookupReference } from "./handoffs-api";
import { normalizeReference } from "./reference";

const t = leadsStrings.reference;

/** TV2: type a reference a visitor quoted (any case, with or without the dash) and open that chat. */
export function ReferenceLookup({
	onOpenHandoff,
	onOpenLead,
}: {
	onOpenHandoff: (id: string) => void;
	onOpenLead: (id: string) => void;
}) {
	const id = useId();
	const [value, setValue] = useState("");
	const [message, setMessage] = useState<string | null>(null);
	const [busy, setBusy] = useState(false);
	const [choices, setChoices] = useState<Lookup["matches"]>([]);
	const kbs = useQuery(corporaSwitcherQuery);
	const kbName = (kbId: string) => kbs.data?.items.find((k) => k.id === kbId)?.name ?? kbId;
	const openMatch = (m: Lookup["matches"][number] | undefined) => {
		const handoff = m?.handoff_ids[0];
		const lead = m?.lead_ids[0];
		if (handoff) onOpenHandoff(handoff);
		else if (lead) onOpenLead(lead);
		else setMessage(t.noPerson);
	};

	const find = async () => {
		const ref = normalizeReference(value);
		if (!ref) {
			setMessage(t.invalid);
			return;
		}
		setBusy(true);
		setMessage(null);
		setChoices([]);
		try {
			const found = await lookupReference(ref);
			// a reference is unique per knowledge base: with two matches the owner picks, never a guess
			if (found.matches.length > 1) setChoices(found.matches);
			else openMatch(found.matches[0]);
		} catch (e) {
			const err = toApiError(e);
			setMessage(
				err.code === "reference_not_found" ? t.notFound : err.code === "reference_invalid" ? t.invalid : t.failed,
			);
		} finally {
			setBusy(false);
		}
	};

	return (
		<form
			className="grid gap-2 rounded-lg border border-border bg-card p-3.5"
			aria-label={t.formLabel}
			onSubmit={(e) => {
				e.preventDefault();
				void find();
			}}
		>
			<div className="flex flex-wrap items-end gap-3">
				<Field id={id} label={t.findTitle} hint={t.findHint}>
					<Input
						id={id}
						value={value}
						placeholder={t.placeholder}
						autoComplete="off"
						autoCapitalize="characters"
						spellCheck={false}
						className="w-40 font-mono uppercase"
						aria-describedby={message ? `${id}-hint ${id}-msg` : `${id}-hint`}
						onChange={(e) => {
							setValue(e.target.value);
							setMessage(null);
							setChoices([]);
						}}
					/>
				</Field>
				<Button type="submit" disabled={busy || value.trim() === ""}>
					{busy ? t.finding : t.findButton}
				</Button>
			</div>
			{choices.length > 1 ? (
				<div className="grid gap-2">
					<p className="m-0 text-[13px] text-muted-foreground">{t.several.replace("{n}", String(choices.length))}</p>
					<ul className="m-0 flex list-none flex-wrap gap-2 p-0">
						{choices.map((m) => (
							<li key={m.conversation_id}>
								<Button type="button" variant="outline" onClick={() => openMatch(m)}>
									{kbName(m.corpus_id)} · {m.handoff_ids.length > 0 ? t.openHandoff : t.openLead}
								</Button>
							</li>
						))}
					</ul>
				</div>
			) : null}
			{message ? (
				<p id={`${id}-msg`} role="status" className="m-0 text-[13px] text-muted-foreground">
					{message}
				</p>
			) : null}
		</form>
	);
}
