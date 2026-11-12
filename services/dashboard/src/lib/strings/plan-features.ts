/**
 * When the deployer's plan hides part of the dashboard from a business (BIZ3). The engine refuses
 * the same routes with `feature_not_in_plan`; these are the words around that.
 */
export const planFeatureStrings = {
	notIncludedTitle: "Not included in your plan",
	notIncludedBody: "This part isn't included in your plan. Ask {support} if you need it.",
	supportFallback: "the team that set up your account",
	back: "Back",
	codeOnly: {
		lead: "Your plan shows the code to paste on your website and the sites it works on.",
		sites: "Allowed sites",
		noSites: "No website added yet. Add your website address so your assistant works there.",
		addLabel: "Add your website",
		addButton: "Add",
		removeSite: "Remove {origin}",
		anySite: "Add one website address, like https://shreedental.in. A star is not allowed here.",
		pause: "Pause",
		resume: "Resume",
		pausedToast: "Your assistant is paused on your website.",
		resumedToast: "Your assistant is back on your website.",
		none: "Your website code will appear here once your assistant is set up.",
	},
} as const;
