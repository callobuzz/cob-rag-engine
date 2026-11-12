import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useId, useMemo } from "react";
import { Controller } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { type Corpus, corporaSwitcherQuery } from "@/lib/api/queries";
import type { ScrapeRequest } from "@/lib/api/scraper-types";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { crawlerStrings as t } from "@/lib/strings/crawler";
import { en, fill } from "@/lib/strings/en";
import { useStartCrawl } from "./api";
import { crawlerMessage } from "./CrawlerError";
import { isCrawlableUrl } from "./statuses";

export const MAX_SEED_URLS = 100;

export function splitUrls(text: string): string[] {
	return [
		...new Set(
			text
				.split(/[\r\n]+/)
				.map((s) => s.trim())
				.filter(Boolean),
		),
	];
}

/** Sitemap or seed URLs (at least one, http(s) only), a knowledge base, 1–1000 pages. */
export const newCrawlSchema = z
	.object({
		sitemap_url: z.string().trim(),
		urls: z.string(),
		kb: z.string().min(1, t.create.kbRequired),
		max_pages: z.string().trim(),
		review_mode: z.boolean(),
	})
	.superRefine((v, ctx) => {
		const seeds = splitUrls(v.urls);
		if (!v.sitemap_url && seeds.length === 0) {
			ctx.addIssue({ code: "custom", path: ["urls"], message: t.create.needOne });
		}
		if (v.sitemap_url && !isCrawlableUrl(v.sitemap_url)) {
			ctx.addIssue({ code: "custom", path: ["sitemap_url"], message: t.create.badSitemap });
		}
		const bad = seeds.find((u) => !isCrawlableUrl(u));
		if (bad) ctx.addIssue({ code: "custom", path: ["urls"], message: fill(t.create.badUrl, { url: bad }) });
		else if (seeds.length > MAX_SEED_URLS) {
			ctx.addIssue({ code: "custom", path: ["urls"], message: fill(t.create.tooManyUrls, { max: MAX_SEED_URLS }) });
		}
		const n = Number(v.max_pages);
		if (!/^\d+$/.test(v.max_pages) || !Number.isInteger(n) || n < 1 || n > 1000) {
			ctx.addIssue({ code: "custom", path: ["max_pages"], message: t.create.maxPagesRange });
		}
	});
export type NewCrawlForm = z.infer<typeof newCrawlSchema>;

export function toScrapeRequest(v: NewCrawlForm): ScrapeRequest {
	const seeds = splitUrls(v.urls);
	return {
		...(v.sitemap_url ? { sitemap_url: v.sitemap_url } : {}),
		...(seeds.length ? { urls: seeds } : {}),
		target_corpus_id: v.kb,
		review_mode: v.review_mode,
		max_pages: Number(v.max_pages),
	};
}

/** New crawl (D8): sitemap and/or seeds, target KB, page limit, review mode (locked on for sign-ins). */
export function NewCrawlDialog({
	open,
	onOpenChange,
	defaultKb,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	defaultKb: string | null;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-xl">
				{open ? <NewCrawlForm defaultKb={defaultKb} onDone={() => onOpenChange(false)} /> : null}
			</DialogContent>
		</Dialog>
	);
}

function NewCrawlForm({ defaultKb, onDone }: { defaultKb: string | null; onDone: () => void }) {
	const principal = usePrincipal();
	const session = principal?.auth === "session";
	const isAdmin = principal?.view === "admin";
	const { supportEmail } = useBranding();
	const navigate = useNavigate();
	const kbs = useQuery(corporaSwitcherQuery);
	const start = useStartCrawl();
	const ids = { sitemap: useId(), urls: useId(), kb: useId(), max: useId(), review: useId(), note: useId() };

	const groups = useMemo(() => {
		const map = new Map<string, Corpus[]>();
		for (const k of [...(kbs.data?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name, "en-IN"))) {
			map.set(k.tenant_name, [...(map.get(k.tenant_name) ?? []), k]);
		}
		return [...map.entries()].sort(([a], [b]) => a.localeCompare(b, "en-IN"));
	}, [kbs.data]);

	const form = useAppForm<NewCrawlForm>({
		resolver: zodResolver(newCrawlSchema),
		defaultValues: { sitemap_url: "", urls: "", kb: defaultKb ?? "", max_pages: "50", review_mode: true },
	});
	const errs = form.formState.errors;
	useEffect(() => {
		form.setFocus("sitemap_url");
	}, [form]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const res = await start.mutateAsync(toScrapeRequest({ ...v, review_mode: session ? true : v.review_mode }));
			toast.success(t.create.started);
			onDone();
			void navigate({ to: "/crawler/$jobId", params: { jobId: res.job_id } });
		},
		{
			fieldName: (path) =>
				path.startsWith("sitemap")
					? "sitemap_url"
					: path.startsWith("max_pages")
						? "max_pages"
						: path.startsWith("target_corpus")
							? "kb"
							: "urls",
			onError: (err) => {
				// A refused address: "<url>: <reason>" on the field that holds that address.
				const blocked = err.status === 422 && !err.fieldErrors.length ? /^(\S+): (.+)$/.exec(err.detail) : null;
				if (blocked?.[1] && isCrawlableUrl(blocked[1])) {
					const field = blocked[1] === form.getValues("sitemap_url") ? "sitemap_url" : "urls";
					form.setError(field, { type: "server", message: err.detail });
					return true;
				}
				if (err.status === 404 && !err.code) {
					form.setError("kb", { type: "server", message: crawlerMessage(err).title });
					return true;
				}
				return false;
			},
		},
	);
	const alertMsg = submit.error ? crawlerMessage(submit.error, supportEmail) : null;

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{t.create.title}</DialogTitle>
				<DialogDescription>{t.create.lead}</DialogDescription>
			</DialogHeader>
			{alertMsg ? (
				<FormAlert>
					<b className="font-semibold">{alertMsg.title}</b>
					{alertMsg.body ? <span className="block">{alertMsg.body}</span> : null}
				</FormAlert>
			) : null}
			<Field id={ids.sitemap} label={t.create.sitemap} hint={t.create.sitemapHint} error={errs.sitemap_url?.message}>
				<Input
					id={ids.sitemap}
					type="url"
					inputMode="url"
					autoComplete="off"
					className="font-mono"
					placeholder={t.create.sitemapPlaceholder}
					aria-invalid={!!errs.sitemap_url}
					aria-describedby={describedBy(ids.sitemap, { hint: true, error: !!errs.sitemap_url })}
					{...form.register("sitemap_url")}
				/>
			</Field>
			<Field id={ids.urls} label={t.create.urls} hint={t.create.urlsHint} error={errs.urls?.message}>
				<Textarea
					id={ids.urls}
					rows={4}
					autoComplete="off"
					spellCheck={false}
					className="font-mono"
					placeholder={t.create.urlsPlaceholder}
					aria-invalid={!!errs.urls}
					aria-describedby={describedBy(ids.urls, { hint: true, error: !!errs.urls })}
					{...form.register("urls")}
				/>
			</Field>
			<div className="grid gap-4 min-[600px]:grid-cols-[minmax(0,1fr)_160px]">
				<Field id={ids.kb} label={t.create.kb} hint={t.create.kbHint} error={errs.kb?.message}>
					<NativeSelect
						id={ids.kb}
						aria-required="true"
						aria-invalid={!!errs.kb}
						aria-describedby={describedBy(ids.kb, { hint: true, error: !!errs.kb })}
						{...form.register("kb")}
					>
						<option value="">{t.create.kbPick}</option>
						{isAdmin
							? groups.map(([tenant, items]) => (
									<optgroup key={tenant} label={tenant}>
										{items.map((k) => (
											<option key={k.id} value={k.id}>
												{k.name}
											</option>
										))}
									</optgroup>
								))
							: groups
									.flatMap(([, items]) => items)
									.map((k) => (
										<option key={k.id} value={k.id}>
											{k.name}
										</option>
									))}
					</NativeSelect>
				</Field>
				<Field id={ids.max} label={t.create.maxPages} hint={t.create.maxPagesHint} error={errs.max_pages?.message}>
					<Input
						id={ids.max}
						type="number"
						inputMode="numeric"
						min={1}
						max={1000}
						step={1}
						aria-invalid={!!errs.max_pages}
						aria-describedby={describedBy(ids.max, { hint: true, error: !!errs.max_pages })}
						{...form.register("max_pages")}
					/>
				</Field>
			</div>
			<div className="flex items-start gap-3 rounded-md border border-border p-3">
				<Controller
					control={form.control}
					name="review_mode"
					render={({ field }) => (
						<Switch
							id={ids.review}
							className="mt-0.5"
							checked={session ? true : field.value}
							disabled={session}
							onCheckedChange={field.onChange}
							aria-describedby={`${ids.review}-hint`}
						/>
					)}
				/>
				<div className="grid gap-0.5">
					<label htmlFor={ids.review} className="text-[13px] font-semibold">
						{t.create.review}
					</label>
					<small id={`${ids.review}-hint`} className="text-[12.5px] text-muted-foreground">
						{session ? t.create.reviewLocked : t.create.reviewHint}
					</small>
				</div>
			</div>
			<FormAlert tone="info" id={ids.note}>
				{t.create.privateNote}
			</FormAlert>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.create.submitting}
				>
					{t.create.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
