import { Mail } from "lucide-react";
import { LogoMark } from "@/components/brand/LogoMark";
import { Segmented } from "@/components/patterns/Segmented";
import { Panel } from "@/features/kbs/ui";
import { DEFAULT_BRAND_COLOR, safeHttpUrl } from "@/lib/branding/BrandingProvider";
import { foregroundOn } from "@/lib/branding/contrast";
import { brandingStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { type BrandValues, HEX_RE } from "./brand-form";

const t = brandingStrings.preview;
export type PreviewView = "login" | "closed";

/**
 * Mockup `.preview`: login and closed-mode miniatures drawn from the form values as they are
 * typed. The drawing is `aria-hidden`; a text description says the same for screen readers.
 */
export function BrandPreview({
	values,
	view,
	onView,
}: {
	values: BrandValues;
	view: PreviewView;
	onView: (v: PreviewView) => void;
}) {
	const name = values.product_name.trim() || "cob-rag-engine";
	const color = HEX_RE.test(values.primary_color.trim()) ? values.primary_color.trim() : DEFAULT_BRAND_COLOR;
	const fg = foregroundOn(color);
	const logo = safeHttpUrl(values.logo_url.trim());
	const contact = [values.support_email.trim(), values.contact_url.trim()].filter(Boolean).join(" · ");
	const description = fill(t.describe, {
		view: view === "login" ? t.describeLogin : t.describeClosed,
		name,
		logo: logo ? t.logoImage : t.logoDefault,
		color,
		contact: contact || t.noContact,
	});
	return (
		<Panel
			title={t.title}
			sub={t.sub}
			aside={
				<Segmented<PreviewView>
					label={t.label}
					value={view}
					onChange={onView}
					options={[
						{ value: "login", label: t.login },
						{ value: "closed", label: t.closed },
					]}
				/>
			}
		>
			<div className="grid gap-3 p-4">
				<p className="sr-only" data-testid="preview-description">
					{description}
				</p>
				<div aria-hidden data-testid="brand-preview" className="overflow-hidden rounded-lg border border-border">
					<div
						className="grid gap-2.5 p-[18px] text-[#eaf2fb]"
						style={{
							backgroundColor: "#04131a",
							backgroundImage: `radial-gradient(80% 90% at 10% 0%, color-mix(in srgb, ${color} 35%, transparent), transparent 70%)`,
						}}
					>
						<div className="flex items-center gap-2.5">
							{logo ? (
								<img src={logo} alt="" className="size-[30px] rounded-[8px] bg-white object-contain" />
							) : (
								<LogoMark size={30} />
							)}
							<b data-testid="preview-name">{name}</b>
						</div>
						<div className="font-display text-xl leading-tight font-extrabold">{t.headline}</div>
					</div>
					{view === "login" ? (
						<div className="grid gap-2.5 bg-background p-[18px]">
							<b className="text-base">{fill(t.logIn, { name })}</b>
							<div className="flex h-[34px] items-center rounded-sm border border-input bg-muted px-2.5 text-[12.5px] text-muted-foreground">
								{t.email}
							</div>
							<div className="flex h-[34px] items-center rounded-sm border border-input bg-muted px-2.5 text-[12.5px] text-muted-foreground">
								••••••••••
							</div>
							<div
								className="grid h-9 place-items-center rounded-sm text-[13px] font-semibold"
								style={{ background: color, color: fg }}
							>
								{t.button}
							</div>
							<small className="text-center text-muted-foreground">
								{t.newHere} <span className="font-semibold text-foreground underline">{t.create}</span>
							</small>
						</div>
					) : (
						<div className="grid gap-2.5 bg-background p-[18px]">
							<b className="text-base">{t.closedTitle}</b>
							<p
								data-testid="preview-contact"
								className="m-0 text-[13px] break-words whitespace-pre-line text-foreground-2"
							>
								{values.contact_text}
							</p>
							<div
								className="grid h-9 place-items-center rounded-sm text-[13px] font-semibold"
								style={{ background: color, color: fg }}
							>
								{t.contactButton}
							</div>
							{contact ? <small className="break-all text-muted-foreground">{contact}</small> : null}
						</div>
					)}
				</div>
				<p className="m-0 flex items-start gap-2 text-[13px] text-foreground-2">
					<Mail className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
					<span>{t.emails}</span>
				</p>
			</div>
		</Panel>
	);
}
