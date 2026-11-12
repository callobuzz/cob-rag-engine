import type { components } from "@/lib/api/schema";
import type { FullConfig, LangKey } from "./schemas";

type S = components["schemas"];

export type { FullConfig, LangKey, TemplateKey, TurnLabel } from "./schemas";
export type LocalizedText = Partial<Record<LangKey, string>>;

/**
 * The engine returns `config` as an untyped object (it is always a full, valid configuration);
 * `api.ts` runs it through `assistantConfigSchema` so every screen works with `FullConfig`.
 */
export type AssistantVersion = Omit<S["AssistantVersionResponse"], "config"> & { config: FullConfig };
export type AssistantState = Omit<S["AssistantStateResponse"], "published" | "draft"> & {
	published: AssistantVersion | null;
	draft: AssistantVersion | null;
};
export type AssistantVersionSummary = S["AssistantVersionSummary"];
export type AssistantVersionList = S["AssistantVersionListResponse"];
export type CompileSample = S["CompileSample"];
export type CompileResponse = S["CompileResponse"];
export type TemplateSummary = S["TemplateSummary"];
export type TemplateDetail = Omit<S["TemplateDetail"], "config"> & { config: FullConfig };
export type LeadFormBlock = S["LeadFormBlock"];
export type HandoffCardBlock = S["HandoffCardBlock"];
export type UiBlock = LeadFormBlock | HandoffCardBlock;

/** One place to add a section: later epics append here and to `AssistantTab`'s body map. */
export const ASSISTANT_SECTIONS = [
	"overview",
	"identity",
	"voice",
	"language",
	"scope",
	"rules",
	"conversation",
	"leads",
	"handoff",
	"advanced",
	"versions",
	"tests",
	"preview",
] as const;
export type AssistantSection = (typeof ASSISTANT_SECTIONS)[number];
