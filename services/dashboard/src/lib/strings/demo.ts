/**
 * The live demo: the sign-in card and the banner. A demo server (RAG_DEMO_MODE) shows two
 * sign-ins to every visitor on purpose, so the password is plain text here.
 */
export const demoStrings = {
	title: "Live demo",
	lead: "Sign in with one of these two accounts and look around.",
	operatorRole: "Platform owner",
	ownerRole: "Business owner",
	use: "Use",
	email: "Email",
	password: "Password",
	note: "The password is the same for both. Everything resets every night.",
	banner: "This is a live demo. Some actions are switched off, and everything is reset every night at {time}.",
	region: "About this demo",
	answersLeft: "{left} of {limit} demo answers left today.",
	answersUsedUp: "Today's demo answers are used up. They start again at {time}.",
	rowLabel: "Use the {role} sign-in",
} as const;
