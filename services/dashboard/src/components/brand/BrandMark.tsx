import { useBranding } from "@/lib/branding/BrandingProvider";
import { cn } from "@/lib/utils";
import { LogoMark } from "./LogoMark";

/**
 * White-label brand mark. With an operator logo_url it is that logo (a static <img>); otherwise the
 * cob-rag-engine chrome logo. `standalone` marks a mark with no product name beside it: the operator
 * logo then carries the product name as its alt text and the cob logo as its title.
 */
export function BrandMark({
	size = 28,
	standalone = false,
	className,
}: {
	size?: number;
	standalone?: boolean;
	className?: string;
}) {
	const { logoUrl, productName } = useBranding();
	if (logoUrl) {
		return (
			<img
				src={logoUrl}
				alt={standalone ? productName : ""}
				width={size}
				height={size}
				data-testid="operator-logo"
				className={cn("shrink-0 rounded-[8px] object-contain", className)}
				style={{ width: size, height: size }}
				referrerPolicy="no-referrer"
			/>
		);
	}
	return <LogoMark size={size} title={standalone ? productName : undefined} className={className} />;
}
