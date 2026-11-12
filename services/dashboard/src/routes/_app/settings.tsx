import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Monitor, Moon, Sun } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { KeyValue } from "@/components/patterns/KeyValue";
import { Segmented } from "@/components/patterns/Segmented";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { WorkspaceSection } from "@/features/workspace/WorkspaceKeyCard";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { displayName, roleBadge } from "@/lib/auth/principal";
import { runtime } from "@/lib/auth/runtime";
import { writeApiKey } from "@/lib/auth/storage";
import { getAuthState, setAuthState, useAuth } from "@/lib/auth/store";
import { getConfig, isHttpUrl, normalizeUrl } from "@/lib/config";
import { getUsdInrRate, setCurrency, useCurrency } from "@/lib/format/currency";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { setDensity, setTheme, useDensity, useTheme } from "@/lib/theme";

export const Route = createFileRoute("/_app/settings")({
	component: SettingsPage,
});

function Card({ title, children }: { title: string; children: ReactNode }) {
	return (
		<section className="rounded-lg border border-border bg-card" aria-label={title}>
			<div className="border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{title}</h2>
			</div>
			<div className="grid gap-4 p-4">{children}</div>
		</section>
	);
}

function Row({ label, children }: { label: string; children: ReactNode }) {
	return (
		<div className="flex flex-wrap items-center justify-between gap-3">
			<span className="text-[13.5px] font-semibold">{label}</span>
			{children}
		</div>
	);
}

function SettingsPage() {
	const theme = useTheme();
	const density = useDensity();
	const currency = useCurrency();
	const { credential, principal } = useAuth();
	const config = getConfig();
	return (
		<>
			<PageHeader title={en.settings.title} description={en.settings.lead} />
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-2">
				<Card title={en.settings.appearance}>
					<Row label={en.settings.theme}>
						<Segmented
							label={en.settings.theme}
							value={theme}
							onChange={setTheme}
							options={[
								{
									value: "light",
									label: (
										<>
											<Sun className="size-3.5" aria-hidden /> {en.shell.light}
										</>
									),
								},
								{
									value: "dark",
									label: (
										<>
											<Moon className="size-3.5" aria-hidden /> {en.shell.dark}
										</>
									),
								},
								{
									value: "system",
									label: (
										<>
											<Monitor className="size-3.5" aria-hidden /> {en.shell.system}
										</>
									),
								},
							]}
						/>
					</Row>
					<Row label={en.settings.density}>
						<Segmented
							label={en.settings.density}
							value={density}
							onChange={setDensity}
							options={[
								{ value: "comfortable", label: en.settings.comfortable },
								{ value: "compact", label: en.settings.compact },
							]}
						/>
					</Row>
				</Card>
				<Card title={en.settings.display}>
					<Row label={en.settings.currency}>
						<Segmented
							label={en.settings.currency}
							value={currency}
							onChange={setCurrency}
							options={[
								{ value: "INR", label: "₹ INR" },
								{ value: "USD", label: "$ USD" },
							]}
						/>
					</Row>
					<p className="m-0 text-[13px] text-muted-foreground">
						{fill(en.settings.currencyNote, { rate: getUsdInrRate() })}
					</p>
				</Card>
				<Card title={en.settings.connection}>
					{credential?.kind === "api_key" ? (
						<EngineUrlEditor current={credential.ragUrl} />
					) : (
						<KeyValue rows={[{ label: en.settings.engineUrl, value: config.ragUrl }]} />
					)}
					<KeyValue rows={[{ label: en.settings.scraperUrl, value: config.scraperUrl }]} />
					{credential?.kind !== "api_key" ? (
						<p className="m-0 text-[13px] text-muted-foreground">{en.settings.connectionReadOnly}</p>
					) : null}
				</Card>
				<Card title={en.settings.signedInAs}>
					{principal ? (
						<KeyValue
							rows={[
								{
									label: "Sign-in",
									value: principal.auth === "session" ? en.settings.authSession : en.settings.authKey,
								},
								{ label: "Who", value: displayName(principal) },
								...(principal.user ? [{ label: "Email", value: principal.user.email }] : []),
								{ label: "Role", value: roleBadge(principal) },
							]}
						/>
					) : null}
					{principal?.auth === "session" ? (
						<Link to="/profile" className="text-[13px] font-semibold text-brand-text">
							{en.settings.logoutOthers}
						</Link>
					) : null}
				</Card>
				{/* D7: the tenant's own workspace and its tk_ key. */}
				{principal?.view === "tenant" ? <WorkspaceSection /> : null}
			</div>
		</>
	);
}

const urlSchema = z.object({
	url: z.string().refine((v) => isHttpUrl(v.trim()), { message: "Use an http:// or https:// address." }),
});

/** Key sign-ins may point at another engine: Test = GET /api/v1/health, Save updates the credential. */
function EngineUrlEditor({ current }: { current: string }) {
	const id = useId();
	const form = useAppForm<{ url: string }>({
		resolver: zodResolver(urlSchema),
		defaultValues: { url: current },
	});
	const value = form.watch("url");
	const submit = useAppSubmit(form, (v) => {
		const c = getAuthState().credential;
		if (c?.kind !== "api_key") return;
		const url = normalizeUrl(v.url);
		writeApiKey(c.key, url, c.persistent);
		setAuthState({ credential: { ...c, ragUrl: url } });
		runtime.clearCache();
		toast.success(en.settings.savedUrl);
	});
	const error = form.formState.errors.url?.message;
	const [probe, setProbe] = useState<{ error: string | null; pending: boolean }>({ error: null, pending: false });
	const test = async () => {
		setProbe({ error: null, pending: true });
		if (!(await form.trigger("url"))) {
			setProbe({ error: null, pending: false });
			form.setFocus("url");
			return;
		}
		try {
			const res = await fetch(`${normalizeUrl(form.getValues("url"))}/api/v1/health`);
			const body = (await res.json().catch(() => ({}))) as { status?: string };
			toast.success(fill(en.settings.testOk, { status: body.status ?? String(res.status) }));
			setProbe({ error: null, pending: false });
		} catch (e) {
			setProbe({
				error: messageFor(toApiError(e), { ragUrl: normalizeUrl(form.getValues("url")) }).title,
				pending: false,
			});
		}
	};
	return (
		<form noValidate onSubmit={(e) => void submit.onSubmit(e)} className="grid gap-2">
			<label htmlFor={id} className="text-[13px] font-semibold">
				{en.settings.engineUrl}
			</label>
			<Input
				id={id}
				className="font-mono"
				aria-invalid={!!error || undefined}
				aria-describedby={error ? `${id}-error` : undefined}
				{...form.register("url", { onChange: () => setProbe((p) => ({ ...p, error: null })) })}
			/>
			{error ? (
				<small id={`${id}-error`} className="text-[12.5px] font-medium text-destructive">
					{error}
				</small>
			) : null}
			<div className="flex gap-2">
				<Button type="button" size="sm" onClick={() => void test()} aria-busy={probe.pending || undefined}>
					{en.settings.test}
				</Button>
				<SubmitButton
					size="sm"
					variant="default"
					pending={submit.isSubmitting}
					locked={normalizeUrl(value) === current}
				>
					{en.settings.saveUrl}
				</SubmitButton>
			</div>
			{probe.error ? <FormAlert>{probe.error}</FormAlert> : null}
		</form>
	);
}
