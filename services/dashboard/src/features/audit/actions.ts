/**
 * The audit action catalog (`AUDIT_ACTIONS` in accounts/audit.py, A3–A7) with plain labels.
 * actions.test.ts checks this list against a copy of the engine's catalog, so a new engine action
 * without a label fails the build's tests.
 */
export const AUDIT_ACTION_LABELS = {
	"settings.updated": "Changed sign-up or branding settings",
	"superadmin.bootstrapped": "Created the super admin account",
	"user.password_reset": "Reset a password with an email code",
	"user.password_changed": "Changed a password",
	"user.login_locked": "Sign-in paused after too many tries",
	"session.refresh_reused": "Ended a session used from two places",
	"tenant.signed_up": "Signed up a new workspace",
	"tenant.activated": "Activated a customer",
	"tenant.suspended": "Suspended a customer",
	"tenant.reactivated": "Reactivated a customer",
	"tenant.declined": "Declined a sign-up",
	"tenant.deleted": "Deleted a customer",
	"tenant.created": "Added a customer",
	"member.invited": "Invited someone to a workspace",
	"invitation.revoked": "Revoked an invitation",
	"invitation.accepted": "Accepted an invitation",
	"member.role_changed": "Changed someone's role",
	"member.removed": "Removed someone from a workspace",
	"plan.created": "Created a plan",
	"plan.updated": "Changed a plan",
	"plan.deleted": "Deleted a plan",
	"tenant.plan_changed": "Moved a customer to another plan",
} as const satisfies Record<string, string>;

export type AuditAction = keyof typeof AUDIT_ACTION_LABELS;
export const AUDIT_ACTIONS = Object.keys(AUDIT_ACTION_LABELS) as AuditAction[];

/** Every prefix ending in "." the engine accepts (ACTION_PREFIXES in api/routes/audit.py). */
export const ACTION_PREFIXES: string[] = [
	...new Set(AUDIT_ACTIONS.flatMap((a) => [...a].flatMap((c, i) => (c === "." ? [a.slice(0, i + 1)] : [])))),
].sort();

const PREFIX_LABELS: Record<string, string> = {
	"settings.": "Settings changes",
	"superadmin.": "Super admin account",
	"user.": "Passwords and sign-in",
	"session.": "Sessions",
	"tenant.": "Customer changes",
	"member.": "Team changes",
	"invitation.": "Invitations",
	"plan.": "Plan changes",
};

export function prefixLabel(prefix: string): string {
	return PREFIX_LABELS[prefix] ?? `${prefix}*`;
}

/** Plain label for an action or a prefix; unknown codes are shown as they are. */
export function actionLabel(action: string): string {
	if (action in AUDIT_ACTION_LABELS) return AUDIT_ACTION_LABELS[action as AuditAction];
	if (action.endsWith(".")) return prefixLabel(action);
	return action;
}

export function isKnownAction(value: string): boolean {
	return value in AUDIT_ACTION_LABELS || ACTION_PREFIXES.includes(value);
}
