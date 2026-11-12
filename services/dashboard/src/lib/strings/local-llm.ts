/** PIP2: the Engine page's card for the free local model (deployer screens only). */
export const localModelStrings = {
	title: "Free local model",
	sub: "A small AI model on this server's own CPU: free for you, slower for visitors",
	loadError: "Could not check the local model",
	states: {
		not_configured: "Not set up",
		pulling: "Downloading the model",
		starting: "Starting",
		ready: "Ready",
		down: "Not reachable",
	} as Record<string, string>,
	body: {
		not_configured:
			"Businesses on the Free speed are paused politely until this is on. To set it up, run the setup wizard (python scripts/setup.py --full) and say yes to free answers on this server, then start the stack again.",
		pulling:
			"Downloading the model… this can take a few minutes the first time. Businesses on the Free speed are paused politely until it is ready.",
		starting: "Starting… Businesses on the Free speed are paused politely until it answers.",
		ready:
			"Ready — answers on this server's CPU. Expect 10 to 40 seconds for an answer. To use it, turn on the Free plan in Plans (it starts switched off) and give a business that plan.",
		down: "Not reachable, or it cannot load the model (often too little memory). Check that the local-llm service is running and read docker compose logs local-llm. Businesses on the Free speed are paused politely meanwhile.",
	} as Record<string, string>,
	rows: { model: "Model", held: "Downloaded models", checked: "Checked" },
	none: "none yet",
	tryIt: "Try it",
	trying: "Asking the model…",
	waiting: "Try again in a few seconds",
	result: (first: string, rate: string | null) =>
		rate ? `First words after ${first} · ${rate} words a second` : `First words after ${first}`,
	said: "It said",
	noAnswer: "The model did not answer.",
	tooSoon: "The test just ran. Try again in a few seconds.",
	notReady: "The local model is not ready yet.",
	failed: "The test failed.",
};
