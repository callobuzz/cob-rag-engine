import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { z } from "zod";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { ReasonBanner } from "@/components/auth/ReasonBanner";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { ApiError, toApiError } from "@/lib/api/errors";
import { probeKey, storeKeySignIn } from "@/lib/auth/actions";
import { redirectIfSignedIn, safeNext } from "@/lib/auth/guards";
import { CorpusKeyRefused, type Principal } from "@/lib/auth/principal";
import { setAuthState } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { getConfig, isHttpUrl } from "@/lib/config";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { loginSearchSchema } from "@/lib/search";
import { en } from "@/lib/strings/en";

export const Route = createFileRoute("/login/key")({
	validateSearch: loginSearchSchema,
	beforeLoad: ({ context, search }) => redirectIfSignedIn({ context, search }),
	component: KeySignInPage,
});

/**
 * API-key sign-in (developers and the admin-key recovery path). Nothing is stored until whoami
 * succeeds; ck_ keys are refused; an admin key with "Remember" needs a second, informed click.
 */
const schema = z.object({
	key: z.string().trim().min(1, "Enter an API key."),
	url: z.string().refine((u) => isHttpUrl(u.trim()), "Use an http:// or https:// address."),
	remember: z.boolean(),
});
type Values = z.infer<typeof schema>;

function KeySignInPage() {
	const { next, reason } = Route.useSearch();
	const navigate = useNavigate();
	const { accountsEnabled } = useBranding();
	const ids = { key: useId(), url: useId(), remember: useId(), alert: useId() };
	const form = useAppForm<Values>({
		resolver: zodResolver(schema),
		defaultValues: { key: "", url: getConfig().ragUrl, remember: false },
	});
	const errs = form.formState.errors;
	const remember = form.watch("remember");
	const ragUrl = form.watch("url");
	const [refused, setRefused] = useState(false);
	const [pendingAdmin, setPendingAdmin] = useState<Principal | null>(null);

	// Editing anything starts over: the refusal and the "are you sure" step belong to the old input.
	useEffect(() => {
		const sub = form.watch((_v, info) => {
			if (info.type !== "change") return;
			setRefused(false);
			if (info.name === "key" || info.name === "url") setPendingAdmin(null);
		});
		return () => sub.unsubscribe();
	}, [form]);

	const submit = useAppSubmit(form, async (v) => {
		setRefused(false);
		const key = v.key.trim();
		const url = v.url.trim();
		const finish = async (p: Principal) => {
			storeKeySignIn(key, url, v.remember, p);
			await navigate({ href: safeNext(next) ?? "/" });
		};
		// Second click after the admin warning: remember it (the person saw the warning).
		if (pendingAdmin && v.remember) {
			await finish(pendingAdmin);
			return;
		}
		try {
			const p = await probeKey(key, url);
			if (p.keyKind === "admin" && v.remember) {
				setPendingAdmin(p);
				return;
			}
			await finish(p);
		} catch (err) {
			if (err instanceof CorpusKeyRefused) {
				setRefused(true);
				return;
			}
			// Engines before A3 send 401 without a code: any 401 here means the key was refused.
			const raw = toApiError(err);
			const apiErr =
				raw.status === 401 && !raw.code
					? new ApiError({ status: 401, code: "invalid_api_key", detail: raw.detail, retryAfterS: raw.retryAfterS })
					: raw;
			if (apiErr.code === "workspace_not_active") {
				// A pending or suspended tenant's tk_ key: show the status page, store nothing.
				setAuthState({ lastWorkspaceStatus: apiErr.extra.status === "suspended" ? "suspended" : "pending" });
				await navigate({ to: "/status" });
				return;
			}
			throw apiErr;
		}
	});
	const { error } = submit;

	return (
		<AuthLayout title={en.key.title}>
			<form className="grid gap-4" noValidate onSubmit={(e) => void submit.onSubmit(e)}>
				<h1 className="text-2xl font-bold">{en.key.title}</h1>
				<p className="-mt-2 mb-0 text-foreground-2">{en.key.lead}</p>
				<ReasonBanner reason={reason} />
				{refused ? <FormAlert id={ids.alert}>{en.key.corpusRefused}</FormAlert> : null}
				{error ? <ErrorAlert id={ids.alert} error={error} context={{ ragUrl }} /> : null}
				<Field id={ids.key} label={en.key.label} hint={en.key.help} error={errs.key?.message}>
					<Input
						id={ids.key}
						type="password"
						className="font-mono"
						autoComplete="off"
						spellCheck={false}
						placeholder={en.key.placeholder}
						aria-invalid={!!errs.key || error?.status === 401 || refused || undefined}
						aria-describedby={describedBy(ids.key, { hint: true, error: !!errs.key })}
						{...form.register("key")}
					/>
				</Field>
				<details className="group" open={errs.url ? true : undefined}>
					<summary className="flex cursor-pointer list-none items-center gap-1.5 text-[13px] font-semibold text-foreground-2 [&::-webkit-details-marker]:hidden">
						<ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden />
						{en.key.advanced}
					</summary>
					<div className="mt-2.5">
						<Field id={ids.url} label={en.key.engineUrl} hint={en.key.engineHelp} error={errs.url?.message}>
							<Input
								id={ids.url}
								className="font-mono"
								aria-invalid={!!errs.url || undefined}
								aria-describedby={describedBy(ids.url, { hint: true, error: !!errs.url })}
								{...form.register("url")}
							/>
						</Field>
					</div>
				</details>
				<div className="flex items-start gap-2.5 text-[13.5px]">
					<Checkbox
						id={ids.remember}
						className="mt-[3px]"
						checked={remember}
						onCheckedChange={(c) => {
							form.setValue("remember", c === true);
							submit.clearError();
							setRefused(false);
						}}
						aria-describedby={`${ids.remember}-hint`}
					/>
					<label htmlFor={ids.remember}>
						<b>{en.key.remember}</b>
						<br />
						<small id={`${ids.remember}-hint`} className="text-muted-foreground">
							{en.key.rememberHint}
						</small>
					</label>
				</div>
				{pendingAdmin && remember ? <FormAlert tone="warn">{en.key.adminWarn}</FormAlert> : null}
				<SubmitButton variant="default" size="lg" pending={submit.isSubmitting} locked={submit.locked}>
					{pendingAdmin && remember ? "Remember and sign in" : en.key.submit}
				</SubmitButton>
				{accountsEnabled ? (
					<p className="m-0 text-center text-[13.5px]">
						<Link to="/login" search={next ? { next } : {}} className="font-semibold text-brand-text">
							{en.key.emailInstead}
						</Link>
					</p>
				) : null}
			</form>
		</AuthLayout>
	);
}
