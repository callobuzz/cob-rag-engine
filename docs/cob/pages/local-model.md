---
audience: admin
title: "The free local model"
topic: operations
summary: "A small AI model that answers on your own server's CPU, for free. What it is, what it needs, how to turn it on, how to see when it is ready, and how to choose another model."
starters:
  - "How do I turn on the free local model?"
  - "Why does the Engine page say the local model is downloading?"
  - "How much memory does the local model need?"
  - "How do I choose a different local model?"
---
The free local model is a small open AI model that runs on your own server's CPU. Businesses on the **Free** answer speed (shown to them as "Economy") get their answers from it. It costs you no AI bill, and it is slower: expect 10 to 40 seconds for an answer on a typical server, instead of a few seconds. Answers are still grounded in the business's own knowledge base and cited, exactly as with any other model.

It is also what a business falls back to when its balance runs out or it passes a fair-use limit, so a customer whose subscription ended still gets answers, free and slow, instead of a stop. If the local model is not set up or not ready yet, that fallback pauses politely and shows the business's contact options, as it always did.

## What it needs

- About **6 GB of free memory** for a model with about 4 billion parameters (the default, `gemma3:4b`). A smaller model needs less and answers worse; a larger one needs more.
- A few GB of disk for the downloaded model, kept in a Docker volume so it is downloaded once.
- CPU time. The model answers **one question at a time** by default (`RAG_LOCAL_LLM_SLOTS=1`) so the rest of the engine stays responsive; a free question that arrives while it is busy waits up to `RAG_ANSWER_QUEUE_MAX_WAIT_SECONDS` (20 seconds by default) for its turn and then gets the polite "One moment…" reply. Because an answer takes 10 to 40 seconds, with one slot most overlapping free questions get that reply. Raise `RAG_LOCAL_LLM_SLOTS` if the server has the CPU for it.

## How to turn it on

1. Run `python scripts/setup.py --full` and answer **yes** to "Offer free answers that run on this server's own CPU?". This sets `RAG_LOCAL_LLM_ENABLED=true` and adds `local-llm` to `COMPOSE_PROFILES`. By hand, set both yourself.
2. Start it with `docker compose up -d`. The first start downloads the model, which takes a few minutes.
3. Open **Engine**. The **Free local model** card says "Starting…", then "Downloading the model…", then "Ready — answers on this server's CPU". Choose **Try it** to hear it say a few words and see how fast it is.
4. Open **Plans** and turn on the **Free** plan (it starts switched off, so it cannot be given to a business by mistake), then give a business that plan. Or make the Free profile the fallback for plans.

While the card does not say Ready, a business on the Free speed is paused politely instead of getting an error. If the model fails in the middle of an answer, the visitor gets the polite "One moment…" reply and the card turns to Not reachable until the model loads again.

## What the card says

- **Not set up**: the local model is not part of this install. Use the wizard as above.
- **Downloading the model**: the server is up and still fetching the model. This only happens at the first start or after you change the model.
- **Starting**: the service was just started and is not answering yet. If it stays like this for 15 minutes the card changes to Not reachable.
- **Ready**: it is answering. The engine loads the model into memory as soon as it is listed and reloads it if the service restarts, so the first question is rarely slow because of a cold start.
- **Not reachable**: the service stopped or crashed, or it is running but cannot load the model (usually too little memory: see What it needs). Check `docker compose logs local-llm`. The engine tries to load the model again every 30 seconds and turns Ready by itself once it can.

## How to choose a different model

Set `RAG_LOCAL_LLM_MODEL` to any model the local server can download (for example `qwen3:4b` or `llama3.2:3b`) and restart; the new model is downloaded at the next start. You can also name the model on the Free profile in **Pipeline profiles**, but that model must be downloaded first: `docker compose exec local-llm ollama pull <model>`. Until it is, the profile shows as not available and its businesses are paused politely. To find out which small model is good enough in English, Hindi and Hinglish on your CPU, run `python -m benchmarks.local_llm` (it only ever calls your local model, never a paid one). The **Benchmarks** page shows the measured table, and the setup wizard offers the best model that passed.

If no model passes, the page says plainly: slow and less accurate, use it for a free trial tier only.

## What it costs

Nothing per answer. A local answer is counted as a question but adds nothing to AI spend, so it never counts towards an AI-spend ceiling. A business that brings its own AI key keeps answering with its own key, whatever speed its plan has.
