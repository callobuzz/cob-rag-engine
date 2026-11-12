---
route: /agents
audience: customer
title: "Agents (MCP)"
topic: agents
summary: "Connect a knowledge base to Claude, Cursor, VS Code or n8n with cob-rag-mcp, using a key made for that agent."
starters:
  - "How do I connect Claude to my knowledge base?"
  - "Which key should I give an agent?"
  - "What can an agent do with my knowledge base?"
  - "Why can't my agent reach the engine?"
---
An agent is an AI tool such as Claude Code, Claude Desktop, Cursor, VS Code or n8n. With **cob-rag-mcp** it can search your knowledge base and, with the right key, add content. You host cob-rag-mcp yourself; nothing runs on anyone else's servers.

## What you see

- **Connect an agent**: choose what the key can reach (only this knowledge base, recommended, or all knowledge bases of the workspace) and how cob-rag-mcp is installed (from a local checkout, or from npm with npx). **Create key and show config** makes a key named "Agent - Claude Code" and fills it into the configuration. The key is shown once. Only owners can create keys.
- **Client tabs**: one per tool, each with the exact configuration to copy: a command for Claude Code, a JSON file for Claude Desktop (with the file locations for macOS and Windows), Cursor, VS Code (which asks for the key when it starts, so it is never written to the file) and n8n (an MCP Client Tool node with a Bearer token).
- **Which key?** A knowledge-base key (`ck_`) reaches only this knowledge base and is recommended. A workspace key (`tk_`) reaches all of them. Manage keys links to the knowledge base's API keys tab.
- **What the agent can do**: `search` (passages with sources), `ask` (a full cited answer), `list_documents` and `get_document`, `add_records`, `upload_file` (only over stdio, only from one folder), and usage and feedback tools. The server offers only the tools the key's permission allows. Tools that delete or rotate stay hidden unless `COB_RAG_ALLOW_DESTRUCTIVE=true`; `COB_RAG_READ_ONLY=true` hides every write.
- The **Engine URL** the agent will call, with a warning when it is a loopback address that other machines cannot reach.

## Common tasks

1. **Connect Claude Code.** Choose **Create key and show config**, copy the command from the Claude Code tab, and run it once in a terminal.
2. **Give each agent its own key.** Make one key per agent, with **Read** permission unless it must add content. You can then revoke one without breaking the others.
3. **Reach the engine from another computer.** Ask the platform operator to set the engine's public address so the configuration shows an address the agent can reach.
4. **Connect n8n.** Run the HTTP transport where n8n can reach it, add an MCP Client Tool node, use the Bearer token from the key.

## Troubleshooting

- **Agent says the key was not accepted** (`invalid_api_key`): copy the key again without extra characters, or create a new one.
- **`key_permission_denied`**: the key's permission is lower than the tool needs. Raise it on the API keys tab.
- **"Agents on other machines can't reach 127.0.0.1"**: the engine's public address is not set; ask the operator.
- **Needs Node.js 22 or newer**: install it on the machine that runs the agent.
- **"Only owners can create keys"** (`owner_required`): ask an owner to connect the agent.

## Related

- [Knowledge base settings](/docs/screens/kb-detail)
- [Website widget](/docs/screens/widget)
- [Team](/docs/screens/team)
