"""One thinking control for every model the engine calls (RAG_LLM_THINKING,
RAG_LLM_FAST_THINKING, llm_config.thinking / llm_config.fast_thinking).

A level is one of ``off | minimal | low | medium | high | provider-default``. Each model family
switches thinking in its own way, so `thinking_attempts` turns (model, provider route, level)
into the exact request parameters for that family:

- through OpenRouter, its unified ``reasoning`` object in the request body
  (openrouter.ai/docs/use-cases/reasoning-tokens): ``effort``, ``enabled`` or ``max_tokens``;
- otherwise LiteLLM's ``reasoning_effort`` (which LiteLLM maps to Anthropic ``thinking`` and
  Gemini ``thinkingBudget`` / ``thinkingLevel``) or the provider's own body field
  (``chat_template_kwargs.enable_thinking`` on vLLM-style hosts, ``thinking.type`` on DeepSeek,
  Z.ai and Moonshot).

Where a family cannot switch thinking off, the request asks for its minimum and the attempt
says so in `effective` (``gpt-oss`` thinks at ``low`` at least, Gemini 3 Flash at ``minimal``).
The attempts are ordered: when a provider refuses one (a 400 naming reasoning or thinking), the
caller remembers that and sends the next, which ends with sending nothing at all, so a model
never keeps receiving a parameter it rejects.

The family table is a snapshot of the OpenRouter catalogue's per-model ``reasoning`` metadata
(``GET /api/v1/models``: ``supported_efforts``, ``mandatory``, ``default_enabled``) and the
providers' docs, both read on 2026-10-03; ``docs/internal/research/2026-10-03-thinking-control.md``
has the sources. ``python -m benchmarks.thinking_check --drift`` compares it with the live
catalogue.
"""

import dataclasses
import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "provider-default"]
THINKING_LEVELS: tuple[str, ...] = ("off", "minimal", "low", "medium", "high", "provider-default")
# The old RAG_LLM_REASONING_EFFORT / llm_config.reasoning_effort values, read as levels.
LEGACY_EFFORTS: dict[str, str] = {"none": "off", "low": "low", "medium": "medium", "high": "high"}
_ORDER = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
# OpenRouter's effort names in rising order ("none" first).
_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
# The OpenRouter catalogue's per-model `reasoning` metadata, refreshed with
# `python -m benchmarks.thinking_check --snapshot`.
SNAPSHOT_PATH = Path(__file__).with_name("openrouter_reasoning.json")

Route = Literal["openrouter", "native"]
Native = Literal["effort", "template", "thinking_type", "anthropic", "none"]


def parse_thinking(value: object) -> str | None:
    """A thinking level from a setting or a stored value: a level, or an old effort name
    (``none`` reads as ``off``). Empty or None is None (unset). Raises ValueError otherwise."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("thinking must be a string")
    text = value.strip().lower().replace("_", "-")
    if not text:
        return None
    if text in THINKING_LEVELS:
        return text
    if text in LEGACY_EFFORTS:
        return LEGACY_EFFORTS[text]
    raise ValueError(f"thinking must be one of {', '.join(THINKING_LEVELS)}")


def clean_thinking(value: object) -> str | None:
    """`parse_thinking` that answers None instead of raising (stored or stand-in values)."""
    try:
        return parse_thinking(value)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Family:
    """How one family of models thinks and how to steer it.

    `efforts`: the OpenRouter ``reasoning.effort`` values the family accepts, lowest first
    (``none`` included when it can be switched off that way); empty for a family steered only
    by ``enabled`` (a toggle) or by nothing. `toggle`: ``reasoning.enabled`` switches it.
    `can_disable`: thinking can really be switched off. `thinks_by_default`: what the model
    does when nothing is sent (None = unknown). `budget_min`: a family steered by a token
    budget whose floor is this (Gemini 2.5 Pro); off and minimal ask for it.
    """

    name: str
    efforts: tuple[str, ...] = ()
    toggle: bool = False
    can_disable: bool = True
    thinks_by_default: bool | None = True
    native: Native = "effort"
    native_off: str | None = "none"
    budget_min: int | None = None
    plain: bool = False
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


PLAIN = Family("plain", plain=True, thinks_by_default=False, native="none", notes="no thinking")
UNKNOWN = Family(
    "unknown", efforts=("none", "minimal", "low", "medium", "high"), thinks_by_default=None
)

# Ordered: the first pattern that matches the model name (vendor prefix kept, "openrouter/"
# and ":variant" suffixes dropped) decides.
_FAMILIES: tuple[tuple[str, Family], ...] = (
    # ---- OpenAI
    (
        r"gpt-oss",
        Family(
            "gpt-oss",
            ("low", "medium", "high"),
            can_disable=False,
            notes="Harmony reasoning levels; low is the floor",
        ),
    ),
    (
        r"(^|/)gpt-5(-mini|-nano)?$",
        Family(
            "gpt-5",
            ("minimal", "low", "medium", "high"),
            can_disable=False,
            notes="minimal is the floor",
        ),
    ),
    (r"(^|/)gpt-5\.\d+-chat|(^|/)gpt-5-chat", PLAIN),
    (
        r"(^|/)gpt-5(\.[0-5])?-pro|(^|/)gpt-5\.[0-2]-codex|(^|/)gpt-6-astra|(^|/)gpt-6\.\d",
        Family(
            "openai-mandatory",
            ("low", "medium", "high"),
            can_disable=False,
            notes="reasoning mandatory; low is the floor",
        ),
    ),
    (
        r"(^|/)gpt-(5\.\d+|6)",
        Family(
            "openai-none",
            ("none", "low", "medium", "high"),
            notes="effort none switches reasoning off",
        ),
    ),
    (
        r"(^|/)o\d(-mini|-pro)?(-high)?$|(^|/)o\d-",
        Family("openai-o", ("low", "medium", "high"), can_disable=False, notes="low is the floor"),
    ),
    (r"(^|/)(gpt-4|gpt-3|chatgpt)", PLAIN),
    # ---- Google
    (
        r"gemini-2\.5-pro",
        Family(
            "gemini-2.5-pro",
            can_disable=False,
            budget_min=128,
            native_off="minimal",
            notes="thinking budget floor 128",
        ),
    ),
    (
        r"gemini-2\.5-flash-lite",
        Family(
            "gemini-2.5-flash-lite",
            ("none", "minimal", "low", "medium", "high"),
            thinks_by_default=False,
            notes="budget 0 = off; off by default",
            extra={"or_off_fallback": {"max_tokens": 0}},
        ),
    ),
    (
        r"gemini-2\.5-flash",
        Family(
            "gemini-2.5-flash",
            ("none", "minimal", "low", "medium", "high"),
            notes="budget 0 = off",
            extra={"or_off_fallback": {"max_tokens": 0}},
        ),
    ),
    (
        r"gemini-3(\.\d+)?-pro|gemini-3\.([7-9]|\d\d)-flash|gemini-(pro|flash)-latest",
        Family(
            "gemini-3-low",
            ("low", "medium", "high"),
            can_disable=False,
            native_off="low",
            notes="thinkingLevel low is the floor",
        ),
    ),
    (
        r"gemini-3",
        Family(
            "gemini-3-flash",
            ("minimal", "low", "medium", "high"),
            can_disable=False,
            native_off="minimal",
            notes="thinkingLevel minimal is the floor and does not guarantee off",
        ),
    ),
    (
        r"gemma-4",
        Family(
            "gemma-4",
            toggle=True,
            thinks_by_default=False,
            native="none",
            notes="thinking only when the system prompt asks; off by default",
        ),
    ),
    (r"gemma|llama|mixtral|codestral|pixtral|phi-|command-r|olmo", PLAIN),
    # ---- Anthropic
    (
        r"claude-(opus|sonnet)-5\.5|claude-.*(fable|mythos)|claude-sonnet-latest",
        Family(
            "claude-mandatory",
            ("low", "medium", "high"),
            can_disable=False,
            native="anthropic",
            notes="thinking cannot be disabled; low is the floor",
        ),
    ),
    (
        r"claude-(opus|sonnet|haiku)-5|claude-5",
        Family(
            "claude-5",
            ("low", "medium", "high"),
            toggle=True,
            native="anthropic",
            notes="thinking on by default; disabled switches it off",
        ),
    ),
    (
        r"claude",
        Family(
            "claude",
            ("low", "medium", "high"),
            thinks_by_default=False,
            native="anthropic",
            notes="extended thinking is opt-in",
        ),
    ),
    # ---- Qwen
    (r"qwen3-max", Family("qwen3-hybrid", toggle=True, native="none")),
    (
        r"qwen.*(thinking|qwq)|qwq",
        Family("always-on", can_disable=False, native="none", notes="a thinking-only variant"),
    ),
    (r"qwen.*(instruct|coder|-vl-)|qwen2|qwen-(turbo|plus|max)$", PLAIN),
    (r"qwen3-.*-2507$", PLAIN),  # the 2507 releases split instruct (no thinking) from thinking
    (
        r"qwen3\.8-(max|2\.4t)",
        Family(
            "qwen3.8-max", ("minimal", "low", "medium", "high"), can_disable=False, native="none"
        ),
    ),
    (r"qwen3\.8-27b", Family("qwen3.8", ("low", "medium", "high"), toggle=True, native="template")),
    (
        r"qwen3",
        Family(
            "qwen3-hybrid",
            toggle=True,
            native="template",
            notes="enable_thinking false switches it off",
        ),
    ),
    # ---- DeepSeek
    (
        r"deepseek-(r1|reasoner)",
        Family("always-on", can_disable=False, native="none", notes="a thinking-only model"),
    ),
    (
        r"deepseek-v4|deepseek-(flash|pro)-latest",
        Family(
            "deepseek-v4",
            ("low", "high"),
            toggle=True,
            native="thinking_type",
            notes="thinking.type disabled switches it off",
        ),
    ),
    (
        r"deepseek-(chat-v3\.1|v3\.1|v3\.2)",
        Family("deepseek-hybrid", toggle=True, thinks_by_default=False, native="thinking_type"),
    ),
    (r"deepseek", PLAIN),
    # ---- Mistral
    (r"magistral", Family("always-on", can_disable=False, native="none")),
    (
        r"mistral-small-2603|mistral-medium-3-5|mistral-small-4|mistral-small-latest",
        Family(
            "mistral-reasoning",
            ("none", "high"),
            thinks_by_default=False,
            notes="reasoning_effort none or high",
        ),
    ),
    (r"mistral|ministral", PLAIN),
    # ---- Moonshot
    (r"kimi-k2-thinking|kimi-k2\.7-code", Family("always-on", can_disable=False, native="none")),
    (
        r"kimi-k3|kimi-latest",
        Family(
            "kimi-k3",
            ("low", "high"),
            can_disable=False,
            native="none",
            notes="always thinks; low is the floor",
        ),
    ),
    (r"kimi-k2\.\d", Family("kimi-k2-hybrid", toggle=True, native="thinking_type")),
    (r"kimi", PLAIN),
    # ---- Z.ai GLM
    (
        r"glm-5\.3|glm-(flash-)?latest",
        Family(
            "glm-mandatory",
            ("low", "high"),
            can_disable=False,
            native="none",
            notes="forced thinking",
        ),
    ),
    (r"glm-5\.2", Family("glm-5.2", ("high",), toggle=True, native="thinking_type")),
    (
        r"glm-(4\.[5-9]|5)",
        Family(
            "glm-hybrid",
            toggle=True,
            native="thinking_type",
            notes="thinking.type disabled switches it off",
        ),
    ),
    # ---- NVIDIA
    (
        r"nemotron-3-super",
        Family("nemotron-super", ("low", "medium"), toggle=True, native="template"),
    ),
    (
        r"nemotron-3-ultra",
        Family("nemotron-ultra", ("medium", "high"), toggle=True, native="template"),
    ),
    (
        r"nemotron",
        Family(
            "nemotron-hybrid",
            toggle=True,
            native="template",
            notes="/no_think or enable_thinking false",
        ),
    ),
    # ---- xAI
    (r"grok-4\.3", Family("grok-4.3", ("none", "low", "medium", "high"), native="effort")),
    (
        r"grok-4\.20$|grok-4\.20-(?!multi)",
        Family("grok-4.20", toggle=True, thinks_by_default=False, native="none"),
    ),
    (
        r"grok-3-mini",
        Family("grok-3-mini", ("low", "high"), can_disable=False, notes="low or high only"),
    ),
    (r".*non-reasoning", PLAIN),
    (
        r"grok-4\.(5|6|7|\d\d)|grok-latest|grok-4\.20-multi",
        Family(
            "grok-mandatory",
            ("low", "medium", "high"),
            can_disable=False,
            notes="reasoning cannot be disabled; low is the floor",
        ),
    ),
    (
        r"grok-4|grok-build",
        Family("always-on", can_disable=False, native="none", notes="reasoning cannot be disabled"),
    ),
    (r"grok", PLAIN),
)
_COMPILED = tuple((re.compile(p), f) for p, f in _FAMILIES)


def model_key(model: str) -> str:
    """The name the family table matches: lower case, without "openrouter/", a leading "~"
    alias marker or a ":variant" suffix."""
    name = model.strip().lower()
    if name.startswith("openrouter/"):
        name = name[len("openrouter/") :]
    name = name.lstrip("~")
    return name.split(":", 1)[0]


def family_of(model: str) -> Family | None:
    """The family `model` belongs to, or None when the table does not know it."""
    key = model_key(model)
    for pattern, family in _COMPILED:
        if pattern.search(key):
            return family
    return None


@dataclass(frozen=True, slots=True)
class ThinkingAttempt:
    """One way to ask for a thinking level.

    `kwargs` go to ``litellm.acompletion`` as they are; `body` is merged into its
    ``extra_body`` (the request JSON). `effective` is the level the model then runs at:
    a level; ``on`` when the family can only be switched on (its own depth); or
    ``provider-default`` when nothing is sent and the model's default is not known to be off.
    `key` names the attempt for the rejection memory; the empty attempt has key "".
    """

    key: str
    effective: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)

    @property
    def sends(self) -> bool:
        return bool(self.kwargs or self.body)


def _nothing(family: Family | None) -> ThinkingAttempt:
    if family is None or family.thinks_by_default is None:
        return ThinkingAttempt("", "provider-default")
    return ThinkingAttempt("", "provider-default" if family.thinks_by_default else "off")


def _floor(efforts: tuple[str, ...]) -> str | None:
    """The lowest effort that still thinks (the family's floor when it cannot be off)."""
    usable = [e for e in efforts if e != "none"]
    return usable[0] if usable else None


def _pick(efforts: tuple[str, ...], level: str) -> str | None:
    """The supported effort for a thinking level: the level itself, else the next one up,
    else the highest the family has."""
    usable = [e for e in efforts if e != "none"]
    if not usable:
        return None
    if level in usable:
        return level
    want = _ORDER.index(level)
    higher = [e for e in usable if _ORDER.index(e) > want]
    return higher[0] if higher else usable[-1]


class _Attempts:
    def __init__(self) -> None:
        self.items: list[ThinkingAttempt] = []

    def add(self, attempt: ThinkingAttempt) -> None:
        if all(a.key != attempt.key for a in self.items):
            self.items.append(attempt)


def _or(reasoning: dict[str, Any], effective: str) -> ThinkingAttempt:
    key = "or:" + ",".join(f"{k}={v}" for k, v in sorted(reasoning.items()))
    return ThinkingAttempt(key, effective, body={"reasoning": reasoning})


def _or_attempts(family: Family, level: str) -> list[ThinkingAttempt]:
    """Through OpenRouter: its unified `reasoning` object in the request body."""
    out = _Attempts()
    if family.budget_min is not None:  # steered by a token budget with a floor
        if level in ("off", "minimal"):
            out.add(_or({"max_tokens": family.budget_min}, "minimal"))
        else:
            out.add(_or({"effort": level}, level))
        return out.items
    if level == "off":
        if "none" in family.efforts:
            out.add(_or({"effort": "none"}, "off"))
        if "or_off_fallback" in family.extra:  # the same switch in the family's own unit
            out.add(_or(dict(family.extra["or_off_fallback"]), "off"))
        if family.can_disable and (family.toggle or family is UNKNOWN):
            out.add(_or({"enabled": False}, "off"))
        floor = _floor(family.efforts)
        if floor and (not family.can_disable or family.toggle or family is UNKNOWN):
            out.add(_or({"effort": floor}, floor))
        if family is UNKNOWN:
            out.add(_or({"effort": "low"}, "low"))
        return out.items
    effort = _pick(family.efforts, level)
    if effort:
        out.add(_or({"effort": effort}, effort))
        if family is UNKNOWN and effort == "minimal":
            out.add(_or({"effort": "low"}, "low"))
    elif family.toggle:
        out.add(_or({"enabled": True}, "on"))
    return out.items


def _effort(value: str, effective: str) -> ThinkingAttempt:
    # allowed_openai_params: LiteLLM forwards (or maps) the parameter even for a model id its
    # map does not list yet, instead of refusing it client-side.
    return ThinkingAttempt(
        f"effort={value}",
        effective,
        kwargs={"reasoning_effort": value, "allowed_openai_params": ["reasoning_effort"]},
    )


def _native_attempts(family: Family, level: str) -> list[ThinkingAttempt]:
    """Without OpenRouter: LiteLLM's reasoning_effort, or the provider's own body field."""
    out = _Attempts()
    on = level != "off"
    if family.native == "none":
        return out.items
    if family.native == "template":  # vLLM / SGLang chat templates (Qwen3, Nemotron)
        template: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": on}}
        out.add(ThinkingAttempt(f"template={on}", level if on else "off", body=template))
        return out.items
    if family.native == "thinking_type":  # DeepSeek, Z.ai GLM, Moonshot Kimi
        body = {"thinking": {"type": "enabled" if on else "disabled"}}
        out.add(ThinkingAttempt(f"thinking={on}", "on" if on else "off", body=body))
        return out.items
    if family.native == "anthropic":
        # LiteLLM maps reasoning_effort to Anthropic `thinking` (minimal -> the 1024-token
        # floor). Off on an opt-in model is simply not asking for it.
        if not on:
            if family.toggle:
                out.add(
                    ThinkingAttempt(
                        "thinking=disabled", "off", kwargs={"thinking": {"type": "disabled"}}
                    )
                )
            if not family.can_disable or family.toggle:
                out.add(_effort("low", "low"))
            return out.items
        value = level if level != "minimal" or family.can_disable else "low"
        out.add(_effort(value, value))
        return out.items
    # "effort": OpenAI, Gemini (LiteLLM maps it to thinkingBudget / thinkingLevel), Mistral,
    # xAI, Groq and other OpenAI-compatible hosts.
    if level in ("off", "minimal") and family.native_off not in (None, "none"):
        floor: str = family.native_off  # Gemini 2.5 Pro and Gemini 3: LiteLLM's own floor value
        out.add(_effort(floor, "minimal" if floor == "minimal" else floor))
        if floor != "low":
            out.add(_effort("low", "low"))
        return out.items
    if not on:
        if "none" in family.efforts:
            out.add(_effort("none", "off"))
        lowest = _floor(family.efforts)
        if lowest and (not family.can_disable or family is UNKNOWN):
            out.add(_effort(lowest, lowest))
        if family is UNKNOWN:
            out.add(_effort("low", "low"))
        return out.items
    effort = _pick(family.efforts, level)
    if effort:
        out.add(_effort(effort, effort))
        if family is UNKNOWN and effort == "minimal":
            out.add(_effort("low", "low"))
    return out.items


@functools.lru_cache(maxsize=1)
def _snapshot() -> dict[str, Any]:
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        models = data.get("models")
        return models if isinstance(models, dict) else {}
    except (OSError, ValueError):
        return {}


def catalogue_meta(model: str) -> tuple[bool, dict[str, Any] | None]:
    """(listed, reasoning metadata) of `model` in the OpenRouter catalogue snapshot. A model
    listed with None does not reason."""
    raw = model.strip()
    # LiteLLM's "openrouter/" prefix, unless the id itself is OpenRouter's (openrouter/auto).
    name = raw[len("openrouter/") :] if raw.lower().startswith("openrouter/") else raw
    snapshot = _snapshot()
    for key in (raw, name, name.split(":", 1)[0]):
        if key in snapshot:
            meta = snapshot[key]
            return True, meta if isinstance(meta, dict) else None
    return False, None


def _with_catalogue(family: Family | None, meta: dict[str, Any] | None) -> Family:
    """The family as the OpenRouter catalogue lists the model: its efforts and whether its
    reasoning is mandatory. The table still decides what a vendor documents more strictly
    (a family that cannot switch thinking off stays minimum-only) and the native details."""
    if meta is None:
        return PLAIN
    listed = meta.get("supported_efforts") or []
    efforts = tuple(e for e in _EFFORT_ORDER if e in listed)
    mandatory = bool(meta.get("mandatory"))
    default = meta.get("default_enabled")
    if family is None or family.plain:
        return Family(
            "openrouter-catalogue",
            efforts,
            toggle=not mandatory,
            can_disable=not mandatory,
            thinks_by_default=default if isinstance(default, bool) else None,
            native="none",
            notes="from the OpenRouter catalogue",
        )
    if mandatory or not family.can_disable:
        efforts = tuple(e for e in efforts if e != "none")
    return dataclasses.replace(
        family,
        efforts=efforts or family.efforts,
        can_disable=family.can_disable and not mandatory,
        toggle=family.toggle and not mandatory,
    )


def thinking_attempts(
    model: str, *, openrouter: bool, level: str | None, reasoning_known: bool = False
) -> list[ThinkingAttempt]:
    """The attempts for `level` on `model`, best first; the last one sends nothing.

    `openrouter`: the call goes through OpenRouter; a model in the catalogue snapshot then
    gets the efforts it lists. `reasoning_known`: LiteLLM's model map says the model reasons;
    it decides only for a model neither the table nor the catalogue knows.
    """
    family = family_of(model)
    if openrouter:
        listed, meta = catalogue_meta(model)
        if listed:
            family = _with_catalogue(family, meta)
    lvl = clean_thinking(level) or "provider-default"
    if lvl == "provider-default" or (family is not None and family.plain):
        return [_nothing(family)]
    if family is None:
        if not reasoning_known and not openrouter:
            return [_nothing(None)]
        family = UNKNOWN
    attempts = _or_attempts(family, lvl) if openrouter else _native_attempts(family, lvl)
    # Sending nothing ends the list: for a family that is off by default it is the off switch
    # itself, and after every refusal it is what the model is left with.
    return [*attempts, _nothing(None if family is UNKNOWN else family)]


def describe(model: str, *, openrouter: bool, level: str) -> dict[str, Any]:
    """What `thinking_attempts` would send first, for tools and the research table."""
    first = thinking_attempts(model, openrouter=openrouter, level=level, reasoning_known=True)[0]
    family = family_of(model)
    if openrouter:
        listed, meta = catalogue_meta(model)
        if listed:
            family = _with_catalogue(family, meta)
    return {
        "family": family.name if family else "unknown",
        "kwargs": first.kwargs,
        "body": first.body,
        "effective": first.effective,
        # The family thinks and cannot be switched off: "off" gets its minimum.
        "minimum_only": bool(family and not family.plain and not family.can_disable),
        "notes": family.notes if family else "",
    }


# ---------------------------------------------------------------- reasoning text in content

# Some hosts put a model's thinking inside the answer text instead of a separate field:
# Qwen3 / DeepSeek-R1 style <think>...</think> (an empty pair even when thinking is off), and
# Gemma 4's thought channel, which it still emits, empty, when thinking is off. None of it may
# reach a visitor, so it is removed from complete replies and from streams.
_BLOCKS: tuple[tuple[str, str], ...] = (
    ("<think>", "</think>"),
    ("<|channel>thought", "<channel|>"),
)
# What a check counts as reasoning leaking into an answer.
LEAK_MARKERS: tuple[str, ...] = ("<think>", "</think>", "<|channel>", "<channel|>", "<|think|>")


def strip_reasoning_text(text: str) -> str:
    """`text` without inline reasoning blocks (a complete reply)."""
    if "<" not in text:
        return text
    stream = ReasoningTextFilter()
    return stream.feed(text) + stream.flush()


class ReasoningTextFilter:
    """Feed streamed deltas, get the visible part: every reasoning block (and the blank space
    after it) is dropped. Holds back only a possible partial tag at the end of what it has
    seen, so a plain answer streams through unchanged and at once. An unclosed block is
    dropped to the end."""

    def __init__(self) -> None:
        self._buf = ""
        self._close: str | None = None  # the closing tag while inside a block
        self._after_block = False

    def feed(self, delta: str) -> str:
        self._buf += delta
        out: list[str] = []
        while self._buf:
            if self._close is not None:
                end = self._buf.lower().find(self._close.lower())
                if end < 0:
                    keep = len(self._close) - 1
                    self._buf = self._buf[-keep:] if keep else ""
                    break
                self._buf = self._buf[end + len(self._close) :]
                self._close = None
                self._after_block = True
                continue
            if self._after_block:
                stripped = self._buf.lstrip()
                if not stripped:
                    self._buf = ""
                    break
                self._buf = stripped
                self._after_block = False
            low = self._buf.lower()
            hits = [(low.find(o.lower()), o, c) for o, c in _BLOCKS if o.lower() in low]
            if hits:
                start, opening, closing = min(hits)
                out.append(self._buf[:start])
                self._buf = self._buf[start + len(opening) :]
                self._close = closing
                continue
            hold = max(_partial_suffix(low, o.lower()) for o, _ in _BLOCKS)
            out.append(self._buf[: len(self._buf) - hold])
            self._buf = self._buf[len(self._buf) - hold :]
            break
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self._close is not None else self._buf
        self._buf, self._close = "", None
        return rest


def _partial_suffix(text: str, tag: str) -> int:
    """How many trailing characters of `text` are a proper prefix of `tag`."""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-n:]):
            return n
    return 0


# ---------------------------------------------------------------- checking a reply


@dataclass(frozen=True, slots=True)
class ThinkingCheck:
    """Whether one reply really ran without thinking (`check_thinking_off`)."""

    ok: bool
    reasoning_tokens: int | None
    leaked: tuple[str, ...]
    thinking_effective: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "thinking_ok": self.ok,
            "reasoning_tokens": self.reasoning_tokens,
            "leaked": list(self.leaked),
            "thinking_effective": self.thinking_effective,
        }


def check_thinking_off(
    *,
    content: str,
    reasoning_tokens: int | None,
    reasoning_text: str | None = None,
    thinking_effective: str | None = None,
) -> ThinkingCheck:
    """Per reply, because a host may ignore the switch: no reasoning tokens billed, no
    reasoning markers in the visible text and no reasoning field. `reasoning_tokens` None
    (not reported) does not pass: a check that cannot see the count cannot vouch for it."""
    leaked = [m for m in LEAK_MARKERS if m in content]
    if reasoning_text and reasoning_text.strip():
        leaked.append("reasoning field")
    ok = reasoning_tokens == 0 and not leaked
    return ThinkingCheck(ok, reasoning_tokens, tuple(leaked), thinking_effective)


def reasoning_of(message: Any) -> str:
    """The reasoning text a LiteLLM message (or stream delta) carries in its own fields:
    `reasoning_content` (LiteLLM's name), `reasoning` (OpenRouter's) or Anthropic thinking
    blocks. Empty when none."""
    parts: list[str] = []
    for name in ("reasoning_content", "reasoning"):
        value = message.get(name) if isinstance(message, dict) else getattr(message, name, None)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    blocks = (
        message.get("thinking_blocks")
        if isinstance(message, dict)
        else getattr(message, "thinking_blocks", None)
    )
    if isinstance(blocks, list):
        for block in blocks:
            text = block.get("thinking") if isinstance(block, dict) else None
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n".join(parts)
