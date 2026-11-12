import { TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { BrandMark } from "@/components/brand/BrandMark";
import { PoweredBy } from "@/components/shell/PoweredBy";
import { PublicCob } from "@/features/cob-public/PublicCob";
import { LegalLinks } from "@/features/legal/LegalLinks"; // P3 legal
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";
import { en } from "@/lib/strings/en";
import { BrandSide } from "./BrandSide";

/**
 * Public pages: branded side panel + form column (mockup `.signin`). Two columns from 861 px,
 * one below. Form column ≤ 420 px with the permanent PoweredBy line under it.
 */
export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
	const { failed } = useBranding();
	useDocumentTitle(title);
	return (
		<div className="grid min-h-dvh grid-cols-1 min-[861px]:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
			<a
				href="#main"
				className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-sm focus:bg-card focus:px-3 focus:py-2"
			>
				{en.common.skipToContent}
			</a>
			<BrandSide />
			<main id="main" className="grid place-items-center bg-background px-4 py-10 min-[861px]:px-6">
				<div className="grid w-full max-w-[420px] gap-[18px]">
					{failed ? (
						<p
							role="status"
							className="m-0 flex items-start gap-2 rounded-sm bg-warning-soft p-2.5 text-[13px] text-warning"
						>
							<TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
							{en.common.brandingFailed}
						</p>
					) : null}
					<div className="hidden min-[861px]:block" data-testid="form-brand-mark">
						<BrandMark size={44} standalone />
					</div>
					{children}
					{/* P3 legal */}
					<LegalLinks tone="auth" />
					{/* /P3 legal */}
					<PoweredBy tone="auth" />
				</div>
			</main>
			<PublicCob />
		</div>
	);
}
