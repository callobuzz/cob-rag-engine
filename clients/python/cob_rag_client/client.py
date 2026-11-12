"""Thin, typed wrappers over the cob-rag-engine REST API.

Every method returns the engine's JSON as plain dicts, so nothing here has to
change when the engine adds a field. Errors raise CobRagError with the HTTP
status and the engine's `detail` message.

Which key to use:
  admin key  (RAG_ADMIN_API_KEY) — create tenants and corpora, see everything
  tenant key (tk_...)            — manage one tenant's corpora
  corpus key (ck_...)            — ingest into and query one corpus

A key has a permission: `read` (search, ask, list, get), `write` (read plus add and change
content) or `full` (write plus delete and key management). A call the key's permission does
not allow raises CobRagError with `code == "key_permission_denied"`. Deleting a knowledge base,
a document or a customer with a key needs `confirm_name`, the item's exact name (the
`*_delete_preview` methods say what would be removed and the name to send).
The assistant methods: reads need `read`; draft edits, publish, restore and the on/off
switch need `write` (a workspace member's sign-in may edit drafts but not publish); so does
`improve_assistant_rule`, which spends the knowledge base's model.
The test-set methods (golden sets, evaluation runs, the review inbox): reads, estimates and
comparisons need `read`; creating or changing sets, criteria, runs and ratings need `write`;
deleting a set needs `full`; the judge settings need `write` from an owner.
The guided-setup methods (`start_setup` ... `apply_setup`): reads need `read`, every other step
`write`; `apply_setup` publishes, so a workspace member's sign-in cannot.
The lead methods (`list_leads` ... `withdraw_lead_consent`): reads, updates and the export need
`write` (contact data is not for a read-only key), deleting a lead and managing a knowledge
base's webhooks need `full`.
The handoff methods (`list_handoffs`, `get_handoff`, `update_handoff`): the list needs `read`, one handoff
(it carries the linked lead's contact data) and the status change need `write`.
"""

import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx

JSON = dict[str, Any]
_TERMINAL = {"completed", "failed", "pending_review", "cancelled"}


class CobRagError(Exception):
    """The engine answered with an error status."""

    def __init__(self, status: int, detail: Any, code: str | None = None) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail
        self.code = code  # the engine's machine-readable error code, when it sent one


def _raise_for(response: httpx.Response) -> None:
    if response.is_success:
        return
    code = None
    try:
        body = response.json()
        detail = body.get("detail", response.text)
        code = body.get("code") if isinstance(body.get("code"), str) else None
    except Exception:
        detail = response.text
    raise CobRagError(response.status_code, detail, code)


def _query_body(
    query: str,
    corpus_id: str | None,
    system_prompt: str | None,
    options: JSON | None,
    assistant: JSON | None = None,
) -> JSON:
    body: JSON = {"query": query}
    if corpus_id:
        body["corpus_id"] = corpus_id
    if system_prompt:
        body["system_prompt"] = system_prompt
    if options:
        body["options"] = options
    if assistant:
        body["assistant"] = assistant
    return body


def _llm_headers(llm: JSON | None) -> dict[str, str]:
    """Per-request model override: {"provider", "model", "api_key", "base_url"}."""
    if not llm:
        return {}
    names = {
        "provider": "X-LLM-Provider",
        "model": "X-LLM-Model",
        "api_key": "X-LLM-API-Key",
        "base_url": "X-LLM-Base-URL",
    }
    return {names[k]: str(v) for k, v in llm.items() if k in names and v}


def _file_part(file: str | os.PathLike[str] | bytes, filename: str | None) -> tuple[str, bytes]:
    if isinstance(file, bytes):
        return filename or "document", file
    path = Path(file)
    return filename or path.name, path.read_bytes()


def _clean(params: dict[str, Any]) -> dict[str, str]:
    """Query parameters without the ones left unset."""
    return {k: str(v) for k, v in params.items() if v is not None}


def _present(body: dict[str, Any]) -> dict[str, Any]:
    """A JSON body without the fields left unset (values keep their type)."""
    return {k: v for k, v in body.items() if v is not None}


class _SSEParser:
    """Named Server-Sent Events from /query/stream, fed one line at a time.

    The engine sends `event: sources`, then `event: token` repeatedly, then
    `event: done` (or `event: error`), each with one JSON `data:` line.
    """

    def __init__(self) -> None:
        self._event = "message"
        self._data: list[str] = []

    def feed(self, line: str) -> JSON | None:
        """A completed event as {"event", "data"}, or None while one is building."""
        line = line.rstrip("\r")
        if not line:
            if not self._data:
                return None
            payload = "\n".join(self._data)
            event = {"event": self._event, "data": json.loads(payload)}
            self._event, self._data = "message", []
            return event
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        return None


def _token_or_stop(event: JSON) -> str | None:
    """query_stream's view of an event: the text, "" to skip, None to stop."""
    name, data = event["event"], event["data"]
    if name == "token":
        return str(data.get("content", ""))
    if name == "error":
        raise CobRagError(502, data.get("detail", "generation failed"))
    if name == "done":
        return None
    return ""


class CobRag:
    """Synchronous client."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        *,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "CobRag":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        _raise_for(response)
        return None if response.status_code == 204 else response.json()

    def _text(self, method: str, path: str, **kwargs: Any) -> str:
        response = self._http.request(method, path, **kwargs)
        _raise_for(response)
        return response.text

    # --- identity -----------------------------------------------------------
    def whoami(self) -> JSON:
        """Which kind of key this is (admin, tenant, corpus) and what it belongs to.

        Also `auth` ("api_key" for this client, which never signs in), `role` ("owner" for a
        tenant key) and `user` (set only for dashboard sign-ins).
        """
        return self._req("GET", "/api/v1/whoami")  # type: ignore[no-any-return]

    def info(self) -> JSON:
        """Engine version, enabled features, models and limits."""
        return self._req("GET", "/api/v1/info")  # type: ignore[no-any-return]

    def fast_model_benchmarks(self) -> JSON:
        """The measured fast models (label accuracy, latency, cost, bars) and the recommended
        ones: `{available, benchmarks}`."""
        return self._req("GET", "/api/v1/models/fast-benchmarks")  # type: ignore[no-any-return]

    def answer_model_benchmarks(self) -> JSON:
        """The measured answer models (pass rate, correct declines, latency, cost, a slice per
        language) and the recommended ones: `{available, benchmarks}`."""
        return self._req("GET", "/api/v1/models/answer-benchmarks")  # type: ignore[no-any-return]

    def benchmark_summary(self) -> JSON:
        """Headline tables of the engine's other benchmarks: `{available, summary}`."""
        return self._req("GET", "/api/v1/models/benchmark-summary")  # type: ignore[no-any-return]

    def own_tenant(self) -> JSON:
        """A tenant key's own record: name, limits, corpus count."""
        return self._req("GET", "/api/v1/tenants/me")  # type: ignore[no-any-return]

    def rotate_own_tenant_key(self) -> JSON:
        """Replace this tenant key; the new one is returned once, the old one stops now."""
        return self._req("POST", "/api/v1/tenants/me/rotate-key")  # type: ignore[no-any-return]

    # --- health -------------------------------------------------------------
    def health(self) -> JSON:
        return self._req("GET", "/api/v1/health")  # type: ignore[no-any-return]

    def health_ready(self) -> JSON:
        """Readiness: 200 when Postgres, Redis and Qdrant answer; the engine raises on 503."""
        return self._req("GET", "/api/v1/health/ready")  # type: ignore[no-any-return]

    # --- tenants (admin key) ------------------------------------------------
    def create_tenant(self, name: str, **fields: Any) -> JSON:
        return self._req("POST", "/api/v1/admin/tenants", json={"name": name, **fields})  # type: ignore[no-any-return]

    def list_tenants(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        is_operator: bool | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[JSON]:
        """One page of tenants, newest first. status: pending | active | suspended;
        search: part of the name; is_operator=False leaves out the operator's own workspace
        (customers only). The page's `total` is in the raw response."""
        params = _clean(
            {
                "status": status,
                "search": search,
                "is_operator": None if is_operator is None else str(is_operator).lower(),
                "offset": offset,
                "limit": limit,
            }
        )
        return self._req("GET", "/api/v1/admin/tenants", params=params)["items"]  # type: ignore[no-any-return]

    def activate_tenant(
        self, tenant_id: str, plan_id: str | None = None, note: str | None = None
    ) -> JSON:
        """Activate a pending sign-up, on plan_id (default: the plan it has), with an
        optional note (e.g. how they paid)."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/admin/tenants/{tenant_id}/activate",
            json=_clean({"plan_id": plan_id, "note": note}),
        )

    # --- plans (admin key; my_plan: tenant key or sign-in) --------------------
    def list_plans(self, *, include_inactive: bool = True) -> list[JSON]:
        """Every plan, the default first, each with its tenant_count."""
        params = {"include_inactive": str(include_inactive).lower()}
        return self._req("GET", "/api/v1/admin/plans", params=params)["items"]  # type: ignore[no-any-return]

    def create_plan(self, name: str, **fields: Any) -> JSON:
        """fields: description, max_knowledge_bases, monthly_questions, max_documents,
        max_widget_keys (omit or None = unlimited), is_default."""
        return self._req("POST", "/api/v1/admin/plans", json={"name": name, **fields})  # type: ignore[no-any-return]

    def get_plan(self, plan_id: str) -> JSON:
        """One plan, with its tenant_count."""
        return self._req("GET", f"/api/v1/admin/plans/{plan_id}")  # type: ignore[no-any-return]

    def update_plan(self, plan_id: str, **fields: Any) -> JSON:
        """Only the fields given change; a limit given as None becomes unlimited."""
        return self._req("PATCH", f"/api/v1/admin/plans/{plan_id}", json=fields)  # type: ignore[no-any-return]

    def delete_plan(self, plan_id: str) -> None:
        """Refused (409) while tenants use it or while it is the default."""
        self._req("DELETE", f"/api/v1/admin/plans/{plan_id}")

    def set_tenant_plan(self, tenant_id: str, plan_id: str) -> JSON:
        """Move a tenant to another active plan."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/tenants/{tenant_id}/plan", json={"plan_id": plan_id}
        )

    def tenant_plan(self, tenant_id: str) -> JSON:
        """Admin: a tenant's plan, effective limits and usage this month."""
        return self._req("GET", f"/api/v1/admin/tenants/{tenant_id}/plan")  # type: ignore[no-any-return]

    def my_plan(self) -> JSON:
        """This tenant's plan, limits and usage: {plan, limits, usage, resets_at}."""
        return self._req("GET", "/api/v1/tenants/me/plan")  # type: ignore[no-any-return]

    # --- pipeline profiles (admin key; PIP1) -------------------------------------
    # A profile bundles how a business's questions are answered: model, re-ranker, search width,
    # answer length, queue priority. A plan picks one (`pipeline_profile_id` on the plan); a
    # business may be given another.
    def list_pipeline_profiles(self) -> JSON:
        """Every profile, the default first: {items, default_id}. Each item has its settings,
        what its blank fields mean now (`resolved`) and how many plans and businesses use it."""
        return self._req("GET", "/api/v1/admin/pipelines")  # type: ignore[no-any-return]

    def create_pipeline_profile(
        self, name: str, label: str, *, description: str | None = None, config: JSON | None = None
    ) -> JSON:
        """A new profile. 422 `invalid_profile` names the rule when the settings do not fit."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            "/api/v1/admin/pipelines",
            json=_present(
                {"name": name, "label": label, "description": description, "config": config or {}}
            ),
        )

    def get_pipeline_profile(self, profile_id: str) -> JSON:
        return self._req("GET", f"/api/v1/admin/pipelines/{profile_id}")  # type: ignore[no-any-return]

    def update_pipeline_profile(self, profile_id: str, **fields: Any) -> JSON:
        """Only the fields given change (`name`, `label`, `description`, `config`); `config`
        replaces the whole config."""
        return self._req("PATCH", f"/api/v1/admin/pipelines/{profile_id}", json=fields)  # type: ignore[no-any-return]

    def delete_pipeline_profile(self, profile_id: str) -> None:
        """Refused (409) while a plan or business uses it, or while it is the default."""
        self._req("DELETE", f"/api/v1/admin/pipelines/{profile_id}")

    def get_tenant_pipeline(self, tenant_id: str) -> JSON:
        """Which profile a business answers with, and where it comes from."""
        return self._req("GET", f"/api/v1/admin/pipelines/tenants/{tenant_id}")  # type: ignore[no-any-return]

    def set_tenant_pipeline(self, tenant_id: str, profile_id: str | None) -> JSON:
        """Give a business a profile of its own; None goes back to its plan's."""
        return self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/admin/pipelines/tenants/{tenant_id}",
            json={"profile_id": profile_id},
        )

    # --- fair use (PIP3) -----------------------------------------------------------
    # What stops one business from running up the deployer's costs: limits per plan (a field of
    # the plan: `fair_use_limits`, `fair_use_policy`) with per-business overrides here. The
    # first three are for the admin key; `fair_use()` is a workspace's own read-only view.
    def list_fair_use(
        self,
        *,
        q: str | None = None,
        state: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> JSON:
        """Businesses with their fair-use state (`ok`, `near`, `throttled`, `stopped`), the most
        worrying first: {items, total}."""
        return self._req(  # type: ignore[no-any-return]
            "GET",
            "/api/v1/admin/fair-use",
            params=_present({"q": q, "state": state, "offset": offset, "limit": limit}),
        )

    def get_fair_use(self, tenant_id: str) -> JSON:
        """One business's effective limits and policy, how much of each is used and its state."""
        return self._req("GET", f"/api/v1/admin/fair-use/tenants/{tenant_id}")  # type: ignore[no-any-return]

    def set_fair_use(
        self,
        tenant_id: str,
        *,
        limits: JSON | None = None,
        policy: JSON | None = None,
        clear: list[str] | None = None,
    ) -> JSON:
        """Give a business limits or a policy of its own. A limit set to None is unlimited for
        it; `clear` names settings that go back to following the plan. 422 `invalid_fair_use`
        names the rule that is broken."""
        return self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/admin/fair-use/tenants/{tenant_id}",
            json={"limits": limits or {}, "policy": policy or {}, "clear": clear or []},
        )

    def fair_use(self) -> JSON:
        """This workspace's own view (a workspace sign-in or a tenant key): how fast answers run,
        its state and each limit with the share used. AI usage shows as a share only."""
        return self._req("GET", "/api/v1/fair-use")  # type: ignore[no-any-return]

    # --- handoffs (conversation C5) ----------------------------------------------
    # A handoff is a visitor offered (or tapping) a way to reach a person. The list needs `read`;
    # one handoff carries the linked lead's phone and e-mail, so it and the status change need
    # `write`. A workspace member's sign-in may update (nothing here is owner-only).
    def list_handoffs(
        self,
        *,
        corpus_id: str | None = None,
        status: str | None = None,
        reason: str | None = None,
        channel: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """Handoffs the key may see, newest first. `status` is `new` or `handled`."""
        params = {
            k: v
            for k, v in {
                "corpus_id": corpus_id,
                "status": status,
                "reason": reason,
                "channel": channel,
                "from": date_from,
                "to": date_to,
            }.items()
            if v is not None
        }
        params.update({"offset": str(offset), "limit": str(limit)})
        return self._req("GET", "/api/v1/handoffs", params=params)  # type: ignore[no-any-return]

    def get_handoff(self, handoff_id: str) -> JSON:
        """One handoff with its summary, the masked chat and the lead of a callback request."""
        return self._req("GET", f"/api/v1/handoffs/{handoff_id}")  # type: ignore[no-any-return]

    def update_handoff(self, handoff_id: str, status: str) -> JSON:
        """Mark a handoff `handled` (or `new` again)."""
        return self._req("PATCH", f"/api/v1/handoffs/{handoff_id}", json={"status": status})  # type: ignore[no-any-return]

    # TV2 reference
    def lookup_reference(self, ref: str) -> JSON:
        """Find the chat a visitor quoted a reference for (`SD-4F7K`, any case). Returns the
        matching conversation, handoff and lead ids per knowledge base (needs `read`). Raises
        with code `reference_invalid` (422) or `reference_not_found` (404)."""
        return self._req("GET", "/api/v1/handoffs/lookup", params={"ref": ref})  # type: ignore[no-any-return]

    # /TV2 reference

    # --- WhatsApp (TV6) ----------------------------------------------------------
    # A knowledge base's WhatsApp number and its chats. Reads need `read` (a chat's number and
    # messages need `write`); replying and the chat switches need `write`; settings, the brochure
    # list and disconnecting are owner-only for a signed-in member. The deployer connects a number
    # by hand with an admin key. Connecting by Meta's sign-up window is the dashboard's job.
    def whatsapp(self, corpus_id: str) -> JSON:
        """The knowledge base's WhatsApp number (status, settings, who pays Meta; never a token)."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/whatsapp")  # type: ignore[no-any-return]

    def set_whatsapp_manual(
        self,
        corpus_id: str,
        *,
        phone_number_id: str,
        waba_id: str,
        access_token: str,
        meta_billing: str,
    ) -> JSON:
        """Connect a number by hand (admin key): its id, its WABA id, a permanent token and who
        pays Meta (`business` or `deployer`). The token is checked first and never returned."""
        return self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/corpora/{corpus_id}/whatsapp/manual",
            json={
                "phone_number_id": phone_number_id,
                "waba_id": waba_id,
                "access_token": access_token,
                "meta_billing": meta_billing,
            },
        )

    def update_whatsapp(self, corpus_id: str, **fields: Any) -> JSON:
        """`enabled`, `human_pause_hours` (1 to 72) and `follow_up` (`enabled`, `template_name`,
        `language`, `after_hours`); only the fields given change."""
        return self._req("PATCH", f"/api/v1/corpora/{corpus_id}/whatsapp", json=fields)  # type: ignore[no-any-return]

    def set_whatsapp_documents(self, corpus_id: str, document_ids: list[str]) -> JSON:
        """The documents customers may be sent on WhatsApp (at most 20 PDFs, JPGs or PNGs)."""
        return self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/corpora/{corpus_id}/whatsapp/documents",
            json={"document_ids": document_ids},
        )

    def disconnect_whatsapp(self, corpus_id: str) -> None:
        """Disconnect the number: the token is deleted, the chats stay readable."""
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/whatsapp")

    def whatsapp_threads(
        self, corpus_id: str, *, unread: bool = False, offset: int = 0, limit: int = 50
    ) -> JSON:
        """The WhatsApp chats, most recent customer message first (names masked)."""
        params = {"offset": str(offset), "limit": str(limit)}
        if unread:
            params["unread"] = "true"
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/whatsapp/threads", params=params
        )

    def whatsapp_thread(self, corpus_id: str, thread_id: str) -> JSON:
        """One chat: the number in full and the last 100 messages. Opening it marks it read."""
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}"
        )

    def whatsapp_reply(self, corpus_id: str, thread_id: str, text: str) -> JSON:
        """Answer a customer (text, up to 4,096 characters, within 24 hours of their last
        message); the assistant then stays quiet in that chat."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}/reply",
            json={"text": text},
        )

    def update_whatsapp_thread(self, corpus_id: str, thread_id: str, *, bot_paused: bool) -> JSON:
        """Keep the assistant quiet in one chat (`True`), or let it answer again (`False`)."""
        return self._req(  # type: ignore[no-any-return]
            "PATCH",
            f"/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}",
            json={"bot_paused": bot_paused},
        )

    # /WhatsApp (TV6)

    # --- leads and knowledge-base webhooks (conversation C4) --------------------
    # Lead reads, updates and the export need a `write` key (contact data is more than the
    # read-only agent default may see); deleting a lead and managing webhooks need `full`.
    # Delete, export and webhook changes are owner-only for a signed-in workspace member.
    def list_leads(
        self,
        *,
        corpus_id: str | None = None,
        status: str | None = None,
        trigger: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str | None = None,
        page_url: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> JSON:
        """Leads the key may see, newest first. `q` finds a phone number or e-mail exactly."""
        params = {
            k: v
            for k, v in {
                "corpus_id": corpus_id,
                "status": status,
                "trigger": trigger,
                "date_from": date_from,
                "date_to": date_to,
                "q": q,
                "page_url": page_url,
            }.items()
            if v is not None
        }
        params.update({"offset": str(offset), "limit": str(limit)})
        return self._req("GET", "/api/v1/leads", params=params)  # type: ignore[no-any-return]

    def get_lead(self, lead_id: str) -> JSON:
        """One lead with its summary, notes, consent record and chat transcript."""
        return self._req("GET", f"/api/v1/leads/{lead_id}")  # type: ignore[no-any-return]

    def update_lead(self, lead_id: str, **fields: Any) -> JSON:
        """fields: status, assignee_user_id (None unassigns), notes, qualification."""
        return self._req("PATCH", f"/api/v1/leads/{lead_id}", json=fields)  # type: ignore[no-any-return]

    def delete_lead(self, lead_id: str) -> None:
        """Erase a lead and the website chat it came from (owners only)."""
        self._req("DELETE", f"/api/v1/leads/{lead_id}")

    def export_leads(self, *, corpus_id: str | None = None, **filters: str) -> str:
        """The matching leads as CSV text (owners only; the export is audited)."""
        params = {k: v for k, v in {"corpus_id": corpus_id, **filters}.items() if v is not None}
        return self._text("GET", "/api/v1/leads/export.csv", params=params)

    def list_corpus_webhooks(self, corpus_id: str) -> list[JSON]:
        """The webhooks that receive this knowledge base's leads."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/webhooks")["webhooks"]  # type: ignore[no-any-return]

    def create_corpus_webhook(
        self, corpus_id: str, url: str, events: list[str], secret: str
    ) -> JSON:
        """events: lead.created and/or handoff.requested. https and a public host only."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/webhooks",
            json={"url": url, "events": events, "secret": secret},
        )

    def update_corpus_webhook(self, corpus_id: str, webhook_id: str, *, enabled: bool) -> JSON:
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/webhooks/{webhook_id}", json={"enabled": enabled}
        )

    def delete_corpus_webhook(self, corpus_id: str, webhook_id: str) -> None:
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/webhooks/{webhook_id}")

    def withdraw_lead_consent(self, token: str) -> JSON:
        """What the public withdrawal page does with the token from its link (no key needed)."""
        return self._req("POST", "/api/v1/public/leads/withdraw", json={"token": token})  # type: ignore[no-any-return]

    # --- assistant configuration (conversation C2) -----------------------------
    # Reads need the `read` permission; draft edits, publish, restore and the on/off switch
    # need `write`. A workspace member's sign-in may edit drafts but not publish; a key is
    # never a member. Publishing is reversible: restore an older version.
    def get_assistant(self, corpus_id: str) -> JSON:
        """The published assistant and the draft being edited (either may be null)."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/assistant")  # type: ignore[no-any-return]

    def put_assistant_draft(
        self, corpus_id: str, config: JSON, *, base_revision: int | None = None
    ) -> JSON:
        """Replace the draft. `base_revision` is the draft_revision you edited (409
        `assistant_draft_conflict` if someone else saved since)."""
        body: JSON = {"config": config, **_present({"base_revision": base_revision})}
        return self._req("PUT", f"/api/v1/corpora/{corpus_id}/assistant/draft", json=body)  # type: ignore[no-any-return]

    def patch_assistant_draft(
        self, corpus_id: str, patch: JSON, *, base_revision: int | None = None
    ) -> JSON:
        """Merge a JSON merge patch (RFC 7396; null removes a field) into the draft."""
        body: JSON = {"patch": patch, **_present({"base_revision": base_revision})}
        return self._req("PATCH", f"/api/v1/corpora/{corpus_id}/assistant/draft", json=body)  # type: ignore[no-any-return]

    def assistant_draft_from_template(
        self,
        corpus_id: str,
        template: str,
        *,
        keep_identity: bool = True,
        base_revision: int | None = None,
    ) -> JSON:
        """Start the draft from a business template (see `assistant_templates`)."""
        body: JSON = {
            "template": template,
            "keep_identity": keep_identity,
            **_present({"base_revision": base_revision}),
        }
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/draft/from-template", json=body
        )

    def discard_assistant_draft(self, corpus_id: str) -> None:
        """Throw the draft away; the published assistant and its history are untouched."""
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/assistant/draft")

    def publish_assistant(
        self, corpus_id: str, *, base_revision: int, note: str | None = None
    ) -> JSON:
        """Publish the draft as the next version (owner keys; 422 names what to fix)."""
        body: JSON = {"base_revision": base_revision, **_present({"note": note})}
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/assistant/publish", json=body)  # type: ignore[no-any-return]

    def assistant_versions(self, corpus_id: str, *, offset: int = 0, limit: int = 20) -> JSON:
        """Published and archived versions, newest first: {items, total, offset, limit}."""
        params = {"offset": str(offset), "limit": str(limit)}
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/versions", params=params
        )

    def assistant_version(self, corpus_id: str, version: int) -> JSON:
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/assistant/versions/{version}")  # type: ignore[no-any-return]

    def restore_assistant_version(self, corpus_id: str, version: int) -> JSON:
        """Publish an older version again as a new version (history is never rewritten)."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/versions/{version}/restore"
        )

    def set_assistant_enabled(self, corpus_id: str, enabled: bool) -> JSON:
        """Off = every surface answers exactly as before the assistant existed."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/enabled", json={"enabled": enabled}
        )

    def assistant_analytics(
        self,
        corpus_id: str | None = None,
        *,
        from_: str | None = None,
        to: str | None = None,
    ) -> JSON:
        """Outcome analytics (handled, handed off, leads, cost) for one knowledge base, or for
        all the key can see when corpus_id is None. from_ / to are ISO timestamps."""
        params = _clean({"from": from_, "to": to})
        if corpus_id is None:
            return self._req("GET", "/api/v1/assistant/analytics", params=params)  # type: ignore[no-any-return]
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/analytics", params=params
        )

    def compile_assistant(
        self, corpus_id: str, *, config: JSON | None = None, sample: JSON | None = None
    ) -> JSON:
        """The prompt the model would receive for a sample turn (no model call, nothing saved)."""
        body: JSON = _present({"config": config, "sample": sample})
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/assistant/compile", json=body)  # type: ignore[no-any-return]

    def improve_assistant_rule(
        self, corpus_id: str, instruction: str, type: str, rule_id: str | None = None
    ) -> JSON:
        """Suggest a cleaner wording for a guidance rule (nothing is saved; spends the model,
        so it needs `write`)."""
        body: JSON = _present({"instruction": instruction, "type": type, "rule_id": rule_id})
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/assistant/rules/improve", json=body)  # type: ignore[no-any-return]

    def assistant_templates(self) -> list[JSON]:
        """The eight business templates."""
        return self._req("GET", "/api/v1/assistant/templates")  # type: ignore[no-any-return]

    def assistant_template(self, key: str) -> JSON:
        """One template: its full config and ten test questions."""
        return self._req("GET", f"/api/v1/assistant/templates/{key}")  # type: ignore[no-any-return]

    # --- guided assistant setup (conversation C8) ------------------------------------
    # Reads (`get_setup`, `setup_questions`, `setup_gaps`) need `read`; every other step needs
    # `write`. `apply_setup` publishes: a workspace member's sign-in cannot, a tenant key acts
    # as the owner and a corpus key with `write` can (the same as `publish_assistant`).
    def start_setup(
        self, corpus_id: str, *, business_type: str | None = None, website_url: str | None = None
    ) -> JSON:
        """Start a guided setup for a knowledge base (older unfinished ones are abandoned)."""
        body: JSON = _present({"business_type": business_type, "website_url": website_url})
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/assistant/setup", json=body)  # type: ignore[no-any-return]

    def get_setup(self, corpus_id: str, session_id: str) -> JSON:
        """One setup session: status, proposal, confirmed profile, answers, provenance."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}")  # type: ignore[no-any-return]

    def propose_setup(
        self,
        corpus_id: str,
        session_id: str,
        pages: list[JSON],
        *,
        crawl_job_id: str | None = None,
    ) -> JSON:
        """Send crawled pages (`{url, title, text}`); returns the proposal."""
        body: JSON = _present({"pages": pages, "crawl_job_id": crawl_job_id})
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/propose", json=body
        )

    def setup_questions(self, corpus_id: str, session_id: str) -> JSON:
        """The interview questions (prefilled) and the business profile to confirm."""
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/questions"
        )

    def answer_setup(
        self,
        corpus_id: str,
        session_id: str,
        answers: JSON,
        *,
        business_type: str | None = None,
        profile: JSON | None = None,
        partial: bool = False,
    ) -> JSON:
        """Save the answers and the confirmed profile (name, phone, WhatsApp, e-mail, hours).
        `partial=True` saves progress: required questions may still be open (generate checks)."""
        body: JSON = _present(
            {"answers": answers, "business_type": business_type, "profile": profile}
        )
        if partial:
            body["partial"] = True
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/answers", json=body
        )

    def generate_setup(
        self,
        corpus_id: str,
        session_id: str,
        *,
        wording: str = "ai",
        base_revision: int | None = None,
    ) -> JSON:
        """Write the draft assistant; `wording="template"` makes no model call."""
        body: JSON = _present({"wording": wording, "base_revision": base_revision})
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/generate", json=body
        )

    def run_setup_tests(self, corpus_id: str, session_id: str) -> JSON:
        """Create the setup's test set and start its run; returns golden_set_id / eval_run_id."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/tests"
        )

    def setup_gaps(self, corpus_id: str, session_id: str) -> JSON:
        """Questions the documents could not answer (empty while the run is unfinished)."""
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/gaps"
        )

    def apply_setup(
        self, corpus_id: str, session_id: str, base_revision: int, *, note: str | None = None
    ) -> JSON:
        """Publish the generated draft (`base_revision` is the draft revision you reviewed)."""
        body: JSON = _present({"base_revision": base_revision, "note": note})
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/apply", json=body
        )

    # --- golden sets, evaluation runs and the review inbox (conversation C7) ----------
    # Reads, estimates, comparisons need `read`; creating or changing sets, criteria, runs and
    # ratings need `write` (a full run spends model money and monthly questions); deleting a
    # set needs `full`; the judge settings need `write` from an owner (never a member).
    def list_golden_sets(self, corpus_id: str) -> list[JSON]:
        """The knowledge base's test sets, each with its question count and last run."""
        data = self._req("GET", f"/api/v1/corpora/{corpus_id}/golden-sets")
        return data["items"]  # type: ignore[no-any-return]

    def create_golden_set(self, corpus_id: str, name: str, pairs: list[JSON] | None = None) -> JSON:
        """A set of questions; each pair has `query` and an expected_answer and/or an
        expected_behaviour (answer, decline, handoff, lead, smalltalk, no_answer)."""
        body: JSON = {"name": name, "pairs": pairs or []}
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/golden-sets", json=body)  # type: ignore[no-any-return]

    def golden_set_from_template(self, corpus_id: str, template: str | None = None) -> JSON:
        """A set from a business template's test questions (default: the assistant's own)."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/golden-sets/from-template",
            json={"template": template},
        )

    def get_golden_set(self, corpus_id: str, set_id: str) -> JSON:
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/golden-sets/{set_id}")  # type: ignore[no-any-return]

    def rename_golden_set(self, corpus_id: str, set_id: str, name: str) -> JSON:
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/golden-sets/{set_id}", json={"name": name}
        )

    def delete_golden_set(self, corpus_id: str, set_id: str) -> None:
        """Delete a set with its questions (never the default set). Needs `full`."""
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/golden-sets/{set_id}")

    def add_golden_pairs(self, corpus_id: str, set_id: str, pairs: list[JSON]) -> list[JSON]:
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/golden-sets/{set_id}/pairs", json={"pairs": pairs}
        )

    def list_eval_criteria(self, corpus_id: str) -> list[JSON]:
        """Your own plain-language rules a judge model scores every answer against."""
        data = self._req("GET", f"/api/v1/corpora/{corpus_id}/eval/criteria")
        return data["items"]  # type: ignore[no-any-return]

    def create_eval_criterion(
        self, corpus_id: str, name: str, definition: str, threshold: float = 0.7
    ) -> JSON:
        body: JSON = {"name": name, "definition": definition, "threshold": threshold}
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/eval/criteria", json=body)  # type: ignore[no-any-return]

    def update_eval_criterion(self, corpus_id: str, criterion_id: str, **fields: Any) -> JSON:
        """fields: definition, threshold, enabled."""
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}", json=fields
        )

    def delete_eval_criterion(self, corpus_id: str, criterion_id: str) -> None:
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}")

    def get_eval_settings(self, corpus_id: str) -> JSON:
        """The judge model (never its key) and the pass thresholds."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/eval/settings")  # type: ignore[no-any-return]

    def set_eval_settings(self, corpus_id: str, **fields: Any) -> JSON:
        """fields: judge_provider, judge_model, judge_api_key, judge_base_url, thresholds."""
        return self._req("PUT", f"/api/v1/corpora/{corpus_id}/eval/settings", json=fields)  # type: ignore[no-any-return]

    def estimate_eval_run(
        self, corpus_id: str, set_id: str, *, mode: str = "full", settings: JSON | None = None
    ) -> JSON:
        """What a run would cost (an estimate from token assumptions and the price table)."""
        body: JSON = {"set_id": set_id, "mode": mode, **_present({"settings": settings})}
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/eval/estimate", json=body)  # type: ignore[no-any-return]

    def start_eval_run(
        self,
        corpus_id: str,
        set_id: str,
        *,
        mode: str = "fast",
        name: str | None = None,
        settings: JSON | None = None,
        confirm_cost_usd: float | None = None,
    ) -> JSON:
        """mode "fast": behaviour checks only, no answers. mode "full": fresh answers, scores
        and criteria; send the estimate's total_usd as confirm_cost_usd (409
        `eval_cost_unconfirmed` otherwise). `settings` is the experiment: which assistant
        version, retrieval overrides, answer model."""
        body: JSON = {
            "set_id": set_id,
            "mode": mode,
            **_present({"name": name, "settings": settings, "confirm_cost_usd": confirm_cost_usd}),
        }
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/eval/runs", json=body)  # type: ignore[no-any-return]

    def list_eval_runs(
        self, corpus_id: str, set_id: str | None = None, kind: str | None = None
    ) -> list[JSON]:
        params = _clean({"set_id": set_id, "kind": kind})
        data = self._req("GET", f"/api/v1/corpora/{corpus_id}/eval/runs", params=params or None)
        return data["items"]  # type: ignore[no-any-return]

    def get_eval_run(self, corpus_id: str, run_id: str) -> JSON:
        """A run with its metrics (mean, 95% interval, pass rate), tags and warnings."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/eval/runs/{run_id}")  # type: ignore[no-any-return]

    def list_eval_samples(
        self,
        corpus_id: str,
        run_id: str,
        *,
        failed_only: bool = False,
        metric: str | None = None,
        below: float | None = None,
        tag: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """One row per question: answer, scores, the judge's reasons, behaviour checks."""
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if failed_only:
            params["failed_only"] = "true"
        params.update(_present({"metric": metric, "below": below, "tag": tag}))
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/eval/runs/{run_id}/samples", params=_clean(params)
        )

    def rate_eval_sample(self, corpus_id: str, sample_id: str, rating: str | None) -> JSON:
        """rating: good | acceptable | poor (null clears it)."""
        return self._req(  # type: ignore[no-any-return]
            "PATCH",
            f"/api/v1/corpora/{corpus_id}/eval/samples/{sample_id}",
            json={"rating": rating},
        )

    def compare_eval_runs(self, corpus_id: str, run_ids: list[str]) -> JSON:
        """Two to four completed runs of one set; the first is the baseline."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/eval/compare", json={"run_ids": run_ids}
        )

    def review_items(
        self,
        corpus_id: str,
        kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[JSON]:
        """Questions visitors could not get answered, grouped, most asked first."""
        params = _clean({"kind": kind, "from": since, "to": until})
        data = self._req(
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/review", params=params or None
        )
        return data["items"]  # type: ignore[no-any-return]

    def review_action(self, corpus_id: str, item_key: str, action: str, **payload: Any) -> JSON:
        """action: dismissed | added_document | added_answer | added_rule. payload:
        answer={topic_name, question, answer, language}, rule={instruction, type},
        base_revision (the draft you edited; 0 when there is none)."""
        body: JSON = {"item_key": item_key, "action": action, **payload}
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/review/actions", json=body
        )

    def suspend_tenant(self, tenant_id: str, confirm_name: str, reason: str | None = None) -> JSON:
        """Suspend: every key, widget and sign-in of the tenant stops. confirm_name must be
        the tenant's name exactly."""
        body = {"confirm_name": confirm_name, **_clean({"reason": reason})}
        return self._req("POST", f"/api/v1/admin/tenants/{tenant_id}/suspend", json=body)  # type: ignore[no-any-return]

    def reactivate_tenant(self, tenant_id: str) -> JSON:
        return self._req("POST", f"/api/v1/admin/tenants/{tenant_id}/reactivate")  # type: ignore[no-any-return]

    def decline_tenant(self, tenant_id: str, reason: str | None = None) -> None:
        """Decline a pending sign-up: the tenant and its sign-up account are removed."""
        self._req(
            "POST", f"/api/v1/admin/tenants/{tenant_id}/decline", json=_clean({"reason": reason})
        )

    def delete_tenant(
        self, tenant_id: str, confirm_name: str, *, confirm_operator_workspace: bool = False
    ) -> None:
        """Delete the tenant and everything it owns. confirm_name must be its name exactly.
        The operator's own workspace (`is_operator`) also needs confirm_operator_workspace=True."""
        params = {"confirm_name": confirm_name}
        if confirm_operator_workspace:
            params["confirm_operator_workspace"] = "true"
        self._req("DELETE", f"/api/v1/admin/tenants/{tenant_id}", params=params)

    def tenant_delete_preview(self, tenant_id: str) -> JSON:
        """What deleting the customer would remove (counts) and its `confirm_name`."""
        return self._req("GET", f"/api/v1/admin/tenants/{tenant_id}/delete-preview")  # type: ignore[no-any-return]

    def rotate_tenant_key(self, tenant_id: str) -> JSON:
        return self._req("POST", f"/api/v1/admin/tenants/{tenant_id}/rotate-key")  # type: ignore[no-any-return]

    # --- team (tenant key; admin variants below) ---------------------------
    def team(self) -> JSON:
        """This tenant's members and open invitations: {members, invitations}."""
        return self._req("GET", "/api/v1/tenants/me/members")  # type: ignore[no-any-return]

    def invite(self, email: str, role: str = "member") -> JSON:
        """Invite someone as "owner" or "member"; the link is e-mailed (never returned)."""
        return self._req(  # type: ignore[no-any-return]
            "POST", "/api/v1/tenants/me/members", json={"email": email, "role": role}
        )

    def revoke_invitation(self, invitation_id: str) -> None:
        self._req("DELETE", f"/api/v1/tenants/me/invitations/{invitation_id}")

    def resend_invitation(self, invitation_id: str) -> JSON:
        """E-mail a new link (the old one stops working), valid 7 more days."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/tenants/me/invitations/{invitation_id}/resend"
        )

    def set_member_role(self, user_id: str, role: str) -> JSON:
        """role: "owner" or "member". The last owner stays an owner (409 last_owner)."""
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/tenants/me/members/{user_id}", json={"role": role}
        )

    def remove_member(self, user_id: str) -> None:
        """Remove someone from the workspace; their account stays."""
        self._req("DELETE", f"/api/v1/tenants/me/members/{user_id}")

    def tenant_team(self, tenant_id: str) -> JSON:
        """Admin: a tenant's members and open invitations."""
        return self._req("GET", f"/api/v1/admin/tenants/{tenant_id}/members")  # type: ignore[no-any-return]

    def invite_to_tenant(self, tenant_id: str, email: str, role: str = "owner") -> JSON:
        """Admin: invite someone to a tenant. The response's `accept_url` is the link to
        send them yourself when the engine has no SMTP."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/admin/tenants/{tenant_id}/invitations",
            json={"email": email, "role": role},
        )

    def revoke_tenant_invitation(self, tenant_id: str, invitation_id: str) -> None:
        """Admin: revoke an open invitation of a tenant."""
        self._req("DELETE", f"/api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}")

    def resend_tenant_invitation(self, tenant_id: str, invitation_id: str) -> JSON:
        """Admin: a new link (in `accept_url`) and 7 more days for a tenant's open
        invitation; the old link stops working."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}/resend"
        )

    def set_tenant_member_role(self, tenant_id: str, user_id: str, role: str) -> JSON:
        """Admin: change someone's role in a tenant (409 last_owner for the last owner)."""
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/admin/tenants/{tenant_id}/members/{user_id}", json={"role": role}
        )

    def remove_tenant_member(self, tenant_id: str, user_id: str) -> None:
        """Admin: remove someone from a tenant; their account stays."""
        self._req("DELETE", f"/api/v1/admin/tenants/{tenant_id}/members/{user_id}")

    def restore_operator_workspace(self) -> JSON:
        """Admin: recreate the operator's own workspace after it was deleted on purpose
        (409 operator_workspace_exists when there is nothing to recreate)."""
        return self._req("POST", "/api/v1/admin/operator-workspace/restore")  # type: ignore[no-any-return]

    # --- audit log (admin key) ----------------------------------------------
    def audit_log(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        actor: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """Admin: audit rows, newest first: {items, total, offset, limit}. action is exact
        or a prefix ending in "." ("tenant."); actor is part of who did it; start/end are
        ISO datetimes (default: the last 90 days; at most 366 days)."""
        params = _clean(
            {
                "tenant_id": tenant_id,
                "action": action,
                "actor": actor,
                "start": start,
                "end": end,
                "offset": offset,
                "limit": limit,
            }
        )
        return self._req("GET", "/api/v1/admin/audit", params=params)  # type: ignore[no-any-return]

    # --- corpora (admin or tenant key) --------------------------------------
    def create_corpus(self, name: str, *, tenant_id: str | None = None, **fields: Any) -> JSON:
        """Create a corpus; its ck_ key is in the response, once.

        tenant_id is required with the admin key (every knowledge base belongs to a
        tenant); a tenant key creates corpora for itself and may omit it. fields:
        description, llm_config, prompt_config, retrieval_config, embedding_config.
        """
        body = {"name": name, **_clean({"tenant_id": tenant_id}), **fields}
        return self._req("POST", "/api/v1/corpora", json=body)  # type: ignore[no-any-return]

    def list_corpora(
        self, *, offset: int = 0, limit: int = 100, tenant_id: str | None = None
    ) -> list[JSON]:
        """One page of corpora (newest first); items carry owner, counts, last activity."""
        params = _clean({"offset": offset, "limit": limit, "tenant_id": tenant_id})
        return self._req("GET", "/api/v1/corpora", params=params)["items"]  # type: ignore[no-any-return]

    def get_corpus(self, corpus_id: str) -> JSON:
        return self._req("GET", f"/api/v1/corpora/{corpus_id}")  # type: ignore[no-any-return]

    def update_corpus(self, corpus_id: str, **fields: Any) -> JSON:
        return self._req("PUT", f"/api/v1/corpora/{corpus_id}", json=fields)  # type: ignore[no-any-return]

    def delete_corpus(self, corpus_id: str, confirm_name: str | None = None) -> None:
        """Delete the knowledge base and everything in it. With an API key, confirm_name must
        be its exact name (422 `confirmation_required` / `confirm_mismatch` otherwise)."""
        self._req(
            "DELETE", f"/api/v1/corpora/{corpus_id}", params=_clean({"confirm_name": confirm_name})
        )

    def corpus_delete_preview(self, corpus_id: str) -> JSON:
        """What deleting the knowledge base would remove (counts) and its `confirm_name`."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/delete-preview")  # type: ignore[no-any-return]

    def rotate_corpus_key(self, corpus_id: str) -> JSON:
        """Replace the knowledge base's default key (other named keys are untouched)."""
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/rotate-key")  # type: ignore[no-any-return]

    # --- named API keys (a `full` key, or the admin or tenant key) ------------
    def list_corpus_keys(self, corpus_id: str) -> list[JSON]:
        """The knowledge base's keys: name, prefix, permission, last used. Never a key."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/keys")["keys"]  # type: ignore[no-any-return]

    def create_corpus_key(self, corpus_id: str, name: str, permission: str = "read") -> JSON:
        """A named key (`read`, `write` or `full`); `api_key` in the response is shown once."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/keys",
            json={"name": name, "permission": permission},
        )

    def update_corpus_key(
        self,
        corpus_id: str,
        key_id: str,
        *,
        name: str | None = None,
        permission: str | None = None,
    ) -> JSON:
        body = _clean({"name": name, "permission": permission})
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/keys/{key_id}", json=body
        )

    def rotate_corpus_named_key(self, corpus_id: str, key_id: str) -> JSON:
        """Replace one key's secret; the new `api_key` is returned once, the old one stops."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/keys/{key_id}/rotate"
        )

    def revoke_corpus_key(self, corpus_id: str, key_id: str) -> None:
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/keys/{key_id}")

    def list_my_keys(self) -> list[JSON]:
        """This customer's keys (a `full` tenant key, or an owner's sign-in)."""
        return self._req("GET", "/api/v1/tenants/me/keys")["keys"]  # type: ignore[no-any-return]

    def create_my_key(self, name: str, permission: str = "read") -> JSON:
        return self._req(  # type: ignore[no-any-return]
            "POST", "/api/v1/tenants/me/keys", json={"name": name, "permission": permission}
        )

    def update_my_key(
        self, key_id: str, *, name: str | None = None, permission: str | None = None
    ) -> JSON:
        body = _clean({"name": name, "permission": permission})
        return self._req("PATCH", f"/api/v1/tenants/me/keys/{key_id}", json=body)  # type: ignore[no-any-return]

    def rotate_my_named_key(self, key_id: str) -> JSON:
        return self._req("POST", f"/api/v1/tenants/me/keys/{key_id}/rotate")  # type: ignore[no-any-return]

    def revoke_my_key(self, key_id: str) -> None:
        self._req("DELETE", f"/api/v1/tenants/me/keys/{key_id}")

    def list_tenant_keys(self, tenant_id: str) -> list[JSON]:
        """A customer's keys (admin key)."""
        return self._req("GET", f"/api/v1/admin/tenants/{tenant_id}/keys")["keys"]  # type: ignore[no-any-return]

    def create_tenant_key(self, tenant_id: str, name: str, permission: str = "read") -> JSON:
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/admin/tenants/{tenant_id}/keys",
            json={"name": name, "permission": permission},
        )

    def update_tenant_key(
        self,
        tenant_id: str,
        key_id: str,
        *,
        name: str | None = None,
        permission: str | None = None,
    ) -> JSON:
        body = _clean({"name": name, "permission": permission})
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/admin/tenants/{tenant_id}/keys/{key_id}", json=body
        )

    def rotate_tenant_named_key(self, tenant_id: str, key_id: str) -> JSON:
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/tenants/{tenant_id}/keys/{key_id}/rotate"
        )

    def revoke_tenant_key(self, tenant_id: str, key_id: str) -> None:
        self._req("DELETE", f"/api/v1/admin/tenants/{tenant_id}/keys/{key_id}")

    # --- ingestion (corpus key, or tenant/admin key + corpus_id) ------------
    def ingest(
        self,
        file: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
        corpus_id: str | None = None,
        metadata: JSON | None = None,
        mode: str = "auto",
        auto_confirm: bool = False,
        wait: bool = False,
        poll_interval: float = 2.0,
        wait_timeout: float = 600.0,
    ) -> JSON:
        """Upload a document. With wait=True, block until the job finishes."""
        name, content = _file_part(file, filename)
        # mode: auto (hold near-duplicates for review) | new | replace | version
        data: dict[str, str] = {"mode": mode}
        if auto_confirm:
            data["auto_confirm"] = "true"
        if corpus_id:
            data["corpus_id"] = corpus_id
        if metadata:
            data["metadata"] = json.dumps(metadata)
        job: JSON = self._req("POST", "/api/v1/ingest", files={"file": (name, content)}, data=data)
        return self.wait_for_job(job["job_id"], poll_interval, wait_timeout) if wait else job

    def ingest_records(
        self,
        records: list[JSON],
        *,
        corpus_id: str | None = None,
        wait: bool = False,
        poll_interval: float = 1.0,
        wait_timeout: float = 600.0,
    ) -> JSON:
        """Import up to 1000 text records ({id, text, title?, url?, metadata?}) per call.

        Re-importing an id replaces that record; unchanged records are skipped.
        """
        body: JSON = {"records": records}
        if corpus_id:
            body["corpus_id"] = corpus_id
        job: JSON = self._req("POST", "/api/v1/ingest/records", json=body)
        return self.wait_for_job(job["job_id"], poll_interval, wait_timeout) if wait else job

    def get_job(self, job_id: str) -> JSON:
        return self._req("GET", f"/api/v1/ingest/jobs/{job_id}")  # type: ignore[no-any-return]

    def list_jobs(self, **params: Any) -> JSON:
        """params: status, corpus_id, offset, limit. Returns {items, total, offset, limit}."""
        return self._req("GET", "/api/v1/ingest/jobs", params=_clean(params))  # type: ignore[no-any-return]

    def retry_ingest_job(self, job_id: str) -> JSON:
        """Run a failed upload again from its stored file, as a new job.

        Returns {job_id (the new job), status}. A job that cannot be retried is a 409
        `job_not_retryable` with a `reason`: records_job, not_failed, source_unknown or
        file_missing."""
        return self._req("POST", f"/api/v1/ingest/jobs/{job_id}/retry")  # type: ignore[no-any-return]

    # --- documents (corpus key, or tenant/admin key + corpus_id) -------------
    def list_documents(self, **params: Any) -> JSON:
        """params: corpus_id (admin/tenant keys), search, source (website | articles | files),
        offset, limit."""
        return self._req("GET", "/api/v1/documents", params=_clean(params))  # type: ignore[no-any-return]

    def get_document(self, document_id: str, **params: Any) -> JSON:
        """A document, its versions and chunks. params: corpus_id, version_id, chunk_offset,
        chunk_limit."""
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/documents/{document_id}", params=_clean(params)
        )

    def delete_document(
        self, document_id: str, confirm_name: str | None = None, **params: Any
    ) -> None:
        """Delete a document with all its versions, chunks and vectors. With an API key,
        confirm_name must be its file name (or record id) exactly. params: corpus_id."""
        self._req(
            "DELETE",
            f"/api/v1/documents/{document_id}",
            params=_clean({**params, "confirm_name": confirm_name}),
        )

    def document_delete_preview(self, document_id: str, **params: Any) -> JSON:
        """What deleting the document would remove, and its `confirm_name`. params: corpus_id."""
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/documents/{document_id}/delete-preview", params=_clean(params)
        )

    def wait_for_job(self, job_id: str, poll_interval: float = 2.0, timeout: float = 600.0) -> JSON:
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_job(job_id)
            if job.get("status") in _TERMINAL:
                return job
            if time.monotonic() > deadline:
                raise TimeoutError(f"ingest job {job_id} still {job.get('status')!r}")
            time.sleep(poll_interval)

    # --- asking -------------------------------------------------------------
    def query(
        self,
        query: str,
        *,
        corpus_id: str | None = None,
        system_prompt: str | None = None,
        options: JSON | None = None,
        llm: JSON | None = None,
        assistant: JSON | None = None,
    ) -> JSON:
        """Answer with citations. `llm` overrides the model for this request.

        `assistant`: conversation-layer options, `{"conversation_id", "page_url",
        "use_draft", "debug", "simulate"}`. With the knowledge base's assistant on, the
        response also carries `label`, `route`, `language`, `quick_replies`, `cta`, `ui`,
        `timings`, `assistant_version` (and `trace` with `debug`, which needs an owner and
        an API key with write permission).
        """
        return self._req(  # type: ignore[no-any-return]
            "POST",
            "/api/v1/query",
            json=_query_body(query, corpus_id, system_prompt, options, assistant),
            headers=_llm_headers(llm),
        )

    def query_stream(
        self,
        query: str,
        *,
        corpus_id: str | None = None,
        system_prompt: str | None = None,
        options: JSON | None = None,
        llm: JSON | None = None,
        assistant: JSON | None = None,
    ) -> Iterator[str]:
        """Yield the answer token by token. A failure part-way raises CobRagError."""
        for event in self.query_stream_events(
            query,
            corpus_id=corpus_id,
            system_prompt=system_prompt,
            options=options,
            llm=llm,
            assistant=assistant,
        ):
            token = _token_or_stop(event)
            if token is None:
                return
            if token:
                yield token

    def query_stream_events(
        self,
        query: str,
        *,
        corpus_id: str | None = None,
        system_prompt: str | None = None,
        options: JSON | None = None,
        llm: JSON | None = None,
        assistant: JSON | None = None,
    ) -> Iterator[JSON]:
        """Yield every event as {"event", "data"}: sources, token..., then done or error.

        `stage` events ({"stage": "understand"|"search"|"rerank"|"check"|"write",
        "elapsed_ms"}) report progress while the engine works and can arrive before
        `sources`; `query_stream` skips them. `sources` carries the citations (each with
        its score_breakdown) and the query_id; `done` repeats the query_id with the model,
        (estimated) token usage with the real cost and `usage.thinking_effective` (the
        thinking the answer model ran at), `timings` (ms per stage) and, for a reasoning
        model, `thinking_ms` and `reasoning_tokens`.

        With the assistant on (`assistant` is the same option as on `query`) the stream
        also carries `meta` (label, route, language; first after the `understand` stage),
        `quick_replies`, `ui` (a lead form or handoff card) and `trace` (debug only), and
        `done` adds `label`, `route`, `language`, `cta`, `claims_removed` and
        `gate_threshold`. `query_stream` yields tokens only and ignores all of them.
        """
        with self._http.stream(
            "POST",
            "/api/v1/query/stream",
            json=_query_body(query, corpus_id, system_prompt, options, assistant),
            headers=_llm_headers(llm),
        ) as response:
            if not response.is_success:
                response.read()
                _raise_for(response)
            parser = _SSEParser()
            for line in response.iter_lines():
                event = parser.feed(line)
                if event is not None:
                    yield event
            event = parser.feed("")
            if event is not None:
                yield event

    def retrieve(
        self, query: str, *, corpus_id: str | None = None, options: JSON | None = None
    ) -> JSON:
        """Ranked chunks only — no LLM call. Bring your own generation."""
        return self._req(  # type: ignore[no-any-return]
            "POST", "/api/v1/query/retrieve", json=_query_body(query, corpus_id, None, options)
        )

    # --- website widget -----------------------------------------------------
    def create_widget_key(
        self, corpus_id: str, name: str, allowed_origins: list[str], **fields: Any
    ) -> JSON:
        """fields: rate_limit_per_minute, daily_message_limit, appearance{...}"""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/widget-keys",
            json={"name": name, "allowed_origins": allowed_origins, **fields},
        )

    def list_widget_keys(self, corpus_id: str) -> list[JSON]:
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/widget-keys")  # type: ignore[no-any-return]

    def update_widget_key(self, corpus_id: str, key_id: str, **fields: Any) -> JSON:
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/widget-keys/{key_id}", json=fields
        )

    def rotate_widget_key(self, corpus_id: str, key_id: str) -> JSON:
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/widget-keys/{key_id}/rotate"
        )

    def delete_widget_key(self, corpus_id: str, key_id: str) -> None:
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/widget-keys/{key_id}")

    # --- usage --------------------------------------------------------------
    def usage(self, **params: Any) -> JSON:
        """params: corpus_id, tenant_id, start, end (YYYY-MM-DD), group_by."""
        clean = {k: str(v) for k, v in params.items() if v is not None}
        return self._req("GET", "/api/v1/usage", params=clean)  # type: ignore[no-any-return]

    def usage_consumption(self) -> JSON:
        """The dashboard header summary (tenant key or workspace session; not a ck_ key)."""
        return self._req("GET", "/api/v1/usage/consumption")  # type: ignore[no-any-return]

    def costs(self, **params: Any) -> JSON:
        """Admin: platform cost report. params: start, end (YYYY-MM-DD)."""
        clean = {k: str(v) for k, v in params.items() if v is not None}
        return self._req("GET", "/api/v1/admin/costs", params=clean)  # type: ignore[no-any-return]

    # BIL1 provider balances
    def provider_balances(self) -> JSON:
        """Admin: what OpenRouter, OpenAI, Anthropic and any manual provider have left.

        {"items": [...], "worst": {...} | None}; amounts are integer USD micro-units
        (1_000_000 = $1) and no admin key is ever returned.
        """
        return self._req("GET", "/api/v1/admin/billing/providers")  # type: ignore[no-any-return]

    # BIL2 pricing and accounts
    def get_plan_pricing(self, plan_id: str) -> JSON:
        """Admin: how customers on a plan pay (version null = no pricing, nothing is charged).

        Money is integer minor units (paise / cents) next to `currency`."""
        return self._req("GET", f"/api/v1/admin/billing/plans/{plan_id}/pricing")  # type: ignore[no-any-return]

    def set_plan_pricing(self, plan_id: str, **fields: Any) -> JSON:
        """Admin: replace a plan's pricing with a new version. Every field not given takes its
        default; the response lists the fields that `changed`. 409 `pricing_locked` for the
        Internal plan."""
        return self._req("PUT", f"/api/v1/admin/billing/plans/{plan_id}/pricing", json=fields)  # type: ignore[no-any-return]

    def list_billing_accounts(
        self,
        *,
        q: str | None = None,
        payment_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """Admin: every customer's billing, `{items, total}`. payment_type: prepaid | postpaid."""
        params = _clean({"q": q, "payment_type": payment_type, "offset": offset, "limit": limit})
        return self._req("GET", "/api/v1/admin/billing/accounts", params=params)  # type: ignore[no-any-return]

    def get_billing_account(self, tenant_id: str) -> JSON:
        """Admin: one customer's billing account (created on first read), with its effective
        terms and what it overrides."""
        return self._req("GET", f"/api/v1/admin/billing/accounts/{tenant_id}")  # type: ignore[no-any-return]

    def update_billing_account(self, tenant_id: str, **fields: Any) -> JSON:
        """Admin: change a customer's terms. fields: overrides ({"per_question_minor": 150};
        None removes one), limit_overrides, currency, pause_override, billing_day, notes."""
        return self._req("PATCH", f"/api/v1/admin/billing/accounts/{tenant_id}", json=fields)  # type: ignore[no-any-return]

    def billing_account(self) -> JSON:
        """The caller's own workspace: how it pays (money fields are None for a member)."""
        return self._req("GET", "/api/v1/billing/account")  # type: ignore[no-any-return]

    # /BIL2 pricing and accounts

    # BIL3 ledger
    def list_ledger(
        self,
        tenant_id: str,
        *,
        entry_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """Admin: a customer's transactions, newest first, `{items, total, balance_minor,
        available_minor}`. `start` / `end` are YYYY-MM-DD (UTC, inclusive); `entry_type` one of
        top_up, subscription_charge, usage_charge, add_on_purchase, adjustment, refund,
        included_credit, expiry. Amounts are integer minor units next to `currency`."""
        params = _clean(
            {"entry_type": entry_type, "start": start, "end": end, "offset": offset, "limit": limit}
        )
        return self._req("GET", f"/api/v1/admin/billing/accounts/{tenant_id}/ledger", params=params)  # type: ignore[no-any-return]

    def add_ledger_entry(self, tenant_id: str, **fields: Any) -> JSON:
        """Admin: record a top-up, an adjustment (signed) or a refund. fields: entry_type,
        amount_minor, idempotency_key (a uuid per attempt: sending it again changes nothing),
        memo (required for an adjustment or refund), memo_public, confirm_amount_minor (required
        at or above the engine's confirmation limit), expires_at (positive adjustments)."""
        return self._req("POST", f"/api/v1/admin/billing/accounts/{tenant_id}/ledger", json=fields)  # type: ignore[no-any-return]

    def billing_ledger(self, *, offset: int = 0, limit: int = 50) -> JSON:
        """Owner: this workspace's transactions, newest first, `{items, total}`; each line has the
        customer-facing `description`. A member session gets 403 `owner_required`."""
        return self._req(  # type: ignore[no-any-return]
            "GET", "/api/v1/billing/ledger", params=_clean({"offset": offset, "limit": limit})
        )

    # /BIL3 ledger

    # P1 payments
    def list_payments(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """Admin: payments received, newest first, `{items, total}`, with each payment's fee.
        `status` is captured, authorized, failed, mismatch, refunded or partially_refunded;
        `start` / `end` are YYYY-MM-DD (UTC). Amounts are integer minor units (paise)."""
        params = _clean(
            {
                "tenant_id": tenant_id,
                "status": status,
                "start": start,
                "end": end,
                "offset": offset,
                "limit": limit,
            }
        )
        return self._req("GET", "/api/v1/admin/billing/payments", params=params)  # type: ignore[no-any-return]

    def get_payment(self, payment_id: str) -> JSON:
        """Admin: one payment with its refunds, the deliveries about it and what can still be
        refunded (`refundable_minor`)."""
        return self._req("GET", f"/api/v1/admin/billing/payments/{payment_id}")  # type: ignore[no-any-return]

    def refund_payment(self, payment_id: str, **fields: Any) -> JSON:
        """Admin: refund part of a payment from the workspace's unused balance, to the way it
        paid. fields: amount_minor (what is taken off the balance, before GST), memo,
        idempotency_key, confirm_amount_minor (required for a large refund). A refusal by the
        payment service is 502 `refund_failed` and the money is put back."""
        return self._req(
            "POST", f"/api/v1/admin/billing/payments/{payment_id}/refunds", json=fields
        )  # type: ignore[no-any-return]

    def create_payment_link(self, tenant_id: str, **fields: Any) -> JSON:
        """Admin: a link a customer opens to pay. fields: purpose (top_up, plan, add_on),
        amount_minor (top-ups), add_on_id, send_email. Returns `{order_id, short_url, expires_at,
        gross_minor, emailed}`."""
        return self._req(
            "POST", f"/api/v1/admin/billing/accounts/{tenant_id}/payment-links", json=fields
        )  # type: ignore[no-any-return]

    def tax_details(self) -> JSON:
        """Owner: who this workspace's tax invoices are made out to (`complete` says whether
        they are enough to pay). A member session gets 403 `owner_required`."""
        return self._req("GET", "/api/v1/billing/tax-details")  # type: ignore[no-any-return]

    def set_tax_details(self, **fields: Any) -> JSON:
        """Owner: save the billing name, address and state (a GST number is optional; its state
        wins). fields: legal_name, address_line1, city, state_code, pincode, gstin, ..."""
        return self._req("PUT", "/api/v1/billing/tax-details", json=fields)  # type: ignore[no-any-return]

    def billing_payments(self, *, offset: int = 0, limit: int = 50) -> JSON:
        """Owner: this workspace's payments, newest first, `{items, total}` (no fees)."""
        return self._req(  # type: ignore[no-any-return]
            "GET", "/api/v1/billing/payments", params=_clean({"offset": offset, "limit": limit})
        )

    # /P1 payments

    # P2 invoices
    def billing_invoices(self, *, offset: int = 0, limit: int = 50) -> JSON:
        """Owner: this workspace's tax invoices and credit notes, newest first, `{items, total}`.
        Each item says whether its PDF is ready (the PDF itself is downloaded in the dashboard)."""
        return self._req(  # type: ignore[no-any-return]
            "GET", "/api/v1/billing/invoices", params=_clean({"offset": offset, "limit": limit})
        )

    def list_invoices(
        self,
        *,
        tenant_id: str | None = None,
        doc_type: str | None = None,
        fy: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """Admin: every tax document, newest first: `{items, total, totals}`. `doc_type`:
        invoice, bill_of_supply, credit_note. `fy` like `26-27`; `start`/`end` are YYYY-MM-DD
        (Indian time). `totals` covers everything the filter matches (credit notes subtract)."""
        return self._req(  # type: ignore[no-any-return]
            "GET",
            "/api/v1/admin/billing/invoices",
            params=_clean(
                {
                    "tenant_id": tenant_id,
                    "doc_type": doc_type,
                    "fy": fy,
                    "start": start,
                    "end": end,
                    "offset": offset,
                    "limit": limit,
                }
            ),
        )

    # /P2 invoices

    # BIL5 add-ons
    def list_add_ons(self, *, include_inactive: bool = False) -> JSON:
        """Admin: the add-on catalogue, `{items}` (credit packs, extra limits, question packs)."""
        params = {"include_inactive": "true"} if include_inactive else None
        return self._req("GET", "/api/v1/admin/billing/add-ons", params=params)  # type: ignore[no-any-return]

    def create_add_on(self, **fields: Any) -> JSON:
        """Admin: add one to the catalogue. fields: kind (credit_pack | extra_limit |
        question_pack), name, currency, price_minor, and the fields of the kind: credit_minor
        (+ bonus_pct, expiry_days), limit_field + limit_delta + duration, or questions."""
        return self._req("POST", "/api/v1/admin/billing/add-ons", json=fields)  # type: ignore[no-any-return]

    def update_add_on(self, add_on_id: str, **fields: Any) -> JSON:
        """Admin: change an add-on (only the fields you send; `is_active=False` retires it).
        Customers who already have it keep what they were given."""
        return self._req("PATCH", f"/api/v1/admin/billing/add-ons/{add_on_id}", json=fields)  # type: ignore[no-any-return]

    def delete_add_on(self, add_on_id: str) -> None:
        """Admin: delete an add-on never given to anyone (else 409 `add_on_in_use`)."""
        self._req("DELETE", f"/api/v1/admin/billing/add-ons/{add_on_id}")

    def list_grants(self, tenant_id: str, *, active_only: bool = False) -> JSON:
        """Admin: the add-ons given to a customer, newest first, `{items}`; `active_only` keeps
        those raising a limit right now."""
        params = {"active_only": "true"} if active_only else None
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/admin/billing/accounts/{tenant_id}/grants", params=params
        )

    def apply_add_on(self, tenant_id: str, **fields: Any) -> JSON:
        """Admin: apply an add-on to a customer. fields: add_on_id, idempotency_key (a uuid per
        attempt: sending it again returns the same grant), payment (external | free | wallet),
        memo. A credit pack adds its credit and bonus to the balance; a limit or question pack
        raises the limit until the end of the month or until revoked."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/billing/accounts/{tenant_id}/grants", json=fields
        )

    def revoke_grant(self, tenant_id: str, grant_id: str, *, memo: str | None = None) -> JSON:
        """Admin: end an extra limit or question pack now. Money is not returned automatically;
        a credit pack cannot be revoked (409 `grant_not_revocable`)."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/admin/billing/accounts/{tenant_id}/grants/{grant_id}/revoke",
            json={"memo": memo} if memo else {},
        )

    # /BIL5 add-ons

    # BIL6 statements
    def list_statements(self, tenant_id: str) -> JSON:
        """Admin: a customer's monthly statements, newest first,
        `{items: [{month, currency, opening_minor, closing_minor, live}]}`. The current month is
        `live` (still changing)."""
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/admin/billing/accounts/{tenant_id}/statements"
        )

    def get_statement(self, tenant_id: str, month: str, *, format: str = "json") -> Any:
        """Admin: one month (`YYYY-MM`, UTC) of a customer's statement. `format` is "json" (a
        dict), "csv" or "html" (text; the html page prints to PDF). 404 `statement_not_found`
        for a month before the account existed or in the future."""
        if format == "json":
            return self._req(
                "GET", f"/api/v1/admin/billing/accounts/{tenant_id}/statements/{month}"
            )
        return self._text(
            "GET",
            f"/api/v1/admin/billing/accounts/{tenant_id}/statements/{month}",
            params={"format": format},
        )

    def billing_statements(self) -> JSON:
        """Owner: this workspace's monthly statements, newest first, `{items}`. A member session
        gets 403 `owner_required`."""
        return self._req("GET", "/api/v1/billing/statements")  # type: ignore[no-any-return]

    def billing_statement(self, month: str, *, format: str = "json") -> Any:
        """Owner: one month (`YYYY-MM`) of this workspace's statement, as "json" (a dict), "csv"
        or "html" (text)."""
        if format == "json":
            return self._req("GET", f"/api/v1/billing/statements/{month}")
        return self._text("GET", f"/api/v1/billing/statements/{month}", params={"format": format})

    def request_top_up(self, *, amount_minor: int | None = None, note: str | None = None) -> JSON:
        """Owner: ask the operator to add money (an e-mail to them), `{sent}`. Amount in minor
        units (paise / cents), both fields optional. One request per workspace per hour: another
        within the hour is 429 `topup_request_too_soon` with a `Retry-After`."""
        body: dict[str, Any] = {}  # not `_clean`: an amount stays a number
        if amount_minor is not None:
            body["amount_minor"] = amount_minor
        if note is not None:
            body["note"] = note
        return self._req("POST", "/api/v1/billing/top-up-requests", json=body)  # type: ignore[no-any-return]

    # /BIL6 statements

    # TV3 no surprises
    def set_spend_cap(self, cap_minor: int | None) -> JSON:
        """Owner: the monthly cap on usage charges, in minor units (paise, cents); None removes it.
        Alerts at 80% and at the cap; past it answers use a slower model or pause until the month
        ends."""
        return self._req("PUT", "/api/v1/billing/spend-cap", json={"cap_minor": cap_minor})  # type: ignore[no-any-return]

    def preview_pricing(self, plan_id: str, **fields: Any) -> JSON:
        """Admin: what saving these plan prices would do (same body as `set_plan_pricing`), without
        saving: who pays more, who pays less, and the earliest date a raise may start."""
        return self._req(
            "POST", f"/api/v1/admin/billing/plans/{plan_id}/pricing/preview", json=fields
        )  # type: ignore[no-any-return]

    def cancel_scheduled_pricing(self, plan_id: str) -> JSON:
        """Admin: call off a price change that has been announced but has not started."""
        return self._req("DELETE", f"/api/v1/admin/billing/plans/{plan_id}/pricing/scheduled")  # type: ignore[no-any-return]

    def cancel_pending_change(self, tenant_id: str) -> JSON:
        """Admin: call off a raise for one business that is waiting out its notice period."""
        return self._req("DELETE", f"/api/v1/admin/billing/accounts/{tenant_id}/pending-change")  # type: ignore[no-any-return]

    # /TV3 no surprises

    # TV4 value report
    def value_settings(self) -> JSON:
        """The values behind the value report (or the suggestions for this kind of business until
        the owner saves their own). Workspace sign-in or tenant key."""
        return self._req("GET", "/api/v1/value/settings")  # type: ignore[no-any-return]

    def update_value_settings(
        self,
        *,
        lead_value_minor: int,
        handoff_value_minor: int,
        staff_hour_minor: int,
        minutes_per_answer: int,
        email_enabled: bool = True,
        industry_defaults: str | None = None,
    ) -> JSON:
        """Owner: save the four values (paise or cents; minutes 0 to 120) and the e-mail switch."""
        body: dict[str, Any] = {
            "lead_value_minor": lead_value_minor,
            "handoff_value_minor": handoff_value_minor,
            "staff_hour_minor": staff_hour_minor,
            "minutes_per_answer": minutes_per_answer,
            "email_enabled": email_enabled,
        }
        if industry_defaults is not None:
            body["industry_defaults"] = industry_defaults
        return self._req("PUT", "/api/v1/value/settings", json=body)  # type: ignore[no-any-return]

    def value_report(self, month: str | None = None, *, format: str = "json") -> Any:
        """A month's numbers with the estimated value and its formula (`month` is `YYYY-MM`,
        default: this month so far), as "json" (a dict) or "html" (the printable page, text)."""
        params = _clean({"month": month})
        if format == "json":
            return self._req("GET", "/api/v1/value/report", params=params)
        return self._text("GET", "/api/v1/value/report", params={**params, "format": format})

    def value_reports(self) -> JSON:
        """The frozen months, newest first: `{items: [{month, total_minor, currency}]}`."""
        return self._req("GET", "/api/v1/value/reports")  # type: ignore[no-any-return]

    def admin_value_report(
        self, tenant_id: str, month: str | None = None, *, format: str = "json"
    ) -> Any:
        """Admin: one business's value report, as its owner sees it."""
        params = _clean({"month": month})
        if format == "json":
            return self._req(
                "GET", f"/api/v1/admin/tenants/{tenant_id}/value-report", params=params
            )
        return self._text(
            "GET",
            f"/api/v1/admin/tenants/{tenant_id}/value-report",
            params={**params, "format": format},
        )

    # /TV4 value report

    # BIZ3 home and articles
    def home(self, **params: Any) -> JSON:
        """The business dashboard's summary: assistant, website, today's numbers. Workspace
        sign-in or tenant key. params: corpus_id (default: the workspace's main knowledge base)."""
        return self._req("GET", "/api/v1/home", params=_clean(params))  # type: ignore[no-any-return]

    def create_article(self, corpus_id: str, kind: str, title: str, body: str) -> JSON:
        """Write an article (kind="article") or an FAQ answer (kind="faq": title is the
        question). The assistant learns it in a moment; it counts as one document."""
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/articles",
            json={"kind": kind, "title": title, "body": body},
        )

    def get_article(self, corpus_id: str, document_id: str) -> JSON:
        """An article or FAQ entry with its text as written (by document id or article id)."""
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/articles/{document_id}"
        )

    def update_article(
        self, corpus_id: str, document_id: str, kind: str, title: str, body: str
    ) -> JSON:
        """Change an article's text; the assistant learns the new version. `kind` cannot change."""
        return self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/corpora/{corpus_id}/articles/{document_id}",
            json={"kind": kind, "title": title, "body": body},
        )

    # /BIZ3 home and articles

    # BIZ1 website learning
    def website(self, corpus_id: str) -> JSON:
        """What the assistant learns from the business's own website: settings, the plan's
        limits, the sites and how far each is, and allowed sites not read yet."""
        return self._req("GET", f"/api/v1/corpora/{corpus_id}/website")  # type: ignore[no-any-return]

    def update_website(
        self,
        corpus_id: str,
        *,
        enabled: bool | None = None,
        exclude_patterns: list[str] | None = None,
        refresh_mode: str | None = None,  # BIZ2: off, monthly, weekly, daily, 6h, hourly
        adaptive: bool | None = None,  # BIZ2
    ) -> JSON:
        """Owner: switch learning on or off, choose paths to leave out (`/blog/*`) and how
        often learned pages are checked (`refresh_mode`; `adaptive` checks busy pages more
        often)."""
        body = _present(
            {
                "enabled": enabled,
                "exclude_patterns": exclude_patterns,
                "refresh_mode": refresh_mode,
                "adaptive": adaptive,
            }
        )
        return self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/website", json=body
        )

    def learn_website(self, corpus_id: str, origin: str) -> JSON:
        """Start reading one of the knowledge base's allowed sites (an origin of one of its
        active widget keys). Returns the site (202)."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/website/learn", json={"origin": origin}
        )

    def reread_website(self, corpus_id: str) -> JSON:
        """Read every site again. Once every 10 minutes (429 `website_reread_too_soon`)."""
        return self._req("POST", f"/api/v1/corpora/{corpus_id}/website/reread")  # type: ignore[no-any-return]

    def remove_website_source(self, corpus_id: str, source_id: str) -> None:
        """Owner: stop learning a site; its pages leave the answers (they keep their history)."""
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/website/sources/{source_id}")

    def website_pages(self, corpus_id: str, **params: Any) -> JSON:
        """Every page found on the site and what became of it. params: status (learned,
        waiting, skipped, excluded, failed, removed), source_id, q, offset, limit (<= 100)."""
        return self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/website/pages", params=_clean(params)
        )

    def reread_website_page(self, corpus_id: str, page_id: str) -> JSON:
        """Read one page again now (202)."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/website/pages/{page_id}/reread"
        )

    def update_website_page(self, corpus_id: str, page_id: str, excluded: bool) -> JSON:
        """Owner: leave a page out of the answers (`excluded=True`) or put it back."""
        return self._req(  # type: ignore[no-any-return]
            "PATCH",
            f"/api/v1/corpora/{corpus_id}/website/pages/{page_id}",
            json={"excluded": excluded},
        )

    # /BIZ1 website learning

    # BIZ2 freshness
    def create_learn_key(self, corpus_id: str) -> JSON:
        """Owner: make the push link a website calls when a page changes. Returns `key`,
        `push_url` and `hint` once; making a new one replaces (and stops) the old one."""
        return self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/website/learn-key"
        )

    def delete_learn_key(self, corpus_id: str) -> None:
        """Owner: remove the push link; it stops working at once."""
        self._req("DELETE", f"/api/v1/corpora/{corpus_id}/website/learn-key")

    # /BIZ2 freshness

    # BIZ4 avatar
    def upload_avatar(
        self,
        corpus_id: str,
        file: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
    ) -> JSON:
        """Owner: upload the assistant's picture (PNG, JPEG or WebP, at least 64 px). The engine
        re-saves it as a 256 px PNG and returns `{"avatar_url": "/api/v1/public/avatars/<id>.png"}`;
        put that in the assistant's `identity.avatar_url` and publish."""
        name, content = _file_part(file, filename)
        return self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/assistant/avatar",
            files={"file": (name, content)},
        )

    # /BIZ4 avatar

    # --- engine settings and branding ----------------------------------------
    def get_settings(self) -> JSON:
        """Admin: the sign-up mode and branding, with who changed them last."""
        return self._req("GET", "/api/v1/admin/settings")  # type: ignore[no-any-return]

    def update_settings(self, **changes: Any) -> JSON:
        """Admin: signup_mode ("open" | "approval" | "closed") and/or branding={...}.

        Only what you pass changes; a value of None resets it to its default.
        """
        return self._req("PATCH", "/api/v1/admin/settings", json=changes)  # type: ignore[no-any-return]

    def public_branding(self) -> JSON:
        """Product name, logo, colour, contact text, sign-up mode. Needs no key."""
        return self._req("GET", "/api/v1/public/branding")  # type: ignore[no-any-return]

    # --- e-mail (SMTP) -------------------------------------------------------
    def smtp_status(self) -> JSON:
        """Admin: the e-mail provider in use and the SMTP settings (never the password)."""
        return self._req("GET", "/api/v1/admin/smtp")  # type: ignore[no-any-return]

    def send_test_email(self, to: str) -> JSON:
        """Admin: send a test e-mail now. 503 when e-mail is not configured, 502 with
        `smtp_code` and `error` when the SMTP server refuses it."""
        return self._req("POST", "/api/v1/admin/smtp/test", json={"to": to})  # type: ignore[no-any-return]


class AsyncCobRag:
    """Asynchronous client — the same methods, awaitable."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncCobRag":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._http.request(method, path, **kwargs)
        _raise_for(response)
        return None if response.status_code == 204 else response.json()

    async def _text(self, method: str, path: str, **kwargs: Any) -> str:
        response = await self._http.request(method, path, **kwargs)
        _raise_for(response)
        return response.text

    async def whoami(self) -> JSON:
        """Which kind of key this is, what it belongs to, and `auth` / `role` / `user`."""
        return await self._req("GET", "/api/v1/whoami")  # type: ignore[no-any-return]

    async def info(self) -> JSON:
        return await self._req("GET", "/api/v1/info")  # type: ignore[no-any-return]

    async def fast_model_benchmarks(self) -> JSON:
        return await self._req("GET", "/api/v1/models/fast-benchmarks")  # type: ignore[no-any-return]

    async def answer_model_benchmarks(self) -> JSON:
        return await self._req("GET", "/api/v1/models/answer-benchmarks")  # type: ignore[no-any-return]

    async def benchmark_summary(self) -> JSON:
        return await self._req("GET", "/api/v1/models/benchmark-summary")  # type: ignore[no-any-return]

    async def own_tenant(self) -> JSON:
        return await self._req("GET", "/api/v1/tenants/me")  # type: ignore[no-any-return]

    async def list_jobs(self, **params: Any) -> JSON:
        """params: status, corpus_id, offset, limit. Returns {items, total, ...}."""
        return await self._req("GET", "/api/v1/ingest/jobs", params=_clean(params))  # type: ignore[no-any-return]

    async def retry_ingest_job(self, job_id: str) -> JSON:
        """Run a failed upload again from its stored file, as a new job (see the sync client)."""
        return await self._req("POST", f"/api/v1/ingest/jobs/{job_id}/retry")  # type: ignore[no-any-return]

    async def list_documents(self, **params: Any) -> JSON:
        """params: corpus_id (admin/tenant keys), search, offset, limit."""
        return await self._req("GET", "/api/v1/documents", params=_clean(params))  # type: ignore[no-any-return]

    async def get_document(self, document_id: str, **params: Any) -> JSON:
        """params: corpus_id, version_id, chunk_offset, chunk_limit."""
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/documents/{document_id}", params=_clean(params)
        )

    async def health(self) -> JSON:
        return await self._req("GET", "/api/v1/health")  # type: ignore[no-any-return]

    async def health_ready(self) -> JSON:
        """Readiness: 200 when Postgres, Redis and Qdrant answer; the engine raises on 503."""
        return await self._req("GET", "/api/v1/health/ready")  # type: ignore[no-any-return]

    async def create_tenant(self, name: str, **fields: Any) -> JSON:
        return await self._req("POST", "/api/v1/admin/tenants", json={"name": name, **fields})  # type: ignore[no-any-return]

    async def list_tenants(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        is_operator: bool | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[JSON]:
        params = _clean(
            {
                "status": status,
                "search": search,
                "is_operator": None if is_operator is None else str(is_operator).lower(),
                "offset": offset,
                "limit": limit,
            }
        )
        return (await self._req("GET", "/api/v1/admin/tenants", params=params))["items"]  # type: ignore[no-any-return]

    async def activate_tenant(
        self, tenant_id: str, plan_id: str | None = None, note: str | None = None
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/admin/tenants/{tenant_id}/activate",
            json=_clean({"plan_id": plan_id, "note": note}),
        )

    async def list_plans(self, *, include_inactive: bool = True) -> list[JSON]:
        params = {"include_inactive": str(include_inactive).lower()}
        return (await self._req("GET", "/api/v1/admin/plans", params=params))["items"]  # type: ignore[no-any-return]

    async def create_plan(self, name: str, **fields: Any) -> JSON:
        return await self._req("POST", "/api/v1/admin/plans", json={"name": name, **fields})  # type: ignore[no-any-return]

    async def get_plan(self, plan_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/plans/{plan_id}")  # type: ignore[no-any-return]

    async def update_plan(self, plan_id: str, **fields: Any) -> JSON:
        return await self._req("PATCH", f"/api/v1/admin/plans/{plan_id}", json=fields)  # type: ignore[no-any-return]

    async def delete_plan(self, plan_id: str) -> None:
        await self._req("DELETE", f"/api/v1/admin/plans/{plan_id}")

    async def set_tenant_plan(self, tenant_id: str, plan_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/tenants/{tenant_id}/plan", json={"plan_id": plan_id}
        )

    async def tenant_plan(self, tenant_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/tenants/{tenant_id}/plan")  # type: ignore[no-any-return]

    async def my_plan(self) -> JSON:
        return await self._req("GET", "/api/v1/tenants/me/plan")  # type: ignore[no-any-return]

    # --- pipeline profiles (admin key; PIP1) -------------------------------------
    async def list_pipeline_profiles(self) -> JSON:
        return await self._req("GET", "/api/v1/admin/pipelines")  # type: ignore[no-any-return]

    async def create_pipeline_profile(
        self, name: str, label: str, *, description: str | None = None, config: JSON | None = None
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            "/api/v1/admin/pipelines",
            json=_present(
                {"name": name, "label": label, "description": description, "config": config or {}}
            ),
        )

    async def get_pipeline_profile(self, profile_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/pipelines/{profile_id}")  # type: ignore[no-any-return]

    async def update_pipeline_profile(self, profile_id: str, **fields: Any) -> JSON:
        return await self._req("PATCH", f"/api/v1/admin/pipelines/{profile_id}", json=fields)  # type: ignore[no-any-return]

    async def delete_pipeline_profile(self, profile_id: str) -> None:
        await self._req("DELETE", f"/api/v1/admin/pipelines/{profile_id}")

    async def get_tenant_pipeline(self, tenant_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/pipelines/tenants/{tenant_id}")  # type: ignore[no-any-return]

    async def set_tenant_pipeline(self, tenant_id: str, profile_id: str | None) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/admin/pipelines/tenants/{tenant_id}",
            json={"profile_id": profile_id},
        )

    # --- fair use (PIP3) -----------------------------------------------------------
    async def list_fair_use(
        self,
        *,
        q: str | None = None,
        state: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET",
            "/api/v1/admin/fair-use",
            params=_present({"q": q, "state": state, "offset": offset, "limit": limit}),
        )

    async def get_fair_use(self, tenant_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/fair-use/tenants/{tenant_id}")  # type: ignore[no-any-return]

    async def set_fair_use(
        self,
        tenant_id: str,
        *,
        limits: JSON | None = None,
        policy: JSON | None = None,
        clear: list[str] | None = None,
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/admin/fair-use/tenants/{tenant_id}",
            json={"limits": limits or {}, "policy": policy or {}, "clear": clear or []},
        )

    async def fair_use(self) -> JSON:
        return await self._req("GET", "/api/v1/fair-use")  # type: ignore[no-any-return]

    # --- handoffs (conversation C5) ----------------------------------------------
    # A handoff is a visitor offered (or tapping) a way to reach a person. The list needs `read`;
    # one handoff carries the linked lead's phone and e-mail, so it and the status change need
    # `write`. A workspace member's sign-in may update (nothing here is owner-only).
    async def list_handoffs(
        self,
        *,
        corpus_id: str | None = None,
        status: str | None = None,
        reason: str | None = None,
        channel: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """Handoffs the key may see, newest first. `status` is `new` or `handled`."""
        params = {
            k: v
            for k, v in {
                "corpus_id": corpus_id,
                "status": status,
                "reason": reason,
                "channel": channel,
                "from": date_from,
                "to": date_to,
            }.items()
            if v is not None
        }
        params.update({"offset": str(offset), "limit": str(limit)})
        return await self._req("GET", "/api/v1/handoffs", params=params)  # type: ignore[no-any-return]

    async def get_handoff(self, handoff_id: str) -> JSON:
        """One handoff with its summary, the masked chat and the lead of a callback request."""
        return await self._req("GET", f"/api/v1/handoffs/{handoff_id}")  # type: ignore[no-any-return]

    # TV2 reference
    async def lookup_reference(self, ref: str) -> JSON:
        """Find the chat a visitor quoted a reference for (see the sync client)."""
        return await self._req("GET", "/api/v1/handoffs/lookup", params={"ref": ref})  # type: ignore[no-any-return]

    # /TV2 reference
    async def update_handoff(self, handoff_id: str, status: str) -> JSON:
        """Mark a handoff `handled` (or `new` again)."""
        return await self._req("PATCH", f"/api/v1/handoffs/{handoff_id}", json={"status": status})  # type: ignore[no-any-return]

    # --- WhatsApp (TV6) ----------------------------------------------------------
    # A knowledge base's WhatsApp number and its chats. Reads need `read` (a chat's number and
    # messages need `write`); replying and the chat switches need `write`; settings, the brochure
    # list and disconnecting are owner-only for a signed-in member. The deployer connects a number
    # by hand with an admin key. Connecting by Meta's sign-up window is the dashboard's job.
    async def whatsapp(self, corpus_id: str) -> JSON:
        """The knowledge base's WhatsApp number (status, settings, who pays Meta; never a token)."""
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/whatsapp")  # type: ignore[no-any-return]

    async def set_whatsapp_manual(
        self,
        corpus_id: str,
        *,
        phone_number_id: str,
        waba_id: str,
        access_token: str,
        meta_billing: str,
    ) -> JSON:
        """Connect a number by hand (admin key): its id, its WABA id, a permanent token and who
        pays Meta (`business` or `deployer`). The token is checked first and never returned."""
        return await self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/corpora/{corpus_id}/whatsapp/manual",
            json={
                "phone_number_id": phone_number_id,
                "waba_id": waba_id,
                "access_token": access_token,
                "meta_billing": meta_billing,
            },
        )

    async def update_whatsapp(self, corpus_id: str, **fields: Any) -> JSON:
        """`enabled`, `human_pause_hours` (1 to 72) and `follow_up` (`enabled`, `template_name`,
        `language`, `after_hours`); only the fields given change."""
        return await self._req("PATCH", f"/api/v1/corpora/{corpus_id}/whatsapp", json=fields)  # type: ignore[no-any-return]

    async def set_whatsapp_documents(self, corpus_id: str, document_ids: list[str]) -> JSON:
        """The documents customers may be sent on WhatsApp (at most 20 PDFs, JPGs or PNGs)."""
        return await self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/corpora/{corpus_id}/whatsapp/documents",
            json={"document_ids": document_ids},
        )

    async def disconnect_whatsapp(self, corpus_id: str) -> None:
        """Disconnect the number: the token is deleted, the chats stay readable."""
        await self._req("DELETE", f"/api/v1/corpora/{corpus_id}/whatsapp")

    async def whatsapp_threads(
        self, corpus_id: str, *, unread: bool = False, offset: int = 0, limit: int = 50
    ) -> JSON:
        """The WhatsApp chats, most recent customer message first (names masked)."""
        params = {"offset": str(offset), "limit": str(limit)}
        if unread:
            params["unread"] = "true"
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/whatsapp/threads", params=params
        )

    async def whatsapp_thread(self, corpus_id: str, thread_id: str) -> JSON:
        """One chat: the number in full and the last 100 messages. Opening it marks it read."""
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}"
        )

    async def whatsapp_reply(self, corpus_id: str, thread_id: str, text: str) -> JSON:
        """Answer a customer (text, up to 4,096 characters, within 24 hours of their last
        message); the assistant then stays quiet in that chat."""
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}/reply",
            json={"text": text},
        )

    async def update_whatsapp_thread(
        self, corpus_id: str, thread_id: str, *, bot_paused: bool
    ) -> JSON:
        """Keep the assistant quiet in one chat (`True`), or let it answer again (`False`)."""
        return await self._req(  # type: ignore[no-any-return]
            "PATCH",
            f"/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}",
            json={"bot_paused": bot_paused},
        )

    # /WhatsApp (TV6)

    # --- leads and knowledge-base webhooks (conversation C4) --------------------
    # Lead reads, updates and the export need a `write` key (contact data is more than the
    # read-only agent default may see); deleting a lead and managing webhooks need `full`.
    # Delete, export and webhook changes are owner-only for a signed-in workspace member.
    async def list_leads(
        self,
        *,
        corpus_id: str | None = None,
        status: str | None = None,
        trigger: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        q: str | None = None,
        page_url: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> JSON:
        """Leads the key may see, newest first. `q` finds a phone number or e-mail exactly."""
        params = {
            k: v
            for k, v in {
                "corpus_id": corpus_id,
                "status": status,
                "trigger": trigger,
                "date_from": date_from,
                "date_to": date_to,
                "q": q,
                "page_url": page_url,
            }.items()
            if v is not None
        }
        params.update({"offset": str(offset), "limit": str(limit)})
        return await self._req("GET", "/api/v1/leads", params=params)  # type: ignore[no-any-return]

    async def get_lead(self, lead_id: str) -> JSON:
        """One lead with its summary, notes, consent record and chat transcript."""
        return await self._req("GET", f"/api/v1/leads/{lead_id}")  # type: ignore[no-any-return]

    async def update_lead(self, lead_id: str, **fields: Any) -> JSON:
        """fields: status, assignee_user_id (None unassigns), notes, qualification."""
        return await self._req("PATCH", f"/api/v1/leads/{lead_id}", json=fields)  # type: ignore[no-any-return]

    async def delete_lead(self, lead_id: str) -> None:
        """Erase a lead and the website chat it came from (owners only)."""
        await self._req("DELETE", f"/api/v1/leads/{lead_id}")

    async def export_leads(self, *, corpus_id: str | None = None, **filters: str) -> str:
        """The matching leads as CSV text (owners only; the export is audited)."""
        params = {k: v for k, v in {"corpus_id": corpus_id, **filters}.items() if v is not None}
        return await self._text("GET", "/api/v1/leads/export.csv", params=params)

    async def list_corpus_webhooks(self, corpus_id: str) -> list[JSON]:
        """The webhooks that receive this knowledge base's leads."""
        return (await self._req("GET", f"/api/v1/corpora/{corpus_id}/webhooks"))["webhooks"]  # type: ignore[no-any-return]

    async def create_corpus_webhook(
        self, corpus_id: str, url: str, events: list[str], secret: str
    ) -> JSON:
        """events: lead.created and/or handoff.requested. https and a public host only."""
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/webhooks",
            json={"url": url, "events": events, "secret": secret},
        )

    async def update_corpus_webhook(
        self, corpus_id: str, webhook_id: str, *, enabled: bool
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/webhooks/{webhook_id}", json={"enabled": enabled}
        )

    async def delete_corpus_webhook(self, corpus_id: str, webhook_id: str) -> None:
        await self._req("DELETE", f"/api/v1/corpora/{corpus_id}/webhooks/{webhook_id}")

    async def withdraw_lead_consent(self, token: str) -> JSON:
        """What the public withdrawal page does with the token from its link (no key needed)."""
        return await self._req("POST", "/api/v1/public/leads/withdraw", json={"token": token})  # type: ignore[no-any-return]

    # --- assistant configuration (conversation C2) -----------------------------
    # Reads need the `read` permission; draft edits, publish, restore and the on/off switch
    # need `write`. A workspace member's sign-in may edit drafts but not publish; a key is
    # never a member. Publishing is reversible: restore an older version.
    async def get_assistant(self, corpus_id: str) -> JSON:
        """The published assistant and the draft being edited (either may be null)."""
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/assistant")  # type: ignore[no-any-return]

    async def put_assistant_draft(
        self, corpus_id: str, config: JSON, *, base_revision: int | None = None
    ) -> JSON:
        """Replace the draft. `base_revision` is the draft_revision you edited (409
        `assistant_draft_conflict` if someone else saved since)."""
        body: JSON = {"config": config, **_present({"base_revision": base_revision})}
        return await self._req("PUT", f"/api/v1/corpora/{corpus_id}/assistant/draft", json=body)  # type: ignore[no-any-return]

    async def patch_assistant_draft(
        self, corpus_id: str, patch: JSON, *, base_revision: int | None = None
    ) -> JSON:
        """Merge a JSON merge patch (RFC 7396; null removes a field) into the draft."""
        body: JSON = {"patch": patch, **_present({"base_revision": base_revision})}
        return await self._req("PATCH", f"/api/v1/corpora/{corpus_id}/assistant/draft", json=body)  # type: ignore[no-any-return]

    async def assistant_draft_from_template(
        self,
        corpus_id: str,
        template: str,
        *,
        keep_identity: bool = True,
        base_revision: int | None = None,
    ) -> JSON:
        """Start the draft from a business template (see `assistant_templates`)."""
        body: JSON = {
            "template": template,
            "keep_identity": keep_identity,
            **_present({"base_revision": base_revision}),
        }
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/draft/from-template", json=body
        )

    async def discard_assistant_draft(self, corpus_id: str) -> None:
        """Throw the draft away; the published assistant and its history are untouched."""
        await self._req("DELETE", f"/api/v1/corpora/{corpus_id}/assistant/draft")

    async def publish_assistant(
        self, corpus_id: str, *, base_revision: int, note: str | None = None
    ) -> JSON:
        """Publish the draft as the next version (owner keys; 422 names what to fix)."""
        body: JSON = {"base_revision": base_revision, **_present({"note": note})}
        return await self._req("POST", f"/api/v1/corpora/{corpus_id}/assistant/publish", json=body)  # type: ignore[no-any-return]

    async def assistant_versions(self, corpus_id: str, *, offset: int = 0, limit: int = 20) -> JSON:
        """Published and archived versions, newest first: {items, total, offset, limit}."""
        params = {"offset": str(offset), "limit": str(limit)}
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/versions", params=params
        )

    async def assistant_version(self, corpus_id: str, version: int) -> JSON:
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/assistant/versions/{version}")  # type: ignore[no-any-return]

    async def restore_assistant_version(self, corpus_id: str, version: int) -> JSON:
        """Publish an older version again as a new version (history is never rewritten)."""
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/versions/{version}/restore"
        )

    async def set_assistant_enabled(self, corpus_id: str, enabled: bool) -> JSON:
        """Off = every surface answers exactly as before the assistant existed."""
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/enabled", json={"enabled": enabled}
        )

    async def assistant_analytics(
        self,
        corpus_id: str | None = None,
        *,
        from_: str | None = None,
        to: str | None = None,
    ) -> JSON:
        """Outcome analytics for one knowledge base, or for all the key can see (None)."""
        params = _clean({"from": from_, "to": to})
        if corpus_id is None:
            return await self._req("GET", "/api/v1/assistant/analytics", params=params)  # type: ignore[no-any-return]
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/analytics", params=params
        )

    async def compile_assistant(
        self, corpus_id: str, *, config: JSON | None = None, sample: JSON | None = None
    ) -> JSON:
        """The prompt the model would receive for a sample turn (no model call, nothing saved)."""
        body: JSON = _present({"config": config, "sample": sample})
        return await self._req("POST", f"/api/v1/corpora/{corpus_id}/assistant/compile", json=body)  # type: ignore[no-any-return]

    async def improve_assistant_rule(
        self, corpus_id: str, instruction: str, type: str, rule_id: str | None = None
    ) -> JSON:
        """Suggest a cleaner wording for a guidance rule (nothing is saved; spends the model,
        so it needs `write`)."""
        body: JSON = _present({"instruction": instruction, "type": type, "rule_id": rule_id})
        return await self._req(
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/rules/improve", json=body
        )  # type: ignore[no-any-return]

    async def assistant_templates(self) -> list[JSON]:
        """The eight business templates."""
        return await self._req("GET", "/api/v1/assistant/templates")  # type: ignore[no-any-return]

    async def assistant_template(self, key: str) -> JSON:
        """One template: its full config and ten test questions."""
        return await self._req("GET", f"/api/v1/assistant/templates/{key}")  # type: ignore[no-any-return]

    # --- guided assistant setup (conversation C8) ------------------------------------
    # Reads (`get_setup`, `setup_questions`, `setup_gaps`) need `read`; every other step needs
    # `write`. `apply_setup` publishes: a workspace member's sign-in cannot, a tenant key acts
    # as the owner and a corpus key with `write` can (the same as `publish_assistant`).
    async def start_setup(
        self, corpus_id: str, *, business_type: str | None = None, website_url: str | None = None
    ) -> JSON:
        """Start a guided setup for a knowledge base (older unfinished ones are abandoned)."""
        body: JSON = _present({"business_type": business_type, "website_url": website_url})
        return await self._req("POST", f"/api/v1/corpora/{corpus_id}/assistant/setup", json=body)  # type: ignore[no-any-return]

    async def get_setup(self, corpus_id: str, session_id: str) -> JSON:
        """One setup session: status, proposal, confirmed profile, answers, provenance."""
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}")  # type: ignore[no-any-return]

    async def propose_setup(
        self,
        corpus_id: str,
        session_id: str,
        pages: list[JSON],
        *,
        crawl_job_id: str | None = None,
    ) -> JSON:
        """Send crawled pages (`{url, title, text}`); returns the proposal."""
        body: JSON = _present({"pages": pages, "crawl_job_id": crawl_job_id})
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/propose", json=body
        )

    async def setup_questions(self, corpus_id: str, session_id: str) -> JSON:
        """The interview questions (prefilled) and the business profile to confirm."""
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/questions"
        )

    async def answer_setup(
        self,
        corpus_id: str,
        session_id: str,
        answers: JSON,
        *,
        business_type: str | None = None,
        profile: JSON | None = None,
        partial: bool = False,
    ) -> JSON:
        """Save the answers and the confirmed profile (name, phone, WhatsApp, e-mail, hours).
        `partial=True` saves progress: required questions may still be open (generate checks)."""
        body: JSON = _present(
            {"answers": answers, "business_type": business_type, "profile": profile}
        )
        if partial:
            body["partial"] = True
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/answers", json=body
        )

    async def generate_setup(
        self,
        corpus_id: str,
        session_id: str,
        *,
        wording: str = "ai",
        base_revision: int | None = None,
    ) -> JSON:
        """Write the draft assistant; `wording="template"` makes no model call."""
        body: JSON = _present({"wording": wording, "base_revision": base_revision})
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/generate", json=body
        )

    async def run_setup_tests(self, corpus_id: str, session_id: str) -> JSON:
        """Create the setup's test set and start its run; returns golden_set_id / eval_run_id."""
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/tests"
        )

    async def setup_gaps(self, corpus_id: str, session_id: str) -> JSON:
        """Questions the documents could not answer (empty while the run is unfinished)."""
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/gaps"
        )

    async def apply_setup(
        self, corpus_id: str, session_id: str, base_revision: int, *, note: str | None = None
    ) -> JSON:
        """Publish the generated draft (`base_revision` is the draft revision you reviewed)."""
        body: JSON = _present({"base_revision": base_revision, "note": note})
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/setup/{session_id}/apply", json=body
        )

    # --- golden sets, evaluation runs and the review inbox (conversation C7) ----------
    # Reads, estimates, comparisons need `read`; creating or changing sets, criteria, runs and
    # ratings need `write` (a full run spends model money and monthly questions); deleting a
    # set needs `full`; the judge settings need `write` from an owner (never a member).
    async def list_golden_sets(self, corpus_id: str) -> list[JSON]:
        """The knowledge base's test sets, each with its question count and last run."""
        data = await self._req("GET", f"/api/v1/corpora/{corpus_id}/golden-sets")
        return data["items"]  # type: ignore[no-any-return]

    async def create_golden_set(
        self, corpus_id: str, name: str, pairs: list[JSON] | None = None
    ) -> JSON:
        """A set of questions; each pair has `query` and an expected_answer and/or an
        expected_behaviour (answer, decline, handoff, lead, smalltalk, no_answer)."""
        body: JSON = {"name": name, "pairs": pairs or []}
        return await self._req("POST", f"/api/v1/corpora/{corpus_id}/golden-sets", json=body)  # type: ignore[no-any-return]

    async def golden_set_from_template(self, corpus_id: str, template: str | None = None) -> JSON:
        """A set from a business template's test questions (default: the assistant's own)."""
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/golden-sets/from-template",
            json={"template": template},
        )

    async def get_golden_set(self, corpus_id: str, set_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/golden-sets/{set_id}")  # type: ignore[no-any-return]

    async def rename_golden_set(self, corpus_id: str, set_id: str, name: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/golden-sets/{set_id}", json={"name": name}
        )

    async def delete_golden_set(self, corpus_id: str, set_id: str) -> None:
        """Delete a set with its questions (never the default set). Needs `full`."""
        await self._req("DELETE", f"/api/v1/corpora/{corpus_id}/golden-sets/{set_id}")

    async def add_golden_pairs(self, corpus_id: str, set_id: str, pairs: list[JSON]) -> list[JSON]:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/golden-sets/{set_id}/pairs", json={"pairs": pairs}
        )

    async def list_eval_criteria(self, corpus_id: str) -> list[JSON]:
        """Your own plain-language rules a judge model scores every answer against."""
        data = await self._req("GET", f"/api/v1/corpora/{corpus_id}/eval/criteria")
        return data["items"]  # type: ignore[no-any-return]

    async def create_eval_criterion(
        self, corpus_id: str, name: str, definition: str, threshold: float = 0.7
    ) -> JSON:
        body: JSON = {"name": name, "definition": definition, "threshold": threshold}
        return await self._req("POST", f"/api/v1/corpora/{corpus_id}/eval/criteria", json=body)  # type: ignore[no-any-return]

    async def update_eval_criterion(self, corpus_id: str, criterion_id: str, **fields: Any) -> JSON:
        """fields: definition, threshold, enabled."""
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}", json=fields
        )

    async def delete_eval_criterion(self, corpus_id: str, criterion_id: str) -> None:
        await self._req("DELETE", f"/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}")

    async def get_eval_settings(self, corpus_id: str) -> JSON:
        """The judge model (never its key) and the pass thresholds."""
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/eval/settings")  # type: ignore[no-any-return]

    async def set_eval_settings(self, corpus_id: str, **fields: Any) -> JSON:
        """fields: judge_provider, judge_model, judge_api_key, judge_base_url, thresholds."""
        return await self._req("PUT", f"/api/v1/corpora/{corpus_id}/eval/settings", json=fields)  # type: ignore[no-any-return]

    async def estimate_eval_run(
        self, corpus_id: str, set_id: str, *, mode: str = "full", settings: JSON | None = None
    ) -> JSON:
        """What a run would cost (an estimate from token assumptions and the price table)."""
        body: JSON = {"set_id": set_id, "mode": mode, **_present({"settings": settings})}
        return await self._req("POST", f"/api/v1/corpora/{corpus_id}/eval/estimate", json=body)  # type: ignore[no-any-return]

    async def start_eval_run(
        self,
        corpus_id: str,
        set_id: str,
        *,
        mode: str = "fast",
        name: str | None = None,
        settings: JSON | None = None,
        confirm_cost_usd: float | None = None,
    ) -> JSON:
        """mode "fast": behaviour checks only, no answers. mode "full": fresh answers, scores
        and criteria; send the estimate's total_usd as confirm_cost_usd (409
        `eval_cost_unconfirmed` otherwise). `settings` is the experiment: which assistant
        version, retrieval overrides, answer model."""
        body: JSON = {
            "set_id": set_id,
            "mode": mode,
            **_present({"name": name, "settings": settings, "confirm_cost_usd": confirm_cost_usd}),
        }
        return await self._req("POST", f"/api/v1/corpora/{corpus_id}/eval/runs", json=body)  # type: ignore[no-any-return]

    async def list_eval_runs(
        self, corpus_id: str, set_id: str | None = None, kind: str | None = None
    ) -> list[JSON]:
        params = _clean({"set_id": set_id, "kind": kind})
        data = await self._req(
            "GET", f"/api/v1/corpora/{corpus_id}/eval/runs", params=params or None
        )
        return data["items"]  # type: ignore[no-any-return]

    async def get_eval_run(self, corpus_id: str, run_id: str) -> JSON:
        """A run with its metrics (mean, 95% interval, pass rate), tags and warnings."""
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/eval/runs/{run_id}")  # type: ignore[no-any-return]

    async def list_eval_samples(
        self,
        corpus_id: str,
        run_id: str,
        *,
        failed_only: bool = False,
        metric: str | None = None,
        below: float | None = None,
        tag: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        """One row per question: answer, scores, the judge's reasons, behaviour checks."""
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if failed_only:
            params["failed_only"] = "true"
        params.update(_present({"metric": metric, "below": below, "tag": tag}))
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/eval/runs/{run_id}/samples", params=_clean(params)
        )

    async def rate_eval_sample(self, corpus_id: str, sample_id: str, rating: str | None) -> JSON:
        """rating: good | acceptable | poor (null clears it)."""
        return await self._req(  # type: ignore[no-any-return]
            "PATCH",
            f"/api/v1/corpora/{corpus_id}/eval/samples/{sample_id}",
            json={"rating": rating},
        )

    async def compare_eval_runs(self, corpus_id: str, run_ids: list[str]) -> JSON:
        """Two to four completed runs of one set; the first is the baseline."""
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/eval/compare", json={"run_ids": run_ids}
        )

    async def review_items(
        self,
        corpus_id: str,
        kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[JSON]:
        """Questions visitors could not get answered, grouped, most asked first."""
        params = _clean({"kind": kind, "from": since, "to": until})
        data = await self._req(
            "GET", f"/api/v1/corpora/{corpus_id}/assistant/review", params=params or None
        )
        return data["items"]  # type: ignore[no-any-return]

    async def review_action(
        self, corpus_id: str, item_key: str, action: str, **payload: Any
    ) -> JSON:
        """action: dismissed | added_document | added_answer | added_rule. payload:
        answer={topic_name, question, answer, language}, rule={instruction, type},
        base_revision (the draft you edited; 0 when there is none)."""
        body: JSON = {"item_key": item_key, "action": action, **payload}
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/assistant/review/actions", json=body
        )

    async def suspend_tenant(
        self, tenant_id: str, confirm_name: str, reason: str | None = None
    ) -> JSON:
        body = {"confirm_name": confirm_name, **_clean({"reason": reason})}
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/tenants/{tenant_id}/suspend", json=body
        )

    async def reactivate_tenant(self, tenant_id: str) -> JSON:
        return await self._req("POST", f"/api/v1/admin/tenants/{tenant_id}/reactivate")  # type: ignore[no-any-return]

    async def decline_tenant(self, tenant_id: str, reason: str | None = None) -> None:
        await self._req(
            "POST", f"/api/v1/admin/tenants/{tenant_id}/decline", json=_clean({"reason": reason})
        )

    async def delete_tenant(
        self, tenant_id: str, confirm_name: str, *, confirm_operator_workspace: bool = False
    ) -> None:
        params = {"confirm_name": confirm_name}
        if confirm_operator_workspace:
            params["confirm_operator_workspace"] = "true"
        await self._req("DELETE", f"/api/v1/admin/tenants/{tenant_id}", params=params)

    async def team(self) -> JSON:
        return await self._req("GET", "/api/v1/tenants/me/members")  # type: ignore[no-any-return]

    async def invite(self, email: str, role: str = "member") -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", "/api/v1/tenants/me/members", json={"email": email, "role": role}
        )

    async def revoke_invitation(self, invitation_id: str) -> None:
        await self._req("DELETE", f"/api/v1/tenants/me/invitations/{invitation_id}")

    async def resend_invitation(self, invitation_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/tenants/me/invitations/{invitation_id}/resend"
        )

    async def set_member_role(self, user_id: str, role: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/tenants/me/members/{user_id}", json={"role": role}
        )

    async def remove_member(self, user_id: str) -> None:
        await self._req("DELETE", f"/api/v1/tenants/me/members/{user_id}")

    async def tenant_team(self, tenant_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/tenants/{tenant_id}/members")  # type: ignore[no-any-return]

    async def invite_to_tenant(self, tenant_id: str, email: str, role: str = "owner") -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/admin/tenants/{tenant_id}/invitations",
            json={"email": email, "role": role},
        )

    async def revoke_tenant_invitation(self, tenant_id: str, invitation_id: str) -> None:
        await self._req("DELETE", f"/api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}")

    async def resend_tenant_invitation(self, tenant_id: str, invitation_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}/resend"
        )

    async def set_tenant_member_role(self, tenant_id: str, user_id: str, role: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/admin/tenants/{tenant_id}/members/{user_id}", json={"role": role}
        )

    async def remove_tenant_member(self, tenant_id: str, user_id: str) -> None:
        await self._req("DELETE", f"/api/v1/admin/tenants/{tenant_id}/members/{user_id}")

    async def restore_operator_workspace(self) -> JSON:
        return await self._req("POST", "/api/v1/admin/operator-workspace/restore")  # type: ignore[no-any-return]

    async def audit_log(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        actor: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        params = _clean(
            {
                "tenant_id": tenant_id,
                "action": action,
                "actor": actor,
                "start": start,
                "end": end,
                "offset": offset,
                "limit": limit,
            }
        )
        return await self._req("GET", "/api/v1/admin/audit", params=params)  # type: ignore[no-any-return]

    async def create_corpus(
        self, name: str, *, tenant_id: str | None = None, **fields: Any
    ) -> JSON:
        """tenant_id is required with the admin key; a tenant key may omit it."""
        body = {"name": name, **_clean({"tenant_id": tenant_id}), **fields}
        return await self._req("POST", "/api/v1/corpora", json=body)  # type: ignore[no-any-return]

    async def list_corpora(
        self, *, offset: int = 0, limit: int = 100, tenant_id: str | None = None
    ) -> list[JSON]:
        params = _clean({"offset": offset, "limit": limit, "tenant_id": tenant_id})
        return (await self._req("GET", "/api/v1/corpora", params=params))["items"]  # type: ignore[no-any-return]

    async def get_corpus(self, corpus_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}")  # type: ignore[no-any-return]

    async def update_corpus(self, corpus_id: str, **fields: Any) -> JSON:
        return await self._req("PUT", f"/api/v1/corpora/{corpus_id}", json=fields)  # type: ignore[no-any-return]

    async def delete_corpus(self, corpus_id: str, confirm_name: str | None = None) -> None:
        await self._req(
            "DELETE", f"/api/v1/corpora/{corpus_id}", params=_clean({"confirm_name": confirm_name})
        )

    async def corpus_delete_preview(self, corpus_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/delete-preview")  # type: ignore[no-any-return]

    async def list_corpus_keys(self, corpus_id: str) -> list[JSON]:
        return (await self._req("GET", f"/api/v1/corpora/{corpus_id}/keys"))["keys"]  # type: ignore[no-any-return]

    async def create_corpus_key(self, corpus_id: str, name: str, permission: str = "read") -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/keys",
            json={"name": name, "permission": permission},
        )

    async def update_corpus_key(
        self,
        corpus_id: str,
        key_id: str,
        *,
        name: str | None = None,
        permission: str | None = None,
    ) -> JSON:
        body = _clean({"name": name, "permission": permission})
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/keys/{key_id}", json=body
        )

    async def rotate_corpus_named_key(self, corpus_id: str, key_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/keys/{key_id}/rotate"
        )

    async def revoke_corpus_key(self, corpus_id: str, key_id: str) -> None:
        await self._req("DELETE", f"/api/v1/corpora/{corpus_id}/keys/{key_id}")

    async def list_my_keys(self) -> list[JSON]:
        return (await self._req("GET", "/api/v1/tenants/me/keys"))["keys"]  # type: ignore[no-any-return]

    async def create_my_key(self, name: str, permission: str = "read") -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", "/api/v1/tenants/me/keys", json={"name": name, "permission": permission}
        )

    async def update_my_key(
        self, key_id: str, *, name: str | None = None, permission: str | None = None
    ) -> JSON:
        body = _clean({"name": name, "permission": permission})
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/tenants/me/keys/{key_id}", json=body
        )

    async def rotate_my_named_key(self, key_id: str) -> JSON:
        return await self._req("POST", f"/api/v1/tenants/me/keys/{key_id}/rotate")  # type: ignore[no-any-return]

    async def revoke_my_key(self, key_id: str) -> None:
        await self._req("DELETE", f"/api/v1/tenants/me/keys/{key_id}")

    async def list_tenant_keys(self, tenant_id: str) -> list[JSON]:
        return (await self._req("GET", f"/api/v1/admin/tenants/{tenant_id}/keys"))["keys"]  # type: ignore[no-any-return]

    async def create_tenant_key(self, tenant_id: str, name: str, permission: str = "read") -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/admin/tenants/{tenant_id}/keys",
            json={"name": name, "permission": permission},
        )

    async def update_tenant_key(
        self,
        tenant_id: str,
        key_id: str,
        *,
        name: str | None = None,
        permission: str | None = None,
    ) -> JSON:
        body = _clean({"name": name, "permission": permission})
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/admin/tenants/{tenant_id}/keys/{key_id}", json=body
        )

    async def rotate_tenant_named_key(self, tenant_id: str, key_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/tenants/{tenant_id}/keys/{key_id}/rotate"
        )

    async def revoke_tenant_key(self, tenant_id: str, key_id: str) -> None:
        await self._req("DELETE", f"/api/v1/admin/tenants/{tenant_id}/keys/{key_id}")

    async def delete_document(
        self, document_id: str, confirm_name: str | None = None, **params: Any
    ) -> None:
        await self._req(
            "DELETE",
            f"/api/v1/documents/{document_id}",
            params=_clean({**params, "confirm_name": confirm_name}),
        )

    async def document_delete_preview(self, document_id: str, **params: Any) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/documents/{document_id}/delete-preview", params=_clean(params)
        )

    async def tenant_delete_preview(self, tenant_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/tenants/{tenant_id}/delete-preview")  # type: ignore[no-any-return]

    async def ingest(
        self,
        file: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
        corpus_id: str | None = None,
        metadata: JSON | None = None,
        mode: str = "auto",
        auto_confirm: bool = False,
        wait: bool = False,
        poll_interval: float = 2.0,
        wait_timeout: float = 600.0,
    ) -> JSON:
        import asyncio

        name, content = _file_part(file, filename)
        # mode: auto (hold near-duplicates for review) | new | replace | version
        data: dict[str, str] = {"mode": mode}
        if auto_confirm:
            data["auto_confirm"] = "true"
        if corpus_id:
            data["corpus_id"] = corpus_id
        if metadata:
            data["metadata"] = json.dumps(metadata)
        job: JSON = await self._req(
            "POST", "/api/v1/ingest", files={"file": (name, content)}, data=data
        )
        if not wait:
            return job
        deadline = time.monotonic() + wait_timeout
        while True:
            status: JSON = await self._req("GET", f"/api/v1/ingest/jobs/{job['job_id']}")
            if status.get("status") in _TERMINAL:
                return status
            if time.monotonic() > deadline:
                raise TimeoutError(f"ingest job {job['job_id']} still {status.get('status')!r}")
            await asyncio.sleep(poll_interval)

    async def ingest_records(
        self,
        records: list[JSON],
        *,
        corpus_id: str | None = None,
        wait: bool = False,
        poll_interval: float = 1.0,
        wait_timeout: float = 600.0,
    ) -> JSON:
        import asyncio

        body: JSON = {"records": records}
        if corpus_id:
            body["corpus_id"] = corpus_id
        job: JSON = await self._req("POST", "/api/v1/ingest/records", json=body)
        if not wait:
            return job
        deadline = time.monotonic() + wait_timeout
        while True:
            status: JSON = await self._req("GET", f"/api/v1/ingest/jobs/{job['job_id']}")
            if status.get("status") in _TERMINAL:
                return status
            if time.monotonic() > deadline:
                raise TimeoutError(f"ingest job {job['job_id']} still {status.get('status')!r}")
            await asyncio.sleep(poll_interval)

    async def query(
        self,
        query: str,
        *,
        corpus_id: str | None = None,
        system_prompt: str | None = None,
        options: JSON | None = None,
        llm: JSON | None = None,
        assistant: JSON | None = None,
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            "/api/v1/query",
            json=_query_body(query, corpus_id, system_prompt, options, assistant),
            headers=_llm_headers(llm),
        )

    async def query_stream(
        self,
        query: str,
        *,
        corpus_id: str | None = None,
        system_prompt: str | None = None,
        options: JSON | None = None,
        llm: JSON | None = None,
        assistant: JSON | None = None,
    ) -> AsyncIterator[str]:
        """Yield the answer token by token. A failure part-way raises CobRagError."""
        async for event in self.query_stream_events(
            query,
            corpus_id=corpus_id,
            system_prompt=system_prompt,
            options=options,
            llm=llm,
            assistant=assistant,
        ):
            token = _token_or_stop(event)
            if token is None:
                return
            if token:
                yield token

    async def query_stream_events(
        self,
        query: str,
        *,
        corpus_id: str | None = None,
        system_prompt: str | None = None,
        options: JSON | None = None,
        llm: JSON | None = None,
        assistant: JSON | None = None,
    ) -> AsyncIterator[JSON]:
        """Yield every event as {"event", "data"}: sources, token..., then done or error."""
        async with self._http.stream(
            "POST",
            "/api/v1/query/stream",
            json=_query_body(query, corpus_id, system_prompt, options, assistant),
            headers=_llm_headers(llm),
        ) as response:
            if not response.is_success:
                await response.aread()
                _raise_for(response)
            parser = _SSEParser()
            async for line in response.aiter_lines():
                event = parser.feed(line)
                if event is not None:
                    yield event
            event = parser.feed("")
            if event is not None:
                yield event

    async def retrieve(
        self, query: str, *, corpus_id: str | None = None, options: JSON | None = None
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", "/api/v1/query/retrieve", json=_query_body(query, corpus_id, None, options)
        )

    async def create_widget_key(
        self, corpus_id: str, name: str, allowed_origins: list[str], **fields: Any
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/widget-keys",
            json={"name": name, "allowed_origins": allowed_origins, **fields},
        )

    async def list_widget_keys(self, corpus_id: str) -> list[JSON]:
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/widget-keys")  # type: ignore[no-any-return]

    async def usage(self, **params: Any) -> JSON:
        clean = {k: str(v) for k, v in params.items() if v is not None}
        return await self._req("GET", "/api/v1/usage", params=clean)  # type: ignore[no-any-return]

    async def usage_consumption(self) -> JSON:
        """The dashboard header summary (tenant key or workspace session; not a ck_ key)."""
        return await self._req("GET", "/api/v1/usage/consumption")  # type: ignore[no-any-return]

    async def costs(self, **params: Any) -> JSON:
        """Admin: platform cost report. params: start, end (YYYY-MM-DD)."""
        clean = {k: str(v) for k, v in params.items() if v is not None}
        return await self._req("GET", "/api/v1/admin/costs", params=clean)  # type: ignore[no-any-return]

    # BIL1 provider balances
    async def provider_balances(self) -> JSON:
        return await self._req("GET", "/api/v1/admin/billing/providers")  # type: ignore[no-any-return]

    # BIL2 pricing and accounts
    async def get_plan_pricing(self, plan_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/billing/plans/{plan_id}/pricing")  # type: ignore[no-any-return]

    async def set_plan_pricing(self, plan_id: str, **fields: Any) -> JSON:
        return await self._req("PUT", f"/api/v1/admin/billing/plans/{plan_id}/pricing", json=fields)  # type: ignore[no-any-return]

    async def list_billing_accounts(
        self,
        *,
        q: str | None = None,
        payment_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        params = _clean({"q": q, "payment_type": payment_type, "offset": offset, "limit": limit})
        return await self._req("GET", "/api/v1/admin/billing/accounts", params=params)  # type: ignore[no-any-return]

    async def get_billing_account(self, tenant_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/billing/accounts/{tenant_id}")  # type: ignore[no-any-return]

    async def update_billing_account(self, tenant_id: str, **fields: Any) -> JSON:
        return await self._req("PATCH", f"/api/v1/admin/billing/accounts/{tenant_id}", json=fields)  # type: ignore[no-any-return]

    async def billing_account(self) -> JSON:
        return await self._req("GET", "/api/v1/billing/account")  # type: ignore[no-any-return]

    # /BIL2 pricing and accounts

    # BIL3 ledger
    async def list_ledger(
        self,
        tenant_id: str,
        *,
        entry_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        params = _clean(
            {"entry_type": entry_type, "start": start, "end": end, "offset": offset, "limit": limit}
        )
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/admin/billing/accounts/{tenant_id}/ledger", params=params
        )

    async def add_ledger_entry(self, tenant_id: str, **fields: Any) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/billing/accounts/{tenant_id}/ledger", json=fields
        )

    async def billing_ledger(self, *, offset: int = 0, limit: int = 50) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET", "/api/v1/billing/ledger", params=_clean({"offset": offset, "limit": limit})
        )

    # /BIL3 ledger

    # P1 payments
    async def list_payments(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        params = _clean(
            {
                "tenant_id": tenant_id,
                "status": status,
                "start": start,
                "end": end,
                "offset": offset,
                "limit": limit,
            }
        )
        return await self._req("GET", "/api/v1/admin/billing/payments", params=params)  # type: ignore[no-any-return]

    async def get_payment(self, payment_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/admin/billing/payments/{payment_id}")  # type: ignore[no-any-return]

    async def refund_payment(self, payment_id: str, **fields: Any) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/billing/payments/{payment_id}/refunds", json=fields
        )

    async def create_payment_link(self, tenant_id: str, **fields: Any) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/billing/accounts/{tenant_id}/payment-links", json=fields
        )

    async def tax_details(self) -> JSON:
        return await self._req("GET", "/api/v1/billing/tax-details")  # type: ignore[no-any-return]

    async def set_tax_details(self, **fields: Any) -> JSON:
        return await self._req("PUT", "/api/v1/billing/tax-details", json=fields)  # type: ignore[no-any-return]

    async def billing_payments(self, *, offset: int = 0, limit: int = 50) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET", "/api/v1/billing/payments", params=_clean({"offset": offset, "limit": limit})
        )

    # /P1 payments

    # P2 invoices
    async def billing_invoices(self, *, offset: int = 0, limit: int = 50) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET", "/api/v1/billing/invoices", params=_clean({"offset": offset, "limit": limit})
        )

    async def list_invoices(
        self,
        *,
        tenant_id: str | None = None,
        doc_type: str | None = None,
        fy: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET",
            "/api/v1/admin/billing/invoices",
            params=_clean(
                {
                    "tenant_id": tenant_id,
                    "doc_type": doc_type,
                    "fy": fy,
                    "start": start,
                    "end": end,
                    "offset": offset,
                    "limit": limit,
                }
            ),
        )

    # /P2 invoices

    # BIL5 add-ons
    async def list_add_ons(self, *, include_inactive: bool = False) -> JSON:
        params = {"include_inactive": "true"} if include_inactive else None
        return await self._req("GET", "/api/v1/admin/billing/add-ons", params=params)  # type: ignore[no-any-return]

    async def create_add_on(self, **fields: Any) -> JSON:
        return await self._req("POST", "/api/v1/admin/billing/add-ons", json=fields)  # type: ignore[no-any-return]

    async def update_add_on(self, add_on_id: str, **fields: Any) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/admin/billing/add-ons/{add_on_id}", json=fields
        )

    async def delete_add_on(self, add_on_id: str) -> None:
        await self._req("DELETE", f"/api/v1/admin/billing/add-ons/{add_on_id}")

    async def list_grants(self, tenant_id: str, *, active_only: bool = False) -> JSON:
        params = {"active_only": "true"} if active_only else None
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/admin/billing/accounts/{tenant_id}/grants", params=params
        )

    async def apply_add_on(self, tenant_id: str, **fields: Any) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/admin/billing/accounts/{tenant_id}/grants", json=fields
        )

    async def revoke_grant(self, tenant_id: str, grant_id: str, *, memo: str | None = None) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/admin/billing/accounts/{tenant_id}/grants/{grant_id}/revoke",
            json={"memo": memo} if memo else {},
        )

    # /BIL5 add-ons

    # BIL6 statements
    async def list_statements(self, tenant_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/admin/billing/accounts/{tenant_id}/statements"
        )

    async def get_statement(self, tenant_id: str, month: str, *, format: str = "json") -> Any:
        if format == "json":
            return await self._req(
                "GET", f"/api/v1/admin/billing/accounts/{tenant_id}/statements/{month}"
            )
        return await self._text(
            "GET",
            f"/api/v1/admin/billing/accounts/{tenant_id}/statements/{month}",
            params={"format": format},
        )

    async def billing_statements(self) -> JSON:
        return await self._req("GET", "/api/v1/billing/statements")  # type: ignore[no-any-return]

    async def billing_statement(self, month: str, *, format: str = "json") -> Any:
        if format == "json":
            return await self._req("GET", f"/api/v1/billing/statements/{month}")
        return await self._text(
            "GET", f"/api/v1/billing/statements/{month}", params={"format": format}
        )

    async def request_top_up(
        self, *, amount_minor: int | None = None, note: str | None = None
    ) -> JSON:
        body: dict[str, Any] = {}  # not `_clean`: an amount stays a number
        if amount_minor is not None:
            body["amount_minor"] = amount_minor
        if note is not None:
            body["note"] = note
        return await self._req("POST", "/api/v1/billing/top-up-requests", json=body)  # type: ignore[no-any-return]

    # /BIL6 statements

    # TV3 no surprises
    async def set_spend_cap(self, cap_minor: int | None) -> JSON:
        return await self._req("PUT", "/api/v1/billing/spend-cap", json={"cap_minor": cap_minor})  # type: ignore[no-any-return]

    async def preview_pricing(self, plan_id: str, **fields: Any) -> JSON:
        return await self._req(
            "POST", f"/api/v1/admin/billing/plans/{plan_id}/pricing/preview", json=fields
        )  # type: ignore[no-any-return]

    async def cancel_scheduled_pricing(self, plan_id: str) -> JSON:
        return await self._req("DELETE", f"/api/v1/admin/billing/plans/{plan_id}/pricing/scheduled")  # type: ignore[no-any-return]

    async def cancel_pending_change(self, tenant_id: str) -> JSON:
        return await self._req(
            "DELETE", f"/api/v1/admin/billing/accounts/{tenant_id}/pending-change"
        )  # type: ignore[no-any-return]

    # /TV3 no surprises

    # TV4 value report
    async def value_settings(self) -> JSON:
        return await self._req("GET", "/api/v1/value/settings")  # type: ignore[no-any-return]

    async def update_value_settings(
        self,
        *,
        lead_value_minor: int,
        handoff_value_minor: int,
        staff_hour_minor: int,
        minutes_per_answer: int,
        email_enabled: bool = True,
        industry_defaults: str | None = None,
    ) -> JSON:
        body: dict[str, Any] = {
            "lead_value_minor": lead_value_minor,
            "handoff_value_minor": handoff_value_minor,
            "staff_hour_minor": staff_hour_minor,
            "minutes_per_answer": minutes_per_answer,
            "email_enabled": email_enabled,
        }
        if industry_defaults is not None:
            body["industry_defaults"] = industry_defaults
        return await self._req("PUT", "/api/v1/value/settings", json=body)  # type: ignore[no-any-return]

    async def value_report(self, month: str | None = None, *, format: str = "json") -> Any:
        params = _clean({"month": month})
        if format == "json":
            return await self._req("GET", "/api/v1/value/report", params=params)
        return await self._text("GET", "/api/v1/value/report", params={**params, "format": format})

    async def value_reports(self) -> JSON:
        return await self._req("GET", "/api/v1/value/reports")  # type: ignore[no-any-return]

    async def admin_value_report(
        self, tenant_id: str, month: str | None = None, *, format: str = "json"
    ) -> Any:
        params = _clean({"month": month})
        if format == "json":
            return await self._req(
                "GET", f"/api/v1/admin/tenants/{tenant_id}/value-report", params=params
            )
        return await self._text(
            "GET",
            f"/api/v1/admin/tenants/{tenant_id}/value-report",
            params={**params, "format": format},
        )

    # /TV4 value report

    # BIZ3 home and articles
    async def home(self, **params: Any) -> JSON:
        return await self._req("GET", "/api/v1/home", params=_clean(params))  # type: ignore[no-any-return]

    async def create_article(self, corpus_id: str, kind: str, title: str, body: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/articles",
            json={"kind": kind, "title": title, "body": body},
        )

    async def get_article(self, corpus_id: str, document_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/articles/{document_id}"
        )

    async def update_article(
        self, corpus_id: str, document_id: str, kind: str, title: str, body: str
    ) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PUT",
            f"/api/v1/corpora/{corpus_id}/articles/{document_id}",
            json={"kind": kind, "title": title, "body": body},
        )

    # /BIZ3 home and articles

    # BIZ1 website learning
    async def website(self, corpus_id: str) -> JSON:
        return await self._req("GET", f"/api/v1/corpora/{corpus_id}/website")  # type: ignore[no-any-return]

    async def update_website(
        self,
        corpus_id: str,
        *,
        enabled: bool | None = None,
        exclude_patterns: list[str] | None = None,
        refresh_mode: str | None = None,  # BIZ2
        adaptive: bool | None = None,  # BIZ2
    ) -> JSON:
        body = _present(
            {
                "enabled": enabled,
                "exclude_patterns": exclude_patterns,
                "refresh_mode": refresh_mode,
                "adaptive": adaptive,
            }
        )
        return await self._req(  # type: ignore[no-any-return]
            "PATCH", f"/api/v1/corpora/{corpus_id}/website", json=body
        )

    async def learn_website(self, corpus_id: str, origin: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/website/learn", json={"origin": origin}
        )

    async def reread_website(self, corpus_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/website/reread"
        )

    async def remove_website_source(self, corpus_id: str, source_id: str) -> None:
        await self._req("DELETE", f"/api/v1/corpora/{corpus_id}/website/sources/{source_id}")

    async def website_pages(self, corpus_id: str, **params: Any) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "GET", f"/api/v1/corpora/{corpus_id}/website/pages", params=_clean(params)
        )

    async def reread_website_page(self, corpus_id: str, page_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/website/pages/{page_id}/reread"
        )

    async def update_website_page(self, corpus_id: str, page_id: str, excluded: bool) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "PATCH",
            f"/api/v1/corpora/{corpus_id}/website/pages/{page_id}",
            json={"excluded": excluded},
        )

    # /BIZ1 website learning

    # BIZ2 freshness
    async def create_learn_key(self, corpus_id: str) -> JSON:
        return await self._req(  # type: ignore[no-any-return]
            "POST", f"/api/v1/corpora/{corpus_id}/website/learn-key"
        )

    async def delete_learn_key(self, corpus_id: str) -> None:
        await self._req("DELETE", f"/api/v1/corpora/{corpus_id}/website/learn-key")

    # /BIZ2 freshness

    # BIZ4 avatar
    async def upload_avatar(
        self,
        corpus_id: str,
        file: str | os.PathLike[str] | bytes,
        *,
        filename: str | None = None,
    ) -> JSON:
        name, content = _file_part(file, filename)
        return await self._req(  # type: ignore[no-any-return]
            "POST",
            f"/api/v1/corpora/{corpus_id}/assistant/avatar",
            files={"file": (name, content)},
        )

    # /BIZ4 avatar

    async def get_settings(self) -> JSON:
        return await self._req("GET", "/api/v1/admin/settings")  # type: ignore[no-any-return]

    async def update_settings(self, **changes: Any) -> JSON:
        return await self._req("PATCH", "/api/v1/admin/settings", json=changes)  # type: ignore[no-any-return]

    async def public_branding(self) -> JSON:
        return await self._req("GET", "/api/v1/public/branding")  # type: ignore[no-any-return]

    async def smtp_status(self) -> JSON:
        return await self._req("GET", "/api/v1/admin/smtp")  # type: ignore[no-any-return]

    async def send_test_email(self, to: str) -> JSON:
        return await self._req("POST", "/api/v1/admin/smtp/test", json={"to": to})  # type: ignore[no-any-return]
