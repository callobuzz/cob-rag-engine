import { cobAdminStrings as t } from "@/lib/strings/cob-admin";
import type { CobDocument } from "./api";

/** A typed page is stored under `note:<title>`; show the title. */
export function docName(d: Pick<CobDocument, "name">): string {
	return d.name.replace(/^note:/, "");
}

export const audienceLabel = (a: CobDocument["audience"]) => t.audience[a];
