import { Bot, ImagePlus, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { onboardingStrings } from "@/lib/strings/onboarding";

const t = onboardingStrings.one.avatar;

/** Types the engine accepts (it refuses SVG and GIF and re-saves the rest as a PNG). */
export const AVATAR_TYPES = "image/png,image/jpeg,image/webp";

/**
 * The assistant's picture: a round preview, a button to choose or replace it, and a message when
 * the engine refused the file. It only picks; the caller uploads (the wizard waits for the
 * knowledge base to exist, the Assistant page uploads at once). A chosen file shows from the
 * browser's own copy, so nothing leaves the page until the caller sends it.
 */
export function AvatarUpload({
	current,
	file,
	onPick,
	onRemove,
	busy,
	error,
}: {
	/** The picture the assistant has now (an address a browser can load), or null. */
	current: string | null;
	/** A picture chosen but not sent yet. */
	file?: File | null;
	onPick: (file: File) => void;
	onRemove?: () => void;
	busy?: boolean;
	error?: string | null;
}) {
	const id = useId();
	const input = useRef<HTMLInputElement>(null);
	const [local, setLocal] = useState<string | null>(null);
	useEffect(() => {
		if (!file) {
			setLocal(null);
			return;
		}
		const url = URL.createObjectURL(file);
		setLocal(url);
		return () => URL.revokeObjectURL(url);
	}, [file]);
	const shown = local ?? current;
	return (
		<div className="grid gap-2">
			<div className="flex flex-wrap items-center gap-3">
				{shown ? (
					<img
						src={shown}
						alt={t.preview}
						className="size-16 shrink-0 rounded-full border border-border object-cover"
						data-testid="avatar-preview"
					/>
				) : (
					<span
						role="img"
						aria-label={t.none}
						className="grid size-16 shrink-0 place-items-center rounded-full bg-secondary text-muted-foreground"
						data-testid="avatar-empty"
					>
						<Bot className="size-7" aria-hidden />
					</span>
				)}
				<div className="flex flex-wrap items-center gap-2">
					<input
						ref={input}
						id={id}
						type="file"
						accept={AVATAR_TYPES}
						className="sr-only"
						aria-label={t.label}
						disabled={busy}
						onChange={(e) => {
							const picked = e.target.files?.[0];
							if (picked) onPick(picked);
							e.target.value = "";
						}}
					/>
					<Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => input.current?.click()}>
						<ImagePlus aria-hidden />
						{shown ? t.replace : t.choose}
					</Button>
					{shown && onRemove ? (
						<Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onRemove}>
							<Trash2 aria-hidden />
							{t.remove}
						</Button>
					) : null}
				</div>
			</div>
			<small className="text-[12.5px] text-muted-foreground">{t.hint}</small>
			{error ? (
				<small className="text-[12.5px] font-medium text-destructive" role="alert">
					{error}
				</small>
			) : null}
		</div>
	);
}
