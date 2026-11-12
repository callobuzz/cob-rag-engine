import { Check, ShieldCheck, Users } from "lucide-react";
import { AnimatedLogo } from "@/components/brand/AnimatedLogo";
import { BrandBackdrop } from "@/components/brand/BrandBackdrop";
import { BrandMark } from "@/components/brand/BrandMark";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { en } from "@/lib/strings/en";

const POINT_ICONS = [Check, ShieldCheck, Users];

/** Mockup `.signin-side.auth-side`: brand, headline, value points (hidden < 861 px), footer. */
export function BrandSide() {
	const { productName, supportEmail, logoUrl } = useBranding();
	return (
		<aside className="auth-side relative flex flex-col gap-8 overflow-hidden px-5 py-7 min-[861px]:p-12">
			<div className="flex items-center gap-2.5 font-display text-base font-extrabold tracking-[-.02em] text-[#eaf2fb]">
				<BrandMark size={30} />
				<span>{productName}</span>
			</div>
			<div>
				<p className="m-0 max-w-[520px] font-display text-[clamp(30px,3.4vw,44px)] leading-[1.06] font-extrabold tracking-[-.03em]">
					{en.brandSide.headline}{" "}
					<em className="text-[color:color-mix(in_srgb,var(--brand)_45%,#ffffff)] not-italic">
						{en.brandSide.headlineEm}
					</em>
				</p>
				<p className="mt-4 max-w-[460px] text-[15px] text-[#a9bbcf]">{en.brandSide.lead}</p>
			</div>
			{/* The free centre of the panel: the animated cob-rag-engine logo on the default brand, a quiet
			    brand-tinted backdrop when the operator supplies their own logo. Not shown on phones. */}
			<div className="hidden min-h-[220px] flex-1 place-items-center [container-type:size] min-[861px]:grid">
				{logoUrl ? <BrandBackdrop /> : <AnimatedLogo className="w-[min(100cqw,100cqh)]" />}
			</div>
			<ul className="m-0 hidden list-none gap-3 p-0 min-[861px]:grid">
				{en.brandSide.points.map((p, i) => {
					const Icon = POINT_ICONS[i] ?? Check;
					return (
						<li key={p.b} className="flex items-start gap-3 text-sm text-[#c9d7e6]">
							<Icon
								className="mt-0.5 size-[18px] shrink-0 text-[color:color-mix(in_srgb,var(--brand)_45%,#ffffff)]"
								aria-hidden
							/>
							<span>
								<b>{p.b}</b> {p.t}
							</span>
						</li>
					);
				})}
			</ul>
			<p className="m-0 mt-auto text-[12.5px] text-[#8fa3b8] min-[861px]:mt-0">
				{productName}
				{supportEmail ? ` · ${supportEmail}` : ""}
			</p>
		</aside>
	);
}
