import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import { useId, useRef, useState } from "react";
import { z } from "zod";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { DemoLogins } from "@/components/auth/DemoLogins"; // L5a demo
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { PasswordField } from "@/components/auth/PasswordField";
import { ReasonBanner } from "@/components/auth/ReasonBanner";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { BackToWebsite } from "@/features/landing/BackToWebsite"; // LP4 landing
import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import { loginWithPassword } from "@/lib/auth/actions";
import { redirectIfSignedIn, safeNext } from "@/lib/auth/guards";
import { isInactive } from "@/lib/auth/principal";
import { readPendingEmail, writePendingEmail } from "@/lib/auth/storage";
import { brandingFromCache, useBranding } from "@/lib/branding/BrandingProvider";
import { useDemoInfo } from "@/lib/branding/demo"; // L5a demo
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { emailSchema } from "@/lib/forms/email-rule";
import { loginSearchSchema } from "@/lib/search";
import { en, fill } from "@/lib/strings/en";

export const Route = createFileRoute("/login/")({
	validateSearch: loginSearchSchema,
	beforeLoad: async ({ context, search }) => {
		await redirectIfSignedIn({ context, search });
		// Accounts off on this engine (or an engine before A3): the key sign-in is the way in.
		if (!brandingFromCache(context.queryClient).accountsEnabled) {
			throw redirect({ to: "/login/key", search: search.reason ? { reason: search.reason } : {} });
		}
	},
	component: LoginPage,
});

// Shape only: the reserved-name rule is for creating addresses, not for finding an existing account.
const buildSchema = () =>
	z.object({
		email: emailSchema(true),
		password: z.string().min(1, "Enter your password."),
		keep: z.boolean(),
	});
type FormValues = z.infer<ReturnType<typeof buildSchema>>;

function LoginPage() {
	const { next, reason } = Route.useSearch();
	const navigate = useNavigate();
	const branding = useBranding();
	const [schema] = useState(() => buildSchema());
	const ids = { email: useId(), password: useId(), keep: useId(), alert: useId() };
	const form = useAppForm<FormValues>({
		resolver: zodResolver(schema),
		defaultValues: { email: readPendingEmail(), password: "", keep: false },
	});
	const keep = form.watch("keep");
	const errs = form.formState.errors;
	// L5a demo: the demo's sign-ins, shown to every visitor on purpose
	const demo = useDemoInfo();
	const formRef = useRef<HTMLFormElement>(null);
	// /L5a demo

	const submit = useAppSubmit(form, async (v) => {
		try {
			const p = await loginWithPassword(v.email, v.password, v.keep);
			if (isInactive(p)) {
				await navigate({ to: "/status" });
				return;
			}
			await navigate({ href: safeNext(next) ?? "/" });
		} catch (e) {
			const err = toApiError(e);
			if (err.code === "email_not_verified") {
				writePendingEmail(v.email.trim());
				// Best effort: a fresh code, then the verify page.
				await api()
					.POST("/api/v1/auth/resend", { body: { email: v.email.trim(), purpose: "verify" } })
					.catch(() => undefined);
				await navigate({ to: "/verify" });
				return;
			}
			if (err.code === "invalid_credentials") {
				form.setValue("password", "");
				form.setFocus("password");
			}
			throw err;
		}
	});
	const { error } = submit;

	const signupLink =
		branding.signupMode === "closed" ? (
			<Link to="/signup" className="font-semibold text-brand-text">
				{en.login.talk}
			</Link>
		) : branding.signupAvailable ? (
			<Link to="/signup" className="font-semibold text-brand-text">
				{en.login.create}
			</Link>
		) : null;

	return (
		<AuthLayout title={fill(en.login.title, { product: branding.productName })}>
			{/* LP4 landing */}
			<BackToWebsite />
			{/* /LP4 landing */}
			<form
				ref={formRef} // L5a demo
				className="grid gap-4"
				noValidate
				onSubmit={(e) => void submit.onSubmit(e)}
				aria-describedby={error ? ids.alert : undefined}
			>
				<h1 className="text-2xl font-bold">{fill(en.login.title, { product: branding.productName })}</h1>
				<p className="-mt-2 mb-0 text-foreground-2">{en.login.lead}</p>
				<ReasonBanner reason={reason} />
				{error ? <ErrorAlert id={ids.alert} error={error} until={submit.until} /> : null}
				{error?.code === "accounts_not_configured" ? (
					<Button asChild variant="default" size="lg">
						<Link to="/login/key">{en.login.useKey}</Link>
					</Button>
				) : null}
				<Field id={ids.email} label={en.login.email} error={errs.email?.message}>
					<Input
						id={ids.email}
						type="email"
						autoComplete="email"
						aria-invalid={!!errs.email || undefined}
						aria-describedby={describedBy(ids.email, { error: !!errs.email })}
						{...form.register("email")}
					/>
				</Field>
				<Field
					id={ids.password}
					label={en.login.password}
					error={errs.password?.message}
					labelExtra={
						<Link
							to="/forgot"
							className="text-[13px] font-semibold text-brand-text"
							onClick={() => {
								const typed = form.getValues("email").trim();
								if (typed) writePendingEmail(typed);
							}}
						>
							{en.login.forgot}
						</Link>
					}
				>
					<PasswordField
						id={ids.password}
						autoComplete="current-password"
						aria-invalid={!!errs.password || error?.code === "invalid_credentials" || undefined}
						aria-describedby={describedBy(ids.password, { error: !!errs.password })}
						{...form.register("password")}
					/>
				</Field>
				<div className="flex items-start gap-2.5 text-[13.5px]">
					<Checkbox
						id={ids.keep}
						className="mt-[3px]"
						checked={keep}
						onCheckedChange={(c) => {
							form.setValue("keep", c === true);
							submit.clearError();
						}}
						aria-describedby={`${ids.keep}-hint`}
					/>
					<label htmlFor={ids.keep}>
						<b>{en.login.keep}</b>
						<br />
						<small id={`${ids.keep}-hint`} className="text-muted-foreground">
							{en.login.keepHint}
						</small>
					</label>
				</div>
				{keep ? <FormAlert tone="warn">{en.login.keepWarn}</FormAlert> : null}
				<SubmitButton variant="brand" size="lg" pending={submit.isSubmitting} locked={submit.locked}>
					{en.login.submit}
				</SubmitButton>
				{signupLink ? (
					<p className="m-0 text-center text-[13.5px] text-foreground-2">
						{fill(en.login.newTo, { product: branding.productName })} {signupLink}
					</p>
				) : null}
				<p className="m-0 text-center text-[12.5px] text-muted-foreground">
					<Link
						to="/login/key"
						search={next ? { next } : {}}
						className={
							error?.code === "accounts_not_configured"
								? "font-semibold text-brand-text"
								: "font-medium text-muted-foreground hover:underline"
						}
					>
						{en.login.useKey}
					</Link>{" "}
					· {en.login.forDevelopers}
				</p>
			</form>
			{/* L5a demo */}
			{demo ? (
				<DemoLogins
					info={demo}
					onUse={(login) => {
						submit.clearError();
						form.setValue("email", login.email, { shouldDirty: true, shouldValidate: true });
						form.setValue("password", login.password, { shouldDirty: true, shouldValidate: true });
						formRef.current?.querySelector<HTMLButtonElement>('button[type="submit"]')?.focus();
					}}
				/>
			) : null}
			{/* /L5a demo */}
		</AuthLayout>
	);
}
