import { Trash2, Upload } from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatBytes } from "@/lib/format/bytes";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { type LandingAsset, useDeleteAsset, useUploadAsset } from "./api";
import { useDraft } from "./DraftProvider";

const ACCEPT: Record<LandingAsset["kind"], string[]> = {
	logo: ["image/png", "image/jpeg", "image/webp"],
	poster: ["image/png", "image/jpeg", "image/webp"],
	video: ["video/mp4", "video/webm"],
};

/**
 * Upload a logo, a poster picture or a video. The type and size are checked here first (the
 * largest size comes from the engine), the upload shows its progress, and a file the website uses
 * cannot be deleted (the engine says so in plain words).
 */
export function MediaUploader({
	kind,
	label,
	hint,
	selectedId,
	onSelect,
	disabled = false,
	children,
}: {
	kind: LandingAsset["kind"];
	label: string;
	hint?: string;
	selectedId?: string | null;
	onSelect?: (asset: LandingAsset) => void;
	disabled?: boolean;
	children?: (asset: LandingAsset) => React.ReactNode;
}) {
	const draft = useDraft();
	const upload = useUploadAsset();
	const remove = useDeleteAsset();
	const inputId = useId();
	const input = useRef<HTMLInputElement>(null);
	const [progress, setProgress] = useState<number | null>(null);
	const [error, setError] = useState<string | null>(null);
	const maxMb = draft.limits[kind === "video" ? "upload.video_mb" : "upload.image_mb"];
	const assets = draft.state.assets.filter((a) => a.kind === kind);

	const pick = async (file: File | undefined) => {
		if (!file) return;
		setError(null);
		if (!ACCEPT[kind].includes(file.type)) return setError(t.media.wrongType[kind === "video" ? "video" : "image"]);
		if (maxMb != null && file.size > maxMb * 1024 * 1024) {
			return setError(fill(t.media.tooLarge, { mb: maxMb }));
		}
		try {
			setProgress(0);
			const asset = await upload.mutateAsync({ kind, file, onProgress: setProgress });
			onSelect?.(asset);
			toast.success(t.media.uploaded);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		} finally {
			setProgress(null);
			if (input.current) input.current.value = "";
		}
	};

	const del = async (asset: LandingAsset) => {
		setError(null);
		try {
			await remove.mutateAsync(asset.id);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};

	return (
		<div className="grid gap-2" data-testid={`media-${kind}`}>
			<div className="flex flex-wrap items-center justify-between gap-2">
				<div className="grid">
					<span className="text-[13px] font-semibold">{label}</span>
					{hint ? <small className="text-[12.5px] text-muted-foreground">{hint}</small> : null}
				</div>
				<div>
					<input
						ref={input}
						id={inputId}
						type="file"
						className="peer sr-only"
						accept={ACCEPT[kind].join(",")}
						disabled={disabled || progress != null}
						onChange={(e) => void pick(e.target.files?.[0])}
					/>
					<Button type="button" variant="outline" size="sm" disabled={disabled || progress != null} asChild>
						<label
							htmlFor={inputId}
							className="cursor-pointer peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2"
						>
							<Upload aria-hidden />
							{fill(t.media.upload, { kind: t.media.kinds[kind].toLowerCase() })}
						</label>
					</Button>
				</div>
			</div>
			{progress != null ? (
				<progress
					className="h-1.5 w-full"
					max={1}
					value={progress}
					aria-label={fill(t.media.uploading, { kind: t.media.kinds[kind].toLowerCase() })}
				/>
			) : null}
			{error ? (
				<p role="alert" className="m-0 text-[13px] text-destructive">
					{error}
				</p>
			) : null}
			{assets.length === 0 ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">{t.media.noneOfKind}</p>
			) : (
				<ul className="m-0 grid list-none gap-1.5 p-0">
					{assets.map((a) => (
						<li
							key={a.id}
							data-testid={`asset-${a.id}`}
							className="flex flex-wrap items-center gap-3 rounded-sm border border-border bg-card p-2"
						>
							{kind !== "video" ? (
								<img src={a.url} alt="" className="size-10 rounded-sm object-contain" width={40} height={40} />
							) : null}
							<span className="grid min-w-0 flex-1 text-[13px]">
								<span className="truncate font-medium">
									{t.media.kinds[kind]} …{a.id.slice(-4)}
								</span>
								<span className="text-[12px] text-muted-foreground">
									{formatBytes(a.bytes)}
									{a.width && a.height ? ` · ${a.width}×${a.height}` : ""}
								</span>
							</span>
							{children?.(a)}
							{onSelect ? (
								<Button
									type="button"
									size="sm"
									variant={selectedId === a.id ? "brand" : "outline"}
									aria-pressed={selectedId === a.id}
									disabled={disabled}
									onClick={() => onSelect(a)}
								>
									{selectedId === a.id ? t.media.inUse : t.media.use}
								</Button>
							) : null}
							<Button
								type="button"
								size="icon-sm"
								variant="ghost"
								aria-label={fill(t.media.remove, { kind: t.media.kinds[kind].toLowerCase(), id: a.id.slice(-4) })}
								onClick={() => void del(a)}
							>
								<Trash2 aria-hidden />
							</Button>
						</li>
					))}
				</ul>
			)}
		</div>
	);
}
