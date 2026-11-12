/**
 * API-key strings: the keys table of a knowledge base, a customer and the workspace, and the
 * dialogs on it. Same rules as `en.ts` (`{x}` filled by `fill()`). User-facing text says
 * "knowledge base", never "corpus".
 */
export const keysStrings = {
	title: "API keys",
	sub: {
		kb: "Keys that reach only {name}. Give each agent or app its own key, so you can cut one off without breaking the others.",
		customer: "Keys that reach all of {name}'s knowledge bases. Give each agent or app its own key.",
		workspace: "Keys that reach all of your workspace's knowledge bases. Give each agent or app its own key.",
	},
	memberNote:
		"You're a Member: only owners can create, change, rotate or revoke keys. Ask an owner if a key needs changing.",
	loadError: "Couldn't load the keys.",
	empty: {
		title: "No keys yet",
		body: "Create a key to let an app or an agent use {name}. The key is shown once, when you create it.",
	},
	columns: {
		name: "Name",
		key: "Key",
		permission: "Permission",
		lastUsed: "Last used",
		createdBy: "Created by",
		actions: "Actions",
	},
	caption: "API keys",
	defaultTag: "Default",
	inUseTag: "In use here",
	neverUsed: "Never",
	createdByMigrated: "Before named keys",
	create: {
		button: "Create key",
		title: "Create a key",
		lead: "A named key for one app or agent. You'll see the key once.",
		nameLabel: "Name",
		nameHint: "What it is for, for example Agent – Claude Code.",
		namePlaceholder: "Agent – Claude Code",
		permissionLabel: "Permission",
		submit: "Create key",
		submitting: "Creating…",
		toast: "Key created",
		nameRequired: "Give the key a name.",
		nameTooLong: "Use at most 120 characters.",
	},
	reveal: {
		title: "New key: {name}",
		lead: "Copy it now. The engine keeps only a fingerprint, so it can't show it again.",
		label: "New key",
		done: "Done",
		selfRotate: "You're signed in with this key. Closing this signs you out so you can sign in with the new one.",
		doneSignOut: "Done, sign me out",
	},
	permission: {
		read: { label: "Read", help: "Search, ask, list and get. Cannot change anything." },
		write: { label: "Write", help: "Read, plus add and change content and settings. Cannot delete." },
		full: { label: "Full", help: "Everything, including deleting and managing keys. Use sparingly." },
		fieldLabel: "Permission",
	},
	edit: {
		action: "Edit",
		title: "Edit {name}",
		lead: "Rename it or change what it may do. A change applies on its very next request.",
		submit: "Save changes",
		toast: "Key updated",
		lowerWarn: "Anything using this key that needs more than {level} will start getting refused.",
		liveWarn: "{name} was used {when}: something live is probably using it.",
		nothing: "Change the name or the permission first.",
	},
	rotate: {
		action: "Rotate",
		title: "Rotate {name}?",
		body: 'A new key is created and shown once. The old one stops working at once: whatever uses it will get "key not accepted" until you give it the new key.',
		liveWarn: "This key was used {when}. Something live is probably using it, and it will fail until you update it.",
		selfWarn: "You're signed in with this key. After you rotate it you'll be signed out.",
		confirm: "Rotate key",
		toast: "Key rotated",
		revealTitle: "New key: {name}",
		revealWarn: "The old key has stopped working. Update every app and agent that used it.",
	},
	revoke: {
		action: "Revoke",
		title: "Revoke {name}?",
		body: 'The key stops working at once and can\'t be restored. Anything using it gets "key not accepted".',
		liveWarn: "This key was used {when}. Something live is probably using it.",
		selfWarn: "You're signed in with this key. Revoking it signs you out.",
		word: "revoke",
		confirm: "Revoke key",
		toast: "Key revoked",
	},
	rowActions: "Actions for {name}",
	errors: {
		permission: "Choose Read, Write or Full.",
	},
} as const;

/** Where a risky action's "used recently" warning kicks in. */
export const RECENT_USE_MS = 24 * 60 * 60 * 1000;
