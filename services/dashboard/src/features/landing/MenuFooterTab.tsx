import type { ReactNode } from "react";
import { landingStrings as t } from "@/lib/strings/landing";
import type { LandingContent } from "./api";
import { useDraft } from "./DraftProvider";
import type { LText } from "./draft";
import { Fields, type Rec } from "./fields";
import { LocalizedField } from "./LocalizedField";
import { ANNOUNCEMENT_FIELDS, FOOTER_FIELDS, NAV_FIELDS, PAGE_SEO } from "./pageSpecs";

function Card({ title, about, children }: { title: string; about: string; children: ReactNode }) {
	return (
		<section aria-label={title} className="grid gap-4 rounded-lg border border-border bg-card p-4">
			<div className="grid gap-0.5">
				<h2 className="m-0 text-[15px] font-bold">{title}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">{about}</p>
			</div>
			{children}
		</section>
	);
}

/** The menu, the footer, the bar at the top and the texts search engines and chat apps show. */
export function MenuFooterTab() {
	const draft = useDraft();
	const { nav, footer, announcement, seo } = draft.content;
	const set = <K extends "nav" | "footer" | "announcement">(key: K, next: Rec) =>
		draft.update((d) => {
			d[key] = next as unknown as LandingContent[K];
		});
	const seoRec = seo as unknown as Record<string, Record<string, LText> | LText>;

	return (
		<div className="grid gap-4" data-testid="menu-tab">
			<Card title={t.menu.nav} about={t.menu.navAbout}>
				<Fields
					specs={NAV_FIELDS}
					value={(nav ?? {}) as unknown as Rec}
					onChange={(n) => set("nav", n)}
					path="nav"
					limitBase="nav"
				/>
			</Card>
			<Card title={t.menu.footer} about={t.menu.footerAbout}>
				<Fields
					specs={FOOTER_FIELDS}
					value={(footer ?? {}) as unknown as Rec}
					onChange={(n) => set("footer", n)}
					path="footer"
					limitBase="footer"
				/>
			</Card>
			<Card title={t.menu.announcement} about={t.menu.announcementAbout}>
				<Fields
					specs={ANNOUNCEMENT_FIELDS}
					value={(announcement ?? {}) as unknown as Rec}
					onChange={(n) => set("announcement", n)}
					path="announcement"
					limitBase="announcement"
				/>
			</Card>
			<Card title={t.menu.seo} about={t.menu.seoAbout}>
				<div className="grid gap-5">
					{PAGE_SEO.map((page) => {
						const entry = (seoRec[page.key] ?? {}) as Record<string, LText>;
						const write = (field: "title" | "description", v: LText | null) =>
							draft.update((d) => {
								const target = d.seo[page.key] as unknown as Record<string, LText>;
								target[field] = v ?? { en: "" };
							});
						return (
							<fieldset key={page.key} className="grid gap-3 rounded-sm border border-border p-3">
								<legend className="px-1 text-[13px] font-semibold">{page.label}</legend>
								<LocalizedField
									path={`seo.${page.key}.title`}
									label={t.menu.seoTitle}
									value={entry.title}
									onChange={(v) => write("title", v)}
									limitKey={`seo.${page.key}.title`}
								/>
								<LocalizedField
									path={`seo.${page.key}.description`}
									label={t.menu.seoDescription}
									value={entry.description}
									long
									onChange={(v) => write("description", v)}
									limitKey={`seo.${page.key}.description`}
								/>
							</fieldset>
						);
					})}
					<LocalizedField
						path="seo.og_headline"
						label={t.menu.ogHeadline}
						value={seoRec.og_headline as LText}
						hint={t.menu.ogHint}
						onChange={(v) =>
							draft.update((d) => {
								d.seo.og_headline = v ?? { en: "" };
							})
						}
						limitKey="seo.og_headline"
					/>
				</div>
			</Card>
		</div>
	);
}
