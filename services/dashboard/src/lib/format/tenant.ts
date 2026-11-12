import { operatorStrings } from "@/lib/strings/accounts";

/** A customer's name for a picker; the operator's own workspace says so ("Sahayak AI (your workspace)"). */
export function tenantLabel(t: { name: string; is_operator?: boolean }): string {
	return t.is_operator ? `${t.name} ${operatorStrings.labelSuffix}` : t.name;
}
