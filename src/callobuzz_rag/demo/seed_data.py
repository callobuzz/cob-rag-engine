"""The demo's seed: a fictional clinic in Pune, English and Hindi, kept as data files.

`load()` reads `demo/data/clinic/` and validates every file against the schema the engine's own
routes use, so a typo in the seed fails a unit test and not the nightly reset. Nothing here is a
real person, company, phone number or address beyond a street name.
"""

import copy
import json
import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any, Final

from callobuzz_rag.core.exceptions import RAGKernelError

DATA: Final[Traversable] = resources.files("callobuzz_rag.demo").joinpath("data/clinic")

# `en.about` -> ("about", "en"): the language is the second-to-last suffix of the file name.
_DOC_NAME = re.compile(r"^(?P<slug>[a-z0-9-]+)\.(?P<lang>en|hi|hi-Latn)\.md$")
_TITLE = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)


class SeedError(RAGKernelError):
    """A seed file is missing or does not match the engine's schema (names the file)."""


@dataclass(frozen=True)
class EngineSeed:
    """A `SettingsPatch` body: sign-up mode, branding (the deployer's product), rails."""

    settings: dict[str, Any]


@dataclass(frozen=True)
class PlanSeed:
    name: str
    body: dict[str, Any]  # a `CreatePlanRequest`


@dataclass(frozen=True)
class Business:
    name: str
    owner_name: str
    knowledge_base_name: str
    knowledge_base_description: str
    widget_key_name: str


@dataclass(frozen=True)
class AssistantSeed:
    template: str
    patch: dict[str, Any]  # an RFC 7396 merge patch for the draft
    rules: list[str]  # the instruction of every rule the patch sets


@dataclass(frozen=True)
class DocumentSeed:
    slug: str
    external_id: str
    title: str
    lang: str
    text: str


@dataclass(frozen=True)
class FaqSeed:
    lang: str
    title: str
    body: str


@dataclass(frozen=True)
class Question:
    lang: str
    text: str


@dataclass(frozen=True)
class Seed:
    engine: EngineSeed
    plan: PlanSeed
    business: Business
    assistant: AssistantSeed
    documents: list[DocumentSeed]
    faq: list[FaqSeed]
    questions: list[Question]


def _read(root: Traversable, name: str) -> str:
    try:
        return root.joinpath(name).read_text("utf-8")
    except (OSError, FileNotFoundError) as exc:
        raise SeedError(f"{name}: cannot be read") from exc


def _json(root: Traversable, name: str) -> Any:
    try:
        return json.loads(_read(root, name))
    except ValueError as exc:
        raise SeedError(f"{name}: not valid JSON ({exc})") from exc


def _merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """RFC 7396, used here only to check that the merged config validates."""
    out = copy.deepcopy(target)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_patch(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _strings(data: Any, name: str, *keys: str) -> dict[str, str]:
    if not isinstance(data, dict):
        raise SeedError(f"{name}: expected an object")
    missing = [k for k in keys if not isinstance(data.get(k), str) or not data[k].strip()]
    if missing:
        raise SeedError(f"{name}: {', '.join(missing)} must be non-empty text")
    return {k: data[k] for k in keys}


def _documents(root: Traversable) -> list[DocumentSeed]:
    out: list[DocumentSeed] = []
    docs = root.joinpath("docs")
    for entry in sorted(docs.iterdir(), key=lambda e: e.name):
        match = _DOC_NAME.match(entry.name)
        if match is None:
            raise SeedError(f"docs/{entry.name}: name must look like slug.en.md or slug.hi.md")
        text = entry.read_text("utf-8").strip()
        title = _TITLE.search(text)
        if title is None:
            raise SeedError(f"docs/{entry.name}: needs a '# Title' first line")
        out.append(
            DocumentSeed(
                slug=match["slug"],
                external_id=f"demo:{match['slug']}",
                title=title["title"],
                lang=match["lang"],
                text=text,
            )
        )
    ids = [d.external_id for d in out]
    if len(set(ids)) != len(ids):
        raise SeedError("docs: two documents share a slug")
    return out


def load(path: Traversable | None = None) -> Seed:
    """Read and validate the seed.

    Raises:
        SeedError: a file is missing, unreadable or does not validate (the message names it).
    """
    from pydantic import ValidationError

    from callobuzz_rag.api.routes.engine_settings import SettingsPatch
    from callobuzz_rag.api.schemas.articles import ArticleRequest
    from callobuzz_rag.api.schemas.plan import CreatePlanRequest
    from callobuzz_rag.conversation.schema import AssistantConfig

    root = path or DATA
    try:
        engine = _json(root, "engine.json")
        SettingsPatch.model_validate(engine)

        plan = _json(root, "plan.json")
        CreatePlanRequest.model_validate(plan)

        business = Business(
            **_strings(
                _json(root, "business.json"),
                "business.json",
                "name",
                "owner_name",
                "knowledge_base_name",
                "knowledge_base_description",
                "widget_key_name",
            )
        )

        raw_assistant = _json(root, "assistant.json")
        patch = raw_assistant.get("patch")
        template = raw_assistant.get("template")
        if not isinstance(patch, dict) or not isinstance(template, str):
            raise SeedError("assistant.json: needs a template name and a patch object")
        merged = _merge_patch(AssistantConfig().model_dump(mode="json"), patch)
        AssistantConfig.model_validate(merged)
        rules = [str(r["instruction"]) for r in patch.get("rules", [])]

        faq = [
            FaqSeed(lang=str(item["lang"]), title=str(item["title"]), body=str(item["body"]))
            for item in _json(root, "faq.json")
        ]
        for item in faq:
            ArticleRequest(kind="faq", title=item.title, body=item.body)

        questions = [
            Question(lang=str(item["lang"]), text=str(item["text"]))
            for item in _json(root, "questions.json")
        ]
        documents = _documents(root)
    except ValidationError as exc:
        raise SeedError(f"the seed does not match the engine's schema: {exc}") from exc
    except (KeyError, TypeError) as exc:
        raise SeedError(f"the seed has a malformed entry ({type(exc).__name__}: {exc})") from exc

    return Seed(
        engine=EngineSeed(settings=engine),
        plan=PlanSeed(name=str(plan["name"]), body=plan),
        business=business,
        assistant=AssistantSeed(template=template, patch=patch, rules=rules),
        documents=documents,
        faq=faq,
        questions=questions,
    )


def all_text(seed: Seed) -> str:
    """Every visitor-readable string of the seed, for the content checks."""
    parts: list[str] = [json.dumps(seed.engine.settings, ensure_ascii=False)]
    parts.append(json.dumps(seed.plan.body, ensure_ascii=False))
    parts.append(json.dumps(seed.assistant.patch, ensure_ascii=False))
    parts.extend([seed.business.name, seed.business.owner_name])
    parts.extend(d.text for d in seed.documents)
    parts.extend(f"{f.title}\n{f.body}" for f in seed.faq)
    parts.extend(q.text for q in seed.questions)
    return "\n".join(parts)
