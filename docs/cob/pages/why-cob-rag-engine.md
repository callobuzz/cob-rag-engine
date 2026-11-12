---
audience: public
title: "Why cob-rag-engine if LangChain, LlamaIndex, Dify and agent SDKs exist?"
topic: getting-started
summary: "LangChain, LlamaIndex and agent SDKs are toolkits, Dify is an app builder; cob-rag-engine is the finished knowledge platform and chatbot business for reselling to many customers, and it works with all of them."
---

## The short answer

LangChain and agent SDKs (OpenAI Agents SDK, Claude Agent SDK, LangGraph) are toolkits for building AI. cob-rag-engine is the finished knowledge platform and chatbot business your agents and your customers use: install it and sell AI assistants the same day.

In plain words: a toolkit is a box of parts, and you still have to build the product around them. LlamaIndex is the same kind of thing, focused on getting your data indexed and retrieved. Dify is different: a platform where you build an AI app in a visual builder. cob-rag-engine is the finished product for one job, running knowledge-based assistants for many businesses from one self-hosted engine, already built, tested and measured. It is not an agent framework and it does not try to replace one. Your agents can use it as their knowledge layer.

## What you would still build on a toolkit

Start from LangChain, LlamaIndex, LangGraph or an agent SDK and you get a very good way to call models and tools. To reach what cob-rag-engine ships today, you would still have to build and keep working:

- **Customers, keys, roles and isolation.** Tenants (your customers), knowledge bases, five kinds of credential, owner and member roles, plans and caps. The isolation is tested against attack attempts: 412 of 412 refused in the isolation benchmark (every key kind, owner and member sign-ins, forged tokens, refresh reuse, suspended accounts, enumeration, lockout).
- **Document ingestion.** PDF, DOCX, XLSX, PPTX, HTML, Markdown and audio; OCR for scans, including Hindi (`RAG_OCR_LANGS=hi,en`); page furniture stripped before indexing; near-duplicate review; versioned re-ingestion.
- **Search that is measured.** Hybrid dense and BM25 keyword search with weighted fusion, benchmarked on six public BEIR datasets (mean nDCG@10 0.537 against 0.526 published for the same embedding model) and on real documents.
- **Answers that do not make things up.** A search-confidence gate, and a fact check that makes every price, date, time, number, phone, e-mail, link and name appear in the cited passages, sentence by sentence as the answer streams. When the documents do not say, it says "I don't know" and offers a person.
- **A conversation director.** Greetings, small talk, off-topic and abuse handled before any answer is written; English, Hindi and Hinglish; a model per language; thinking switched off or limited per model; answers started early while the message is still being understood.
- **A website widget that sells.** One script tag, locked to the customer's domains and rate limited; leads with a consent notice that follows the DPDP Act's notice elements; a handoff to a person on WhatsApp, by phone or by e-mail; guided sales replies with one next step. The same assistant also answers on the business's own WhatsApp Business number.
- **Billing and costs.** Plans, prepaid wallets in rupees or dollars, per-question pricing or the real AI cost plus a markup, usage counted per customer, knowledge base and widget, Razorpay payments and GST invoices.
- **Dashboard, COB and evaluation.** An operator and customer dashboard, COB (the built-in assistant), these in-app docs, and a re-runnable retrieval, answer and conversation benchmark suite in the repository.
- **MCP and SDKs.** `cob-rag-mcp` (52 tools) for any MCP client, and Python and JavaScript clients.

## How it differs from LlamaIndex and Dify

- **LlamaIndex** is a developer framework for data ingestion, indexing and retrieval in LLM apps (LlamaCloud and LlamaParse are its paid hosted services). It is a strong retrieval library. You still build the product around it: tenants, widget, leads, billing and a dashboard.
- **Dify** is an open-source platform for building LLM apps, with a visual workflow and agent builder, a knowledge base, a cloud and self-hosting. It is the better choice when you want to design workflows visually, and it has a large plugin and model ecosystem that cob-rag-engine does not. Its licence is Apache 2.0 with added conditions (checked 5 October 2026): you may not use its source to operate a multi-tenant environment without written authorization, and you may not remove its logo or copyright in the console or apps. Read the current text before you build a business on it.
- **What cob-rag-engine has that is built for resale:** customers (tenants), plans, wallets and billing, white-label branding and an operator workspace; Hindi and Hinglish handling with a model per language and Hindi OCR, DPDP consent on leads, WhatsApp handoff and rupee pricing; a fact gate and an honest "I don't know"; published benchmarks; COB and these docs inside the app; and an MCP server.

## They work together

Agents built with LangChain, LlamaIndex, LangGraph, the OpenAI Agents SDK, the Claude Agent SDK, n8n or any MCP client use cob-rag-engine as their knowledge layer. They reach it through MCP (`cob-rag-mcp`, 52 tools), REST, or the Python and JavaScript SDKs, each agent with its own read-only named key.

An agent SDK gives you the loop: the model, the tools, the hand-offs. It does not give you a governed, multi-customer, benchmarked knowledge base, billing, or a chatbot you can sell. That is the part cob-rag-engine adds. You host it yourself; there is no hosted cob-rag-engine.

## Why the answer pipeline has no LangChain

- **Speed and control.** Every step is visible and tunable: early-start answers, a model per language, thinking control, and the fact gate.
- **Stability.** No framework releases to chase underneath a product you sell.
- **A smaller surface.** No LangChain in the default install: our own core handles ingestion, search and answers. The optional evaluation add-on uses RAGAS, which brings LangChain in as its own dependency, used only for quality scoring.
- **Our own provider pattern.** Models, vector databases, parsers, embeddings and storage each sit behind one small interface and can be swapped.

## When another tool alone is the right choice

If you are building a one-off prototype, an unusual pipeline, or an agent for your own use, and you want to hand-build every step, LangChain, LlamaIndex or an agent SDK on its own is the right tool. If you want to design workflows visually, or build internal AI apps for your own company, Dify is a good fit. cob-rag-engine is the right choice when the knowledge belongs to businesses, must stay separate, must be answered honestly, and has to be sold. It has no visual agent builder and no agent loop of its own.

## Side by side

| | LangChain | LlamaIndex | Dify | Agent SDKs | cob-rag-engine |
|---|---|---|---|---|---|
| What it is | An open-source library of parts for AI apps | A framework for ingesting, indexing and retrieving data (paid hosted LlamaCloud and LlamaParse) | An open-source platform to build LLM apps visually: workflows, agents, knowledge base | Libraries that run an agent: model, tools, loop | A self-hosted knowledge platform and chatbot service |
| Time to a working chatbot business | You build the app, accounts, interface and billing around it | You build the product around the retrieval | Quick for your own app in its builder; reselling needs its written authorization | You build the product around the agent | Install, create a customer, upload documents, paste one script tag |
| Multi-customer platform | Not part of the library | Not part of the framework | Multi-tenant operation needs Dify's written authorization (modified Apache 2.0) | Not part of the SDKs | Tenants, per-knowledge-base keys, roles, plans; isolation tested, 412 of 412 attempts refused |
| Document pipeline, including Hindi OCR | Loaders you choose and wire up | Strong loaders and parsing; Hindi OCR is yours to set up | Knowledge base and pipelines; scans depend on plugins | Not what they are for | Parsing, OCR with Hindi, cleanup, near-duplicate review, versions |
| Grounded answers, no made-up facts | You write the checks | You write the checks | Retrieval and citations; checks are built in your workflow | Guardrail hooks; you write the checks | Confidence gate and a sentence-by-sentence fact check; says "I don't know" |
| Website widget, leads, WhatsApp | Not part of the library | Not part of the framework | Embeddable chat app; leads and handoff are yours to build | Not part of the SDKs | Widget and WhatsApp answers, leads with DPDP consent, handoff to a person |
| Billing | Not part of the library | Not part of the framework | App logs; not a billing system for your customers | Not part of the SDKs | Plans, wallets, per-question or cost-plus pricing, Razorpay payments, GST invoices |
| Dashboard and in-app assistant | Not part of the library | Not part of the framework | Its own studio and console | Not part of the SDKs | Dashboard, COB and in-app docs |
| Works with agents | Yes, it is an agent toolkit | Yes, it has agents too | Builds agents itself and has APIs | Yes, it is the agent loop | Yes: MCP (52 tools), REST, Python and JS SDKs, read-only named keys |

## Say it in four lines

1. LangChain, LlamaIndex and the agent SDKs give you parts to build with; cob-rag-engine is the finished knowledge platform: customers, keys, documents, fact-checked answers, a website widget and billing, already built and tested.
2. They are not rivals: your LangChain, LlamaIndex, OpenAI or Claude agent plugs into it over MCP and uses it as its governed knowledge base.
3. Dify is a full platform too, but it is built for designing apps, and its licence needs written authorization to run it for many customers; cob-rag-engine is built to resell, with plans and billing, Hindi and Hinglish, WhatsApp and DPDP consent, and answers that stay inside the customer's own documents.
4. So you sell assistants from day one instead of building the platform under them, and if all you need is a one-off prototype, LangChain alone is the right call.

## Questions people ask

**LangChain is there and good, so why would anyone use cob-rag-engine?** LangChain builds AI; cob-rag-engine is a finished product built for one job: running knowledge-based assistants for many customers from one self-hosted engine. They solve different problems, and they work together.

**What about LlamaIndex?** LlamaIndex is a retrieval framework for developers; you still build tenants, the widget, leads, billing and the dashboard around it. cob-rag-engine ships those, and your LlamaIndex agent can call it over MCP.

**Dify is a full platform too, so why cob-rag-engine?** Dify is for building apps in a visual workflow builder, and has a bigger plugin ecosystem. cob-rag-engine is for reselling: customers, plans, wallets, white-label, Hindi and Hinglish, DPDP consent, WhatsApp handoff and a fact-checked "I don't know". Dify's licence needs written authorization to run it as a multi-tenant service. If you want to design workflows visually for your own company, Dify is a good fit.

**Is cob-rag-engine a LangChain alternative?** Not for building agents. It has no agent framework and no visual builder. It is the knowledge platform an agent, or a customer's website, asks.

**Can I use LangChain, LangGraph, the OpenAI Agents SDK or the Claude Agent SDK with it?** Yes. Use MCP (`cob-rag-mcp`), REST, or the Python and JavaScript SDKs, with a read-only named key for each agent.

**Does it use LangChain inside?** Not in the default install: our own core handles ingestion, search and answers. The optional evaluation add-on uses RAGAS, which brings LangChain in as its own dependency, used only for quality scoring.

**Does it make up answers?** It is built not to. A search-confidence gate and a fact check stop unsupported facts, and with no documented answer it says so and offers a person or a lead form.

**When should I use LangChain or LlamaIndex alone?** For a one-off prototype, an unusual pipeline, or an agent for your own use, where you want to hand-build every step.

**Is there a hosted version?** No. You host it yourself on Postgres, Qdrant and Valkey, with files on the server's disk or in any S3-compatible storage.
