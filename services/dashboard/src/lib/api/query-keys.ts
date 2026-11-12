/**
 * Query-key factories (README "Data fetching"). Shared roots across epics: qk.branding, qk.me,
 * qk.whoami, qk.info, qk.corpora, qk.documents, qk.usage, qk.plan.me, qk.tenants, qk.plans,
 * qk.widgetKeys, qk.feedback, qk.migrations. Later epics add their own leaves.
 */
export const qk = {
	branding: ["public", "branding"] as const,
	me: ["auth", "me"] as const,
	whoami: ["whoami"] as const,
	info: ["info"] as const,
	health: ["health"] as const,
	corpora: {
		all: ["corpora"] as const,
		list: (params: { limit?: number; offset?: number; tenant_id?: string } = {}) =>
			["corpora", "list", params] as const,
		detail: (id: string) => ["corpora", "detail", id] as const,
	},
	assistant: {
		all: ["assistant"] as const,
		state: (kb: string) => ["assistant", "state", kb] as const,
		versions: (kb: string, p: { offset: number; limit: number }) => ["assistant", "versions", kb, p] as const,
		version: (kb: string, v: number) => ["assistant", "version", kb, v] as const,
		templates: ["assistant", "templates"] as const,
		template: (key: string) => ["assistant", "template", key] as const,
		compile: (kb: string, hash: string) => ["assistant", "compile", kb, hash] as const,
	},
	/** Guided assistant setup sessions (C8). */
	setup: {
		session: (kb: string, sid: string) => ["setup", kb, sid] as const,
		questions: (kb: string, sid: string) => ["setup", kb, sid, "questions"] as const,
		gaps: (kb: string, sid: string) => ["setup", kb, sid, "gaps"] as const,
	},
	documents: {
		all: ["documents"] as const,
		list: (kb: string, params: { search?: string; offset?: number; limit?: number } = {}) =>
			["documents", "list", kb, params] as const,
		detail: (id: string, params: { version?: string; chunkOffset?: number } = {}) =>
			["documents", "detail", id, params] as const,
		detailAll: (id: string) => ["documents", "detail", id] as const,
		versions: (id: string) => ["documents", "versions", id] as const,
	},
	ingest: {
		all: ["ingest"] as const,
		jobs: (params: { corpus_id?: string; status?: string; offset?: number; limit?: number }) =>
			["ingest", "jobs", params] as const,
		job: (id: string) => ["ingest", "job", id] as const,
	},
	usage: {
		all: ["usage"] as const,
		report: (params: Record<string, string | boolean | undefined>) => ["usage", "report", params] as const,
		/** The header's "this month" figure (`GET /usage/consumption`). */
		consumption: ["usage", "consumption"] as const,
		/** The admin Costs page (`GET /admin/costs`). */
		costs: (params: Record<string, string | undefined>) => ["usage", "costs", params] as const,
		/** BIL1: the provider balance cards (`GET /admin/billing/providers`). */
		providers: ["usage", "providers"] as const,
	},
	analytics: {
		all: ["analytics"] as const,
		trends: (params: Record<string, string | undefined>) => ["analytics", "trends", params] as const,
	},
	models: {
		presets: ["models", "presets"] as const,
		catalog: ["models", "catalog"] as const,
		embeddings: ["models", "embeddings"] as const,
	},
	plan: { me: ["plan", "me"] as const },
	tenants: {
		all: ["tenants"] as const,
		count: (status: string) => ["tenants", "count", status] as const,
		list: (params: { status?: string; search?: string; is_operator?: boolean; offset?: number; limit?: number }) =>
			["tenants", "list", params] as const,
		detail: (id: string) => ["tenants", "detail", id] as const,
		plan: (id: string) => ["tenants", "plan", id] as const,
		team: (id: string) => ["tenants", "team", id] as const,
		/** The caller's own workspace (GET /tenants/me). */
		me: ["tenants", "me"] as const,
	},
	plans: {
		all: ["plans"] as const,
		list: ["plans", "list"] as const,
		/** Only the plans a customer may be put on (`include_inactive=false`: no Internal, no switched-off ones). */
		assignable: ["plans", "assignable"] as const,
	},
	// PIP4
	pipelines: {
		all: ["pipelines"] as const,
		list: ["pipelines", "list"] as const,
		lanes: ["pipelines", "lanes"] as const,
		tenant: (id: string) => ["pipelines", "tenant", id] as const,
	},
	fairUse: {
		all: ["fair-use"] as const,
		list: (p: { q?: string; state?: string; offset?: number; limit?: number }) => ["fair-use", "list", p] as const,
		tenant: (id: string) => ["fair-use", "tenant", id] as const,
		/** The owner view (GET /fair-use). */
		mine: ["fair-use", "mine"] as const,
	},
	// /PIP4
	/** The caller's team (GET /tenants/me/members). */
	team: ["team"] as const,
	sessions: ["auth", "sessions"] as const,
	smtp: ["admin", "smtp"] as const,
	widgetKeys: {
		all: ["widgetKeys"] as const,
		byCorpus: (corpusId: string) => ["widgetKeys", "corpus", corpusId] as const,
	},
	/** Named API keys of a knowledge base, a customer or the caller's own workspace. */
	apiKeys: {
		all: ["apiKeys"] as const,
		byOwner: (owner: string) => ["apiKeys", owner] as const,
	},
	feedback: { all: ["feedback"] as const },
	conversations: {
		all: ["conversations"] as const,
		list: (kb: string) => ["conversations", "list", kb] as const,
		thread: (kb: string, id: string) => ["conversations", "thread", kb, id] as const,
	},
	migrations: { all: ["migrations"] as const },
	/** Golden sets, criteria, judge settings, runs, samples and comparisons of a knowledge base (C7). */
	evals: {
		all: ["evals"] as const,
		sets: (kb: string) => ["evals", "sets", kb] as const,
		set: (kb: string, id: string) => ["evals", "set", kb, id] as const,
		criteria: (kb: string) => ["evals", "criteria", kb] as const,
		settings: (kb: string) => ["evals", "settings", kb] as const,
		runs: (kb: string, params: { set_id?: string; kind?: string } = {}) => ["evals", "runs", kb, params] as const,
		run: (kb: string, id: string) => ["evals", "run", kb, id] as const,
		samples: (kb: string, id: string, params: Record<string, string | number | boolean | undefined>) =>
			["evals", "samples", kb, id, params] as const,
		compare: (kb: string, ids: string[]) => ["evals", "compare", kb, ids] as const,
	},
	review: {
		all: ["review"] as const,
		list: (kb: string, params: { kind?: string; from?: string } = {}) => ["review", "list", kb, params] as const,
	},
	/** The leads inbox (C4): the list, one lead with its chat. */
	leads: {
		all: ["leads"] as const,
		list: (params: object) => ["leads", "list", params] as const,
		detail: (id: string) => ["leads", "detail", id] as const,
	},
	/** Handoffs (C5): the list, one handoff with its chat. */
	handoffs: {
		all: ["handoffs"] as const,
		list: (params: object) => ["handoffs", "list", params] as const,
		detail: (id: string) => ["handoffs", "detail", id] as const,
	},
	outcomes: {
		all: ["outcomes"] as const,
		report: (kb: string, params: { from?: string; to?: string } = {}) => ["outcomes", kb, params] as const,
	},
	// BIZ3
	/** The business Home (`GET /home`); `kb` undefined = the workspace's main knowledge base. */
	home: (kb?: string) => ["home", kb ?? "main"] as const,
	articles: {
		all: ["articles"] as const,
		detail: (kb: string, id: string) => ["articles", kb, id] as const,
	},
	// /BIZ3
	// BIZ4
	/** The first-run steps of the workspace (`GET /onboarding`). */
	onboarding: ["onboarding"] as const,
	/** Suggested first questions of a knowledge base, per language. */
	suggestions: (kb: string, lang: string) => ["suggestions", kb, lang] as const,
	// /BIZ4
};
