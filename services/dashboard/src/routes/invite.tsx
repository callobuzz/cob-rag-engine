import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { MailX } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { ContactBox } from "@/components/auth/ContactBox";
import { ErrorAlert, useApiErrorState } from "@/components/auth/ErrorAlert";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { NewPasswordFields, passwordShape, withPasswordRules } from "@/components/auth/NewPasswordFields";
import { StatusCard } from "@/components/auth/StatusCard";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { InvitationAccepted, InvitationView, TokenResponse } from "@/lib/api/accounts-types";
import { api, unwrap } from "@/lib/api/client";
import { messageFor } from "@/lib/api/error-messages";
import { ApiError, toApiError } from "@/lib/api/errors";
import { completeSignIn, signOut, switchWorkspace } from "@/lib/auth/actions";
import { takeSecretFragment } from "@/lib/auth/fragment";
import { initials } from "@/lib/auth/principal";
import { hasStoredCredential } from "@/lib/auth/session";
import { clearInviteToken, readInviteToken, writeInviteToken, writePendingEmail } from "@/lib/auth/storage";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatDate } from "@/lib/format/date";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { kbColor } from "@/lib/kb-selection";
import { en, fill } from "@/lib/strings/en";

/**
 * Invitation (`/invite#token=inv_…`, A5). The token comes from the fragment (captured and removed
 * before any request) or, when returning from "Log in to accept", from sessionStorage
 * ["cob.inviteToken"]. Branches: new account · signed in as the invitee · signed in as someone
 * else · account exists (log in first) · invalid.
 */
export const Route = createFileRoute("/invite")({
	beforeLoad: async ({ context }) => {
		// Not redirected when signed in: a signed-in invitee accepts here.
		if (context.auth.hasCredential()) await context.auth.ensurePrincipal();
	},
	component: InvitePage,
});

function readToken(): string | null {
	const fromHash = takeSecretFragment("/invite")?.token;
	if (fromHash) {
		// Keep it for the "log in to accept" round trip (this tab only).
		writeInviteToken(fromHash);
		return fromHash;
	}
	return readInviteToken();
}

function InviteCard({ view }: { view: InvitationView }) {
	const { productName } = useBranding();
	return (
		<div className="flex items-center gap-3 rounded-md border border-border bg-card p-3">
			<span
				aria-hidden
				className="grid size-10 shrink-0 place-items-center rounded-[10px] text-sm font-bold text-white"
				style={{ background: kbColor(view.tenant_name) }}
			>
				{initials(view.tenant_name)}
			</span>
			<div>
				<b>{view.tenant_name}</b> {fill(en.invite.on, { product: productName })}
				<small className="block text-[12.5px] text-muted-foreground">
					{fill(en.invite.invitedAs, { by: view.invited_by })} <b>{view.role === "owner" ? "Owner" : "Member"}</b>
				</small>
			</div>
		</div>
	);
}

function DeadEnd({ error }: { error?: ApiError }) {
	return (
		<StatusCard icon={MailX} tone="stop" title={en.invite.invalidTitle}>
			<p className="m-0 text-foreground-2">
				{error && error.code !== "invitation_invalid"
					? messageFor(error).title
					: messageFor(new ApiError({ status: 404, code: "invitation_invalid", detail: "" })).title}
			</p>
			<p className="m-0 font-semibold">{en.invite.askNew}</p>
			<ContactBox />
		</StatusCard>
	);
}

/** New account: name + password (+ repeat), live-validated; accepting signs the person in. */
function NewAccountForm({
	inv,
	token,
	afterJoin,
	onDead,
	onHaveAccount,
}: {
	inv: InvitationView;
	token: string;
	afterJoin: (tenantStatus: string | undefined) => Promise<void>;
	onDead: (err: ApiError) => void;
	onHaveAccount: () => void;
}) {
	const { passwordPolicy } = useBranding();
	const nameId = useId();
	const [schema] = useState(() =>
		withPasswordRules(
			z.object({ name: z.string().trim().min(1, "Enter your name."), ...passwordShape(passwordPolicy) }),
			{ email: () => inv.email },
		),
	);
	const form = useAppForm<{ name: string; password: string; repeat: string }>({
		resolver: zodResolver(schema),
		defaultValues: { name: "", password: "", repeat: "" },
	});
	const nameError = form.formState.errors.name?.message;
	const submit = useAppSubmit(
		form,
		async (v) => {
			const t = (await unwrap(
				api().POST("/api/v1/invitations/accept", { body: { token, name: v.name.trim(), password: v.password } }),
			)) as TokenResponse;
			const p = completeSignIn(t, false);
			await afterJoin(p.tenant?.status);
		},
		{
			passwordPolicy,
			onError: (err) => {
				if (err.code === "invitation_invalid") {
					onDead(err);
					return true;
				}
				if (err.code === "account_exists") {
					onHaveAccount();
					return true;
				}
				return false;
			},
		},
	);
	return (
		<form className="grid gap-4" noValidate onSubmit={(e) => void submit.onSubmit(e)}>
			{submit.error ? <ErrorAlert error={submit.error} until={submit.until} context={{}} /> : null}
			<Field id={nameId} label={en.invite.yourName} error={nameError}>
				<Input
					id={nameId}
					autoComplete="name"
					aria-invalid={!!nameError || undefined}
					aria-describedby={describedBy(nameId, { error: !!nameError })}
					{...form.register("name")}
				/>
			</Field>
			<NewPasswordFields form={form} policy={passwordPolicy} email={inv.email} label={en.signup.password} />
			<SubmitButton variant="brand" size="lg" pending={submit.isSubmitting} locked={submit.locked}>
				{en.invite.accept}
			</SubmitButton>
			<p className="m-0 text-center text-[12.5px] text-foreground-2">
				{fill(en.invite.expires, { date: formatDate(inv.expires_at) })} {en.invite.alreadyAccount}{" "}
				<button type="button" className="font-semibold text-brand-text" onClick={onHaveAccount}>
					{en.invite.loginToAccept}
				</button>
			</p>
		</form>
	);
}

function InvitePage() {
	const [token] = useState(readToken);
	const principal = usePrincipal();
	const navigate = useNavigate();
	const [busy, setBusy] = useState(false);
	const [dead, setDead] = useState<ApiError | null>(null);
	const { error, set: setError, clear, until, locked } = useApiErrorState();

	const view = useQuery({
		queryKey: ["invitation", "view"],
		queryFn: () => unwrap(api().POST("/api/v1/invitations/view", { body: { token: token ?? "" } })),
		enabled: !!token,
		retry: false,
		gcTime: 0,
		staleTime: Number.POSITIVE_INFINITY,
	});

	const title = view.data ? fill(en.invite.titleJoin, { tenant: view.data.tenant_name }) : en.invite.invalidTitle;

	if (!token) {
		return (
			<AuthLayout title={en.invite.invalidTitle}>
				<StatusCard icon={MailX} tone="stop" title={en.invite.invalidTitle}>
					<FormAlert tone="warn">{en.invite.missing}</FormAlert>
				</StatusCard>
			</AuthLayout>
		);
	}
	if (view.isPending) {
		return (
			<AuthLayout title={en.invite.loading}>
				<p className="m-0 text-foreground-2" role="status">
					{en.invite.loading}
				</p>
				<Skeleton className="h-16 w-full" />
			</AuthLayout>
		);
	}
	const viewError = view.error ? toApiError(view.error) : null;
	if (dead || (viewError && viewError.code === "invitation_invalid") || (viewError && viewError.status === 404)) {
		clearInviteToken();
		return (
			<AuthLayout title={en.invite.invalidTitle}>
				<DeadEnd error={dead ?? viewError ?? undefined} />
			</AuthLayout>
		);
	}
	if (viewError || !view.data) {
		return (
			<AuthLayout title={en.invite.invalidTitle}>
				<ErrorAlert error={viewError ?? new ApiError({ status: 0, code: "network", detail: "" })} until={until} />
				<Button onClick={() => void view.refetch()}>{en.common.retry}</Button>
			</AuthLayout>
		);
	}

	const inv = view.data;
	const session = principal?.auth === "session" ? principal : null;
	const matches = !!session?.user && session.user.email.toLowerCase() === inv.email.toLowerCase();

	const afterJoin = async (tenantStatus: string | undefined) => {
		clearInviteToken();
		toast.success(fill(en.invite.joined, { tenant: inv.tenant_name }));
		await navigate({ to: tenantStatus && tenantStatus !== "active" ? "/status" : "/" });
	};

	const handleAcceptError = async (e: unknown) => {
		const err = setError(e);
		if (err.code === "invitation_invalid") setDead(err);
		else if (err.code === "account_exists") goLogin();
	};

	const goLogin = () => {
		writeInviteToken(token);
		writePendingEmail(inv.email);
		void navigate({ to: "/login", search: { next: "/invite" } });
	};

	const acceptSignedIn = async () => {
		clear();
		setBusy(true);
		try {
			const r = (await unwrap(api().POST("/api/v1/invitations/accept", { body: { token } }))) as InvitationAccepted;
			const p = await switchWorkspace(r.tenant.id);
			await afterJoin(p?.tenant?.status ?? r.tenant.status);
		} catch (e) {
			await handleAcceptError(e);
		} finally {
			setBusy(false);
		}
	};

	const logoutAndUse = async () => {
		writeInviteToken(token);
		writePendingEmail(inv.email);
		await signOut(null, { navigate: false });
		await navigate({ to: "/login", search: { next: "/invite" } });
	};

	const footer = (
		<p className="m-0 text-center text-[12.5px] text-foreground-2">
			{fill(en.invite.expires, { date: formatDate(inv.expires_at) })}
		</p>
	);

	return (
		<AuthLayout title={title}>
			<div className="grid gap-4">
				<h1 className="text-2xl font-bold">{title}</h1>
				<InviteCard view={inv} />
				{error ? <ErrorAlert error={error} until={until} context={{}} /> : null}
				<Field id="invite-email" label={en.login.email}>
					<Input id="invite-email" value={inv.email} readOnly />
				</Field>
				{session && matches ? (
					<>
						<Button variant="brand" size="lg" disabled={busy || locked} onClick={() => void acceptSignedIn()}>
							{en.invite.accept}
						</Button>
						{footer}
					</>
				) : session ? (
					<>
						<FormAlert>
							{
								messageFor(
									new ApiError({
										status: 403,
										code: "invitation_email_mismatch",
										detail: "",
										extra: { email: inv.email },
									}),
								).title
							}
						</FormAlert>
						<Button variant="brand" size="lg" onClick={() => void logoutAndUse()}>
							{fill(en.invite.mismatchLogout, { email: inv.email })}
						</Button>
					</>
				) : inv.account_exists ? (
					<>
						<Button variant="brand" size="lg" onClick={goLogin}>
							{en.invite.loginToAccept}
						</Button>
						{footer}
					</>
				) : (
					<NewAccountForm inv={inv} token={token} afterJoin={afterJoin} onDead={setDead} onHaveAccount={goLogin} />
				)}
				{!session && !hasStoredCredential() ? null : (
					<p className="m-0 text-center text-[12.5px]">
						<Link to="/" className="text-muted-foreground">
							{en.common.goHome}
						</Link>
					</p>
				)}
			</div>
		</AuthLayout>
	);
}
