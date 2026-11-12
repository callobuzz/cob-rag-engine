import { useId } from "react";
import { cn } from "@/lib/utils";
import { LogoArt } from "./LogoArt";
import "./logo.css";

/** Unique id prefix per logo instance, so the gradients of several logos on one page never collide. */
export function useLogoIds(): string {
	return `lm${useId().replace(/[^a-zA-Z0-9]/g, "")}-`;
}

/**
 * The static cob-rag-engine chrome logo (docs/assets/logo-mark.svg). Decorative by default: put the
 * product name next to it, or pass `title` when it stands alone.
 */
export function LogoMark({ size = 28, title, className }: { size?: number; title?: string; className?: string }) {
	const p = useLogoIds();
	return (
		<svg
			data-testid="cob-logo-mark"
			className={cn("lm lm-svg shrink-0", className)}
			style={{ width: size, height: size }}
			viewBox="38 25 470 470"
			role={title ? "img" : undefined}
			aria-label={title}
			aria-hidden={title ? undefined : true}
			focusable="false"
		>
			<LogoArt p={p} />
		</svg>
	);
}
