import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Controller } from "react-hook-form";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { PasswordField } from "@/components/auth/PasswordField";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { KeyValue } from "@/components/patterns/KeyValue";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { SecretReveal } from "@/components/patterns/SecretReveal";
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
import { messageFor } from "@/lib/api/error-messages";
import { ApiError } from "@/lib/api/errors";
import { infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { tenantLabel } from "@/lib/format/tenant";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import {
	activeTenantsQuery,
	type CreateCorpusRequest,
	type CreateCorpusResponse,
	embeddingCatalogQuery,
	presetsQuery,
	useCreateCorpus,
} from "./api";
import { KbErrorAlert } from "./KbErrorAlert";
import { PresetCards } from "./PresetCards";
import { type CreateKbForm, createKbSchema, strOrNull } from "./schemas";

export const PROVIDER_SUGGESTIONS = [
	"openrouter",
	"openai",
	"anthropic",
	"gemini",
	"groq",
	"ollama",
	"azure",
	"mistral",
] as const;

export function embeddingValue(provider?: string, model?: string): string {
	return provider && model ? `${provider}|${model}` : "";
}

/**
 * Create a knowledge base (D3 "Create dialog"): name, description, customer (admin, required),
 * preset, embedding, BYOK. On success the ck_ key is shown once; closing forgets it.
 */
export function CreateKbDialog({
	open,
	onOpenChange,
	defaultTenantId,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	defaultTenantId?: string;
}) {
	const [created, setCreated] = useState<CreateCorpusResponse | null>(null);
	const close = (o: boolean) => {
		onOpenChange(o);
		if (!o) setCreated(null);
	};
	return (
		<Dialog open={open} onOpenChange={close}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
				{created ? (
					<Created kb={created} onClose={() => close(false)} />
				) : open ? (
					<CreateForm defaultTenantId={defaultTenantId} onCreated={setCreated} onCancel={() => close(false)} />
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function CreateForm({
	defaultTenantId,
	onCreated,
	onCancel,
}: {
	defaultTenantId?: string;
	onCreated: (kb: CreateCorpusResponse) => void;
	onCancel: () => void;
}) {
	const principal = usePrincipal();
	const isAdmin = principal?.view === "admin";
	const ids = { name: useId(), description: useId(), tenant: useId(), embedding: useId(), provider: useId() };
	const idModel = useId();
	const idFast = useId();
	const idKey = useId();
	const idProviders = useId();
	const presets = useQuery(presetsQuery);
	const embeddings = useQuery(embeddingCatalogQuery);
	const info = useQuery(infoQuery);
	const tenants = useQuery({ ...activeTenantsQuery, enabled: isAdmin });
	const create = useCreateCorpus();
	const [showAdvanced, setShowAdvanced] = useState(false);

	const form = useAppForm<CreateKbForm>({
		resolver: zodResolver(createKbSchema),
		defaultValues: {
			name: "",
			description: "",
			tenant_id: defaultTenantId ?? "",
			preset: "standard",
			embedding: "",
			provider: "",
			model: "",
			fast_model: "",
			api_key: "",
		},
	});
	const errs = form.formState.errors;
	useEffect(() => {
		form.setFocus("name");
	}, [form]);
	// A native select cannot hold a value it has no option for yet: when the customers arrive after
	// the dialog opened (a customer's own page), put the prefilled customer in.
	const customersLoaded = tenants.data != null;
	const { getFieldState, setValue } = form;
	useEffect(() => {
		if (customersLoaded && defaultTenantId && !getFieldState("tenant_id").isDirty)
			setValue("tenant_id", defaultTenantId);
	}, [customersLoaded, defaultTenantId, getFieldState, setValue]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			if (isAdmin && !v.tenant_id) {
				throw new ApiError({ status: 422, code: "tenant_required", detail: "" });
			}
			const body: CreateCorpusRequest = { name: v.name, description: strOrNull(v.description) };
			if (isAdmin) body.tenant_id = v.tenant_id;
			if (v.preset) body.retrieval_config = { preset: v.preset };
			if (v.embedding) {
				const [provider, ...rest] = v.embedding.split("|");
				body.embedding_config = { provider, model: rest.join("|") };
			}
			const llm = {
				provider: strOrNull(v.provider),
				model: strOrNull(v.model),
				fast_model: strOrNull(v.fast_model),
				api_key: v.api_key.trim() || null,
			};
			const llmSet = Object.fromEntries(Object.entries(llm).filter(([, x]) => x !== null));
			if (Object.keys(llmSet).length) body.llm_config = llmSet;
			const kb = await create.mutateAsync(body);
			toast.success(t.create.createdToast, { description: kb.name });
			onCreated(kb);
		},
		{
			fieldName: (path) =>
				path.startsWith("embedding_config")
					? "embedding"
					: path.startsWith("llm_config.")
						? (path.slice(11) as keyof CreateKbForm)
						: (path as keyof CreateKbForm),
			onError: (err) => {
				if (err.code === "tenant_required") {
					form.setError("tenant_id", { type: "server", message: messageFor(err).title });
					form.setFocus("tenant_id");
					return true;
				}
				if (err.status === 404) {
					form.setError("tenant_id", { type: "server", message: t.create.tenantGone });
					return true;
				}
				if (err.status === 422 && !err.fieldErrors.length && /embedding/i.test(err.detail)) {
					form.setError("embedding", { type: "server", message: err.detail });
					return true;
				}
				return false;
			},
		},
	);

	const tenantDisabled = tenants.isError || tenants.isPending;

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{t.create.title}</DialogTitle>
				<DialogDescription>{t.create.lead}</DialogDescription>
			</DialogHeader>
			{submit.error ? <KbErrorAlert error={submit.error} hideAction={isAdmin} /> : null}
			<Field id={ids.name} label={t.create.name} error={errs.name?.message}>
				<Input
					id={ids.name}
					placeholder={t.create.namePlaceholder}
					autoComplete="off"
					aria-invalid={!!errs.name}
					aria-describedby={describedBy(ids.name, { error: !!errs.name })}
					{...form.register("name")}
				/>
			</Field>
			<Field id={ids.description} label={t.create.description} hint={t.create.descriptionHint}>
				<Input
					id={ids.description}
					autoComplete="off"
					aria-describedby={describedBy(ids.description, { hint: true })}
					{...form.register("description")}
				/>
			</Field>
			{isAdmin ? (
				<Field id={ids.tenant} label={t.create.customer} hint={t.create.customerHint} error={errs.tenant_id?.message}>
					<NativeSelect
						id={ids.tenant}
						required
						aria-required="true"
						disabled={tenantDisabled}
						aria-invalid={!!errs.tenant_id}
						aria-describedby={describedBy(ids.tenant, { hint: true, error: !!errs.tenant_id })}
						{...form.register("tenant_id")}
					>
						<option value="">{t.create.customerPick}</option>
						{(tenants.data?.items ?? []).map((x) => (
							<option key={x.id} value={x.id}>
								{tenantLabel(x)}
							</option>
						))}
					</NativeSelect>
					{tenants.isError ? (
						<Button
							type="button"
							size="sm"
							variant="ghost"
							className="justify-self-start"
							onClick={() => void tenants.refetch()}
						>
							{t.create.customersError} {en.common.retry}
						</Button>
					) : null}
				</Field>
			) : null}
			<div className="grid gap-1.5">
				<span className="text-[13px] font-semibold" id={`${ids.name}-preset`}>
					{t.create.searchQuality}
				</span>
				<Controller
					control={form.control}
					name="preset"
					render={({ field }) => (
						<PresetCards
							label={t.create.searchQuality}
							value={field.value}
							presets={presets.data?.presets}
							onChange={field.onChange}
						/>
					)}
				/>
				{presets.isError ? <FormAlert tone="warn">{t.retrieval.presetsError}</FormAlert> : null}
			</div>
			<Field
				id={ids.embedding}
				label={t.create.embedding}
				hint={t.create.embeddingHint}
				error={errs.embedding?.message}
			>
				<NativeSelect
					id={ids.embedding}
					aria-invalid={!!errs.embedding}
					aria-describedby={describedBy(ids.embedding, { hint: true, error: !!errs.embedding })}
					{...form.register("embedding")}
				>
					<option value="">{fill(t.create.engineDefault, { model: info.data?.embedding.model ?? "…" })}</option>
					{(embeddings.data ?? [])
						.filter((m) => !(m.provider === info.data?.embedding.provider && m.model === info.data?.embedding.model))
						.map((m) => (
							<option key={`${m.provider}|${m.model}`} value={embeddingValue(m.provider, m.model)}>
								{[m.name ?? m.model, m.dimensions ? `${m.dimensions} dims` : null, m.provider]
									.filter(Boolean)
									.join(" · ")}
							</option>
						))}
				</NativeSelect>
				{embeddings.isError ? <small className="text-[12.5px] text-warning">{t.embedding.catalogError}</small> : null}
			</Field>
			<div className="rounded-md border border-border">
				<button
					type="button"
					aria-expanded={showAdvanced}
					onClick={() => setShowAdvanced((s) => !s)}
					className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13.5px] font-semibold"
				>
					<ChevronRight className={showAdvanced ? "size-4 rotate-90" : "size-4"} aria-hidden />
					{t.create.advanced}
				</button>
				{showAdvanced ? (
					<div className="grid gap-4 border-t border-border p-3">
						<datalist id={idProviders}>
							{PROVIDER_SUGGESTIONS.map((p) => (
								<option key={p} value={p} />
							))}
						</datalist>
						<div className="grid gap-4 min-[600px]:grid-cols-2">
							<Field id={ids.provider} label={t.model.provider} hint={t.model.providerHint}>
								<Input
									id={ids.provider}
									list={idProviders}
									className="font-mono"
									autoComplete="off"
									aria-describedby={describedBy(ids.provider, { hint: true })}
									{...form.register("provider")}
								/>
							</Field>
							<Field id={idModel} label={t.model.model} hint={t.model.modelHint}>
								<Input
									id={idModel}
									className="font-mono"
									autoComplete="off"
									aria-describedby={describedBy(idModel, { hint: true })}
									{...form.register("model")}
								/>
							</Field>
						</div>
						<Field id={idFast} label={t.model.fastModel} hint={t.model.fastModelHint}>
							<Input
								id={idFast}
								className="font-mono"
								autoComplete="off"
								aria-describedby={describedBy(idFast, { hint: true })}
								{...form.register("fast_model")}
							/>
						</Field>
						<Field id={idKey} label={t.model.newKey} hint={t.create.keyHint}>
							<PasswordField
								id={idKey}
								autoComplete="new-password"
								placeholder={t.model.newKeyPlaceholder}
								aria-describedby={describedBy(idKey, { hint: true })}
								{...form.register("api_key")}
							/>
						</Field>
					</div>
				) : null}
			</div>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onCancel}>
					{en.common.cancel}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.create.creating}
				>
					{t.create.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}

function Created({ kb, onClose }: { kb: CreateCorpusResponse; onClose: () => void }) {
	const navigate = useNavigate();
	return (
		<div className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{fill(t.create.createdTitle, { name: kb.name })}</DialogTitle>
				<DialogDescription>{t.create.keyNote}</DialogDescription>
			</DialogHeader>
			<SecretReveal value={kb.api_key} label={t.create.keyLabel} />
			<div className="rounded-md border border-border">
				<KeyValue
					rows={[
						{ label: t.create.id, value: kb.id },
						{ label: t.create.collection, value: kb.qdrant_collection },
					]}
				/>
			</div>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onClose}>
					{t.create.done}
				</Button>
				<Button
					type="button"
					variant="outline"
					onClick={() => {
						onClose();
						void navigate({ to: "/kbs/$kbId", params: { kbId: kb.id } });
					}}
				>
					{t.create.open}
				</Button>
				<Button
					type="button"
					variant="default"
					onClick={() => {
						onClose();
						void navigate({ to: "/kbs/$kbId/setup", params: { kbId: kb.id } });
					}}
				>
					{t.create.setup}
				</Button>
			</DialogFooter>
		</div>
	);
}
