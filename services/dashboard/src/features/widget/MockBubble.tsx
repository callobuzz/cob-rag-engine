import { FileText, MessageCircle, RotateCcw, X } from "lucide-react";
import { widgetStrings as t } from "@/lib/strings/widget";
import { cn } from "@/lib/utils";
import { type AppearanceForm, effectiveAppearance } from "./schemas";

/**
 * A static sketch of the widget (D6 "Unsaved appearance preview"): title, greeting, placeholder,
 * colour, corner and the sources chip follow the form as you type. The live iframe shows the
 * saved values. Drawn like widget.js: white text on the brand colour, white panel.
 */
export function MockBubble({ value, className }: { value: AppearanceForm; className?: string }) {
	const a = effectiveAppearance(value);
	const left = a.position === "left";
	return (
		<figure className={cn("m-0 grid gap-2", className)} data-testid="mock-bubble" data-position={a.position}>
			<figcaption className="text-[12.5px] text-muted-foreground">{t.mock.caption}</figcaption>
			<div
				role="img"
				aria-label={`${t.mock.label}: ${a.title}, ${a.primary_color}, ${a.position === "left" ? t.appearance.left : t.appearance.right}`}
				className="rounded-lg border border-dashed border-input bg-muted p-3"
			>
				<div aria-hidden className={cn("flex flex-col gap-2.5", left ? "items-start" : "items-end")}>
					<div className="w-full max-w-[300px] overflow-hidden rounded-[14px] bg-white text-[#111827] shadow-[0_10px_30px_rgba(0,0,0,.18)]">
						<div
							className="flex items-center justify-between gap-2 px-3.5 py-3 text-white"
							style={{ background: a.primary_color }}
							data-testid="mock-head"
						>
							<b className="truncate text-[14px] font-semibold">{a.title}</b>
							<span className="flex gap-1 opacity-90">
								<RotateCcw className="size-4" />
								<X className="size-4" />
							</span>
						</div>
						<div className="grid gap-2 bg-[#f9fafb] p-3">
							<div className="max-w-[85%] rounded-[12px] rounded-bl-[4px] border border-[#e5e7eb] bg-white px-3 py-2 text-[13px] leading-snug break-words">
								{a.greeting}
								{a.show_sources ? (
									<div className="mt-1.5 flex" data-testid="mock-sources">
										<span className="inline-flex items-center gap-1 rounded-full border border-[#e5e7eb] bg-[#f3f4f6] px-2 py-px text-[11px] text-[#374151]">
											<FileText className="size-3" />
											{t.mock.source}
										</span>
									</div>
								) : null}
							</div>
						</div>
						<div className="flex gap-2 border-t border-[#e5e7eb] p-2.5">
							<span className="min-w-0 flex-1 truncate rounded-[9px] border border-[#d1d5db] px-2.5 py-1.5 text-[13px] text-[#6b7280]">
								{a.placeholder}
							</span>
							<span
								className="rounded-[9px] px-3 py-1.5 text-[13px] font-semibold text-white"
								style={{ background: a.primary_color }}
							>
								{t.mock.send}
							</span>
						</div>
					</div>
					<span
						className="grid size-12 place-items-center rounded-full text-white shadow-[0_6px_18px_rgba(0,0,0,.2)]"
						style={{ background: a.primary_color }}
						data-testid="mock-launcher"
					>
						<MessageCircle className="size-5" />
					</span>
				</div>
			</div>
		</figure>
	);
}
