import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { landingStrings as t } from "@/lib/strings/landing";
import { useLandingSettings } from "./api";
import { useDraft } from "./DraftProvider";

/** `https://host` or `https://host:port`, no path (the engine checks it too). */
const ADDRESS = /^https:\/\/[A-Za-z0-9.-]+(:\d{1,5})?\/?$/;

/** Where the website lives, and what to set up so that address reaches it. */
export function AddressCard() {
	const draft = useDraft();
	const settings = useLandingSettings();
	const id = useId();
	const saved = draft.state.address ?? "";
	const [text, setText] = useState(saved);
	const [error, setError] = useState<string | null>(null);
	useEffect(() => setText(saved), [saved]);
	const valid = text.trim() === "" || ADDRESS.test(text.trim());

	const save = async (value: string | null) => {
		setError(null);
		try {
			await settings.mutateAsync({ address: value });
			toast.success(t.address.saved);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};

	return (
		<section
			aria-label={t.address.title}
			className="grid content-start gap-3 rounded-lg border border-border bg-card p-4"
			data-testid="address-card"
		>
			<div className="grid gap-0.5">
				<h2 className="m-0 text-[15px] font-bold">{t.address.title}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">{t.address.about}</p>
			</div>
			<div className="grid gap-1.5">
				<label htmlFor={id} className="text-[13px] font-semibold">
					{t.address.label}
				</label>
				<div className="flex flex-wrap gap-2">
					<Input
						id={id}
						className="max-w-sm flex-1"
						value={text}
						placeholder="https://www.sharmadental.in"
						inputMode="url"
						aria-invalid={!valid || undefined}
						aria-describedby={`${id}-hint`}
						onChange={(e) => setText(e.target.value)}
					/>
					<Button
						type="button"
						variant="outline"
						disabled={!valid || text.trim() === "" || text.trim() === saved || settings.isPending}
						onClick={() => void save(text.trim())}
					>
						{t.address.save}
					</Button>
					{saved ? (
						<Button type="button" variant="ghost" disabled={settings.isPending} onClick={() => void save(null)}>
							{t.address.useDefault}
						</Button>
					) : null}
				</div>
				<small
					id={`${id}-hint`}
					className={valid ? "text-[12.5px] text-muted-foreground" : "text-[12.5px] text-destructive"}
				>
					{valid ? t.address.hint : t.address.invalid}
				</small>
				{error ? (
					<p role="alert" className="m-0 text-[13px] text-destructive">
						{error}
					</p>
				) : null}
			</div>
			<details>
				<summary className="cursor-pointer text-[13px] font-semibold">{t.address.helpTitle}</summary>
				<ol className="mt-2 mb-0 grid list-decimal gap-1.5 pl-5 text-[13px] text-foreground-2">
					<li>{t.address.dns}</li>
					<li>{t.address.caddy}</li>
					<li>{t.address.off}</li>
					<li>{t.address.native}</li>
				</ol>
			</details>
		</section>
	);
}
