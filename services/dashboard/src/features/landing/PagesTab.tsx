import type { ReactNode } from "react";
import { landingStrings as t } from "@/lib/strings/landing";
import type { LandingContent } from "./api";
import { useDraft } from "./DraftProvider";
import type { LText } from "./draft";
import { Fields, type Rec } from "./fields";
import { LocalizedField } from "./LocalizedField";
import { ABOUT_FIELDS, CONTACT_FIELDS, FEATURES_FIELDS, TOPICS } from "./pageSpecs";

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

/** The Features, About and Contact pages. */
export function PagesTab() {
	const draft = useDraft();
	const { features, about, contact } = draft.content;
	const topics = (contact.topics ?? {}) as Record<string, LText>;

	const set = <K extends "features" | "about" | "contact">(key: K, next: Rec) =>
		draft.update((d) => {
			d[key] = next as unknown as LandingContent[K];
		});

	return (
		<div className="grid gap-4" data-testid="pages-tab">
			<Card title={t.pages.features} about={t.pages.featuresAbout}>
				<Fields
					specs={FEATURES_FIELDS}
					value={features as unknown as Rec}
					onChange={(n) => set("features", n)}
					path="features"
					limitBase="features"
				/>
			</Card>
			<Card title={t.pages.about} about={t.pages.aboutAbout}>
				<Fields
					specs={ABOUT_FIELDS}
					value={about as unknown as Rec}
					onChange={(n) => set("about", n)}
					path="about"
					limitBase="about"
				/>
			</Card>
			<Card title={t.pages.contact} about={t.pages.contactAbout}>
				<Fields
					specs={CONTACT_FIELDS}
					value={contact as unknown as Rec}
					onChange={(n) => set("contact", n)}
					path="contact"
					limitBase="contact"
				/>
				<fieldset className="grid gap-3 rounded-sm border border-border p-3">
					<legend className="px-1 text-[13px] font-semibold">{t.pages.topics}</legend>
					<p className="m-0 text-[12.5px] text-muted-foreground">{t.pages.topicsAbout}</p>
					<div className="grid gap-3 min-[861px]:grid-cols-2">
						{TOPICS.map((topic) => (
							<LocalizedField
								key={topic.key}
								path={`contact.topics.${topic.key}`}
								label={topic.label}
								value={topics[topic.key]}
								limitKey="contact.topics"
								onChange={(v) =>
									draft.update((d) => {
										const next = { ...(d.contact.topics ?? {}) } as Record<string, LText>;
										if (v == null) delete next[topic.key];
										else next[topic.key] = v;
										d.contact.topics = next as typeof d.contact.topics;
									})
								}
							/>
						))}
					</div>
				</fieldset>
			</Card>
		</div>
	);
}
