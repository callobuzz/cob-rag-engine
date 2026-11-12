import { TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Segmented } from "@/components/patterns/Segmented";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { landingStrings as t } from "@/lib/strings/landing";
import { type LandingLang, type PageKey, usePreviewLink } from "./api";
import { useDraft } from "./DraftProvider";

const PHONE = { w: 390, h: 800 };
const DESKTOP = { w: 1440, h: 900 };
/** Ask for a fresh link this long before the old one stops working. */
const REFRESH_BEFORE_MS = 60_000;
/** More lines than this for the headline at phone width is flagged. */
const FIT_LINES = 3;

type Mode = "light" | "dark";

/** Add what the preview page reads: `preview=1` makes it report its headline, `theme` forces light or dark. */
export function previewSrc(url: string, mode: Mode): string {
	const sep = url.includes("?") ? "&" : "?";
	return `${url}${sep}preview=1&theme=${mode}`;
}

function Frame({
	title,
	src,
	size,
	frameRef,
	testId,
}: {
	title: string;
	src: string;
	size: { w: number; h: number };
	frameRef?: React.Ref<HTMLIFrameElement>;
	testId: string;
}) {
	const box = useRef<HTMLDivElement>(null);
	const [scale, setScale] = useState(1);
	useEffect(() => {
		const el = box.current;
		if (!el) return;
		const measure = () => setScale(Math.min(1, el.clientWidth / size.w || 1));
		measure();
		if (typeof ResizeObserver === "undefined") return;
		const ro = new ResizeObserver(measure);
		ro.observe(el);
		return () => ro.disconnect();
	}, [size.w]);
	return (
		<div ref={box} className="min-w-0">
			<div style={{ height: size.h * scale }} className="overflow-hidden rounded-lg border border-border bg-white">
				<iframe
					ref={frameRef}
					title={title}
					data-testid={testId}
					src={src}
					sandbox="allow-scripts allow-same-origin"
					style={{
						width: size.w,
						height: size.h,
						transform: `scale(${scale})`,
						transformOrigin: "top left",
						border: 0,
					}}
				/>
			</div>
		</div>
	);
}

/**
 * The draft at phone and desktop size, side by side. The link is a signed, short-lived address of
 * the saved draft; it is made again after every save and shortly before it runs out. The preview
 * page tells us how many lines its headline takes at phone width, so a headline that wraps too
 * much is flagged here.
 */
export function PreviewPane({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
	const draft = useDraft();
	const link = usePreviewLink();
	const [page, setPage] = useState<PageKey>("home");
	const [lang, setLang] = useState<LandingLang>(draft.defaultLanguage);
	const [mode, setMode] = useState<Mode>("light");
	const [current, setCurrent] = useState<{ url: string; expiresAt: number } | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [lines, setLines] = useState<number | null>(null);
	const phone = useRef<HTMLIFrameElement>(null);
	const { mutateAsync } = link;

	const refresh = useCallback(async () => {
		try {
			const made = await mutateAsync({ page, lang });
			setCurrent({ url: made.url, expiresAt: Date.parse(made.expires_at) });
			setError(null);
			setLines(null);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	}, [mutateAsync, page, lang]);

	// A new link when it opens, when the page or language changes, and after each saved draft.
	// biome-ignore lint/correctness/useExhaustiveDependencies: the revision is the trigger
	useEffect(() => {
		if (open) void refresh();
	}, [open, refresh, draft.state.draft_revision]);

	// ...and just before the old one expires.
	useEffect(() => {
		if (!open || !current) return;
		const wait = Math.max(1_000, current.expiresAt - Date.now() - REFRESH_BEFORE_MS);
		const timer = setTimeout(() => void refresh(), wait);
		return () => clearTimeout(timer);
	}, [open, current, refresh]);

	// The preview page reports its headline; only the phone frame's report counts for the fit check.
	useEffect(() => {
		if (!current) return;
		const origin = new URL(current.url, window.location.href).origin;
		const onMessage = (event: MessageEvent) => {
			if (event.origin !== origin || event.source !== phone.current?.contentWindow) return;
			const data = event.data as { type?: string; headlineLines?: number } | null;
			if (data?.type === "landing-preview" && typeof data.headlineLines === "number") setLines(data.headlineLines);
		};
		window.addEventListener("message", onMessage);
		return () => window.removeEventListener("message", onMessage);
	}, [current]);

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent
				side="right"
				className="w-full gap-3 overflow-y-auto p-4 sm:max-w-none min-[1200px]:w-[min(1280px,94vw)]"
				data-testid="preview-pane"
			>
				<SheetHeader className="p-0 pr-8">
					<SheetTitle>{t.preview.title}</SheetTitle>
					<SheetDescription>{draft.dirty ? t.preview.unsaved : t.preview.about}</SheetDescription>
				</SheetHeader>
				<div className="flex flex-wrap items-end gap-4">
					<Segmented
						label={t.preview.page}
						value={page}
						onChange={setPage}
						options={[
							{ value: "home", label: t.preview.pages.home },
							{ value: "pricing", label: t.preview.pages.pricing },
							{ value: "features", label: t.preview.pages.features },
							{ value: "about", label: t.preview.pages.about },
							{ value: "contact", label: t.preview.pages.contact },
						]}
					/>
					{draft.languages.length > 1 ? (
						<Segmented
							label={t.preview.language}
							value={lang}
							onChange={setLang}
							options={draft.languages.map((l) => ({ value: l, label: t.langs[l] }))}
						/>
					) : null}
					<Segmented
						label={t.preview.mode}
						value={mode}
						onChange={setMode}
						options={[
							{ value: "light", label: t.look.light },
							{ value: "dark", label: t.look.dark },
						]}
					/>
				</div>
				{error ? (
					<p role="alert" className="m-0 text-[13px] text-destructive">
						{error}
					</p>
				) : null}
				{lines != null && lines > FIT_LINES ? (
					<p
						role="note"
						data-testid="fit-check"
						className="m-0 flex items-start gap-2 rounded-sm bg-warning-soft p-3 text-[13px] text-warning"
					>
						<TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
						{t.preview.fitCheck}
					</p>
				) : null}
				{current ? (
					<div className="grid items-start gap-4 min-[900px]:grid-cols-[390px_minmax(0,1fr)]">
						<Frame
							title={t.preview.phone}
							testId="preview-phone"
							src={previewSrc(current.url, mode)}
							size={PHONE}
							frameRef={phone}
						/>
						<Frame
							title={t.preview.desktop}
							testId="preview-desktop"
							src={previewSrc(current.url, mode)}
							size={DESKTOP}
						/>
					</div>
				) : null}
			</SheetContent>
		</Sheet>
	);
}
