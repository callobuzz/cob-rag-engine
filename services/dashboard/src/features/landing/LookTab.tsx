import { useEffect, useId, useRef, useState } from "react";
import { Segmented } from "@/components/patterns/Segmented";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { landingStrings as t } from "@/lib/strings/landing";
import { type LandingAsset, type LandingContent, type LandingThemePreview, useThemePreview } from "./api";
import { ContrastReport } from "./ContrastReport";
import { useDraft } from "./DraftProvider";
import { FontPicker } from "./FontPicker";
import type { PairingKey } from "./fontPairings";
import { MediaUploader } from "./MediaUploader";

const HEX = /^#[0-9a-fA-F]{6}$/;
const DEBOUNCE_MS = 250;

type Theme = NonNullable<LandingContent["theme"]>;

/** A colour picked by eye or typed as a code. Only a complete code is sent upward. */
function ColourField({
	label,
	value,
	onChange,
	hint,
}: {
	label: string;
	value: string;
	onChange: (hex: string) => void;
	hint?: string;
}) {
	const id = useId();
	const [text, setText] = useState(value);
	useEffect(() => setText(value), [value]);
	return (
		<div className="grid content-start gap-1.5">
			<label htmlFor={`${id}-hex`} className="text-[13px] font-semibold">
				{label}
			</label>
			<div className="flex items-center gap-2">
				<input
					type="color"
					aria-label={`${label} (picker)`}
					value={HEX.test(value) ? value : "#000000"}
					onChange={(e) => onChange(e.target.value)}
					className="size-9 cursor-pointer rounded-sm border border-input bg-transparent p-0.5"
				/>
				<Input
					id={`${id}-hex`}
					value={text}
					spellCheck={false}
					className="max-w-32 font-mono"
					aria-describedby={hint ? `${id}-hint` : undefined}
					aria-invalid={!HEX.test(text) || undefined}
					onChange={(e) => {
						setText(e.target.value);
						if (HEX.test(e.target.value)) onChange(e.target.value.toLowerCase());
					}}
				/>
			</div>
			{hint ? (
				<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
					{hint}
				</small>
			) : null}
		</div>
	);
}

/** Brand colour, fonts, logo and video: everything that changes how the website looks, not what it says. */
export function LookTab() {
	const draft = useDraft();
	const theme: Theme = draft.content.theme ?? {
		brand_color: "#0f766e",
		base: "light",
		button_shape: "round",
		font_pairing: "neutral",
	};
	const setTheme = (patch: Partial<Theme>) =>
		draft.update((d) => {
			d.theme = { ...(d.theme ?? theme), ...patch };
		});

	// The palette for what is chosen now, from the engine, a short pause after the last change.
	const preview = useThemePreview();
	const [shown, setShown] = useState<LandingThemePreview>(draft.state.draft_theme);
	const first = useRef(true);
	const { brand_color, accent_color, base } = theme;
	const run = preview.mutateAsync;
	// biome-ignore lint/correctness/useExhaustiveDependencies: `run` is stable; the three values drive it
	useEffect(() => {
		if (first.current) {
			first.current = false;
			return;
		}
		const timer = setTimeout(() => {
			run({ brand_color, accent_color: accent_color ?? null, base })
				.then(setShown)
				.catch(() => {});
		}, DEBOUNCE_MS);
		return () => clearTimeout(timer);
	}, [brand_color, accent_color, base]);

	const video = draft.state.assets.find((a) => a.kind === "video");
	const poster = draft.state.assets.find((a) => a.kind === "poster");
	const videoSection = draft.content.home.find((s) => s.type === "video");
	const showVideo = () => {
		if (!video || !poster) return;
		draft.update((d) => {
			const existing = d.home.find((s) => s.type === "video");
			if (existing && existing.type === "video") {
				existing.video_asset_id = video.id;
				existing.poster_asset_id = poster.id;
				existing.enabled = true;
				return;
			}
			const at = d.home.findIndex((s) => s.type === "final_cta");
			d.home.splice(at < 0 ? d.home.length : at, 0, {
				id: "video",
				type: "video",
				enabled: true,
				video_asset_id: video.id,
				poster_asset_id: poster.id,
				placement: "section",
			});
		});
	};

	return (
		<div className="grid gap-4" data-testid="look-tab">
			<section aria-label={t.look.colourTitle} className="grid gap-4 rounded-lg border border-border bg-card p-4">
				<h2 className="m-0 text-[15px] font-bold">{t.look.colourTitle}</h2>
				<div className="grid gap-4 min-[861px]:grid-cols-2">
					<ColourField
						label={t.look.brand}
						value={brand_color}
						hint={t.look.brandHint}
						onChange={(hex) => setTheme({ brand_color: hex })}
					/>
					<div className="grid content-start gap-2">
						<div className="flex items-center gap-3">
							<Switch
								id="landing-accent-on"
								checked={accent_color != null}
								onCheckedChange={(on) => setTheme({ accent_color: on ? brand_color : null })}
							/>
							<label htmlFor="landing-accent-on" className="text-[13px] font-semibold">
								{t.look.accentOn}
							</label>
						</div>
						{accent_color != null ? (
							<ColourField
								label={t.look.accent}
								value={accent_color}
								onChange={(hex) => setTheme({ accent_color: hex })}
							/>
						) : null}
					</div>
				</div>
				<div className="flex flex-wrap gap-6">
					<div className="grid gap-1.5">
						<span className="text-[13px] font-semibold">{t.look.base}</span>
						<Segmented
							label={t.look.base}
							value={base}
							onChange={(v) => setTheme({ base: v })}
							options={[
								{ value: "light", label: t.look.light },
								{ value: "dark", label: t.look.dark },
								{ value: "auto", label: t.look.auto },
							]}
						/>
					</div>
					<div className="grid gap-1.5">
						<span className="text-[13px] font-semibold">{t.look.shape}</span>
						<Segmented
							label={t.look.shape}
							value={theme.button_shape}
							onChange={(v) => setTheme({ button_shape: v })}
							options={[
								{ value: "round", label: t.look.round },
								{ value: "soft", label: t.look.soft },
							]}
						/>
					</div>
				</div>
				<ContrastReport theme={shown} pending={preview.isPending} />
			</section>

			<section aria-label={t.look.fonts} className="rounded-lg border border-border bg-card p-4">
				<FontPicker value={theme.font_pairing as PairingKey} onChange={(key) => setTheme({ font_pairing: key })} />
				<p className="mt-2 mb-0 text-[12.5px] text-muted-foreground">{t.look.fontsAbout}</p>
			</section>

			<section aria-label={t.look.logoTitle} className="rounded-lg border border-border bg-card p-4">
				<MediaUploader
					kind="logo"
					label={t.look.logoTitle}
					hint={t.look.logoHint}
					selectedId={theme.logo_asset_id ?? null}
					onSelect={(a: LandingAsset) => setTheme({ logo_asset_id: theme.logo_asset_id === a.id ? null : a.id })}
				/>
			</section>

			<section aria-label={t.look.videoTitle} className="grid gap-4 rounded-lg border border-border bg-card p-4">
				<h2 className="m-0 text-[15px] font-bold">{t.look.videoTitle}</h2>
				<MediaUploader kind="poster" label={t.look.posterTitle} hint={t.look.posterHint} />
				<MediaUploader kind="video" label={t.look.videoFile} hint={t.look.videoHint} />
				<div className="flex flex-wrap items-center gap-3">
					<Button
						type="button"
						variant="brand"
						data-testid="use-video"
						disabled={!video || !poster}
						onClick={showVideo}
					>
						{videoSection ? t.look.useVideoAgain : t.look.useVideo}
					</Button>
					{video && !poster ? (
						<span role="note" data-testid="poster-required" className="text-[12.5px] text-warning">
							{t.look.posterRequired}
						</span>
					) : null}
				</div>
			</section>
		</div>
	);
}
