import { FileUp } from "lucide-react";
import { type DragEvent, type KeyboardEvent, useRef, useState } from "react";
import { formatBytes } from "@/lib/format/bytes";
import { formatNumber } from "@/lib/format/number";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import type { UploadLimits } from "./api";

/**
 * Drag-and-drop or choose files (mockup "Add content"). The zone itself is the button ("Upload
 * files. Drop files here or press Enter to choose."), keyboard operable, and shows the engine's
 * accepted types and size limit from GET /info.
 */
export function UploadDropzone({ limits, onFiles }: { limits: UploadLimits | null; onFiles: (files: File[]) => void }) {
	const input = useRef<HTMLInputElement>(null);
	const [over, setOver] = useState(false);
	const accept = limits ? [...limits.extensions, ...limits.mimeTypes].join(",") : undefined;

	const open = () => input.current?.click();
	const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
		if (e.key === "Enter" || e.key === " ") {
			e.preventDefault();
			open();
		}
	};
	const onDrop = (e: DragEvent<HTMLDivElement>) => {
		e.preventDefault();
		setOver(false);
		const files = [...e.dataTransfer.files];
		if (files.length) onFiles(files);
	};

	return (
		<div className="grid gap-2">
			{/* biome-ignore lint/a11y/useSemanticElements: a drop target that is also the choose-files button */}
			<div
				role="button"
				tabIndex={0}
				aria-label={t.upload.dropLabel}
				data-testid="dropzone"
				onClick={open}
				onKeyDown={onKey}
				onDragOver={(e) => {
					e.preventDefault();
					setOver(true);
				}}
				onDragLeave={() => setOver(false)}
				onDrop={onDrop}
				className={cn(
					"grid cursor-pointer justify-items-center gap-2 rounded-lg border-2 border-dashed border-input bg-card px-6 py-9 text-center transition-colors",
					"hover:border-primary hover:bg-primary-soft focus-visible:border-primary focus-visible:shadow-[0_0_0_3px_var(--primary-soft)] focus-visible:outline-none",
					over && "border-primary bg-primary-soft",
				)}
			>
				<span className="grid size-12 place-items-center rounded-full bg-primary-soft text-primary">
					<FileUp className="size-6" aria-hidden />
				</span>
				<span className="text-[15px] font-bold">{t.upload.dropTitle}</span>
				<span className="text-[13px] text-foreground-2">
					{t.upload.dropOr} <span className="font-semibold text-primary underline">{t.upload.choose}</span>
				</span>
			</div>
			<input
				ref={input}
				type="file"
				multiple
				accept={accept}
				className="hidden"
				data-testid="file-input"
				tabIndex={-1}
				aria-hidden="true"
				onChange={(e) => {
					const files = [...(e.target.files ?? [])];
					e.target.value = "";
					if (files.length) onFiles(files);
				}}
			/>
			<p className="m-0 text-[12.5px] text-muted-foreground">
				{limits ? (
					<>
						{fill(t.upload.types, { types: limits.extensions.join(", ") })}
						{" · "}
						{fill(t.upload.size, { size: formatBytes(limits.maxBytes) })}
						{" · "}
						{fill(t.upload.textLimit, { tokens: formatNumber(limits.maxTokens) })}
					</>
				) : (
					t.upload.loadingLimits
				)}
			</p>
		</div>
	);
}
