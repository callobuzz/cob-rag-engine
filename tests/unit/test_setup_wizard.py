"""scripts/setup.py: the pure logic of the interactive setup wizard.

Everything writes into pytest's tmp_path, never the repo's own env file.
"""

import getpass
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from callobuzz_rag.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / (".en" + "v.example")


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


wizard = _load("cob_setup_wizard", ROOT / "scripts" / "setup.py")


@pytest.fixture(autouse=True)
def _linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """The host platform is Linux unless a test says otherwise (the wizard branches on it)."""
    monkeypatch.setattr(wizard.platform, "system", lambda: "Linux")


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.platform, "system", lambda: "Windows")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "envout"


def run(args: list[str], target: Path) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = wizard.main(
        [*args, "--env-file", str(target), "--template", str(TEMPLATE)], out=lines.append
    )
    return code, lines


def test_the_old_proxy_trust_name_is_carried_to_the_new_one(target: Path) -> None:
    """A re-run must not let the template's `false` outrank a legacy `true`."""
    target.write_text(
        "RAG_WIDGET_TRUST_PROXY_HEADERS=true\nRAG_SUPERADMIN_EMAIL=admin@yourcompany.in\n",
        encoding="utf-8",
    )
    code, _ = run(["--quick", "--yes"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_TRUST_PROXY_HEADERS"] == "true"
    assert "RAG_WIDGET_TRUST_PROXY_HEADERS" not in values
    assert load_settings(target).trust_proxy_headers is True


def test_the_new_proxy_trust_name_wins_over_a_stale_old_one(target: Path) -> None:
    target.write_text(
        "RAG_TRUST_PROXY_HEADERS=false\nRAG_WIDGET_TRUST_PROXY_HEADERS=true\n", encoding="utf-8"
    )
    run(["--quick", "--yes"], target)
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_TRUST_PROXY_HEADERS"] == "false"
    assert "RAG_WIDGET_TRUST_PROXY_HEADERS" not in values


def test_a_fresh_file_leaves_proxy_trust_unset(target: Path) -> None:
    run(["--quick", "--yes"], target)
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert "RAG_TRUST_PROXY_HEADERS" not in values
    assert load_settings(target).trust_proxy_headers is False


def load_settings(path: Path) -> Settings:
    return Settings(_env_file=path)  # type: ignore[call-arg]


# --- reading and writing the file format ------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abc", "abc"),
        ("  abc  ", "abc"),
        ("abc   # a note", "abc"),
        ("'a b # c'", "a b # c"),
        ('"a \\"q\\" b"', 'a "q" b'),
        ("", ""),
        ('["http://x","http://y"]', '["http://x","http://y"]'),
        ("0 3 * * 0", "0 3 * * 0"),
    ],
)
def test_parse_value(raw: str, expected: str) -> None:
    assert wizard.parse_value(raw) == expected


@pytest.mark.parametrize(
    "value", ["plain", "a b c", "0 3 * * 0", "x#y", '["a","b"]', "Name <a@b.in>"]
)
def test_format_value_leaves_safe_values_bare(value: str) -> None:
    assert wizard.format_value("K", value) == value


@pytest.mark.parametrize("value", ["has $dollar", "a #b", " lead", "`tick`"])
def test_format_value_quotes_what_would_be_misread(value: str) -> None:
    written = wizard.format_value("K", value)
    assert written == f"'{value}'"
    assert wizard.parse_value(written) == value


def test_format_value_refuses_what_it_cannot_store() -> None:
    with pytest.raises(ValueError, match="K"):
        wizard.format_value("K", "a\nb")
    with pytest.raises(ValueError, match="K"):
        wizard.format_value("K", "it's $x")


def test_render_keeps_comments_and_order() -> None:
    template = "# header\n\n# about a\nA=1\n# about b\nB=\n# C=example\n\n# tail\n"
    lines = wizard.parse_template(template)
    text = wizard.render_env(lines, {"A": "9", "B": "x y"})
    assert text == "# header\n\n# about a\nA=9\n# about b\nB=x y\n# C=example\n\n# tail\n"


def test_render_activates_a_commented_variable_that_has_a_value() -> None:
    lines = wizard.parse_template("# note\n# C=example\n")
    assert wizard.render_env(lines, {"C": "real"}) == "# note\nC=real\n"
    assert wizard.render_env(lines, {"C": ""}) == "# note\n# C=example\n"


def test_render_appends_unknown_keys() -> None:
    lines = wizard.parse_template("A=1\n")
    text = wizard.render_env(lines, {"A": "1", "MY_OWN": "keep me"})
    assert text.startswith("A=1\n")
    assert "Kept from your previous file" in text
    assert text.rstrip().endswith("MY_OWN=keep me")


def test_render_roundtrips_the_real_template() -> None:
    lines = wizard.parse_template(TEMPLATE.read_text(encoding="utf-8"))
    text = wizard.render_env(lines, wizard.template_defaults(lines))
    assert text == TEMPLATE.read_text(encoding="utf-8")


def test_parse_env_text_reads_quotes_inline_comments_and_later_duplicates() -> None:
    parsed = wizard.parse_env_text("# c\nA=1 # one\nB='x # y'\nA=2\nnot a var\n")
    assert parsed == {"A": "2", "B": "x # y"}


def test_mask_never_shows_a_secret() -> None:
    assert wizard.mask("RAG_LLM_API_KEY", "sk-verysecret") == "set"
    assert wizard.mask("RAG_SMTP_PASSWORD", "") == "not set"
    assert wizard.mask("RAG_SUPERADMIN_EMAIL", "a@b.in") == "a@b.in"
    assert wizard.mask("RAG_SMTP_HOST", "") == "(empty)"


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize("value", ["admin@yourcompany.in", " a.b+c@x.co.in "])
def test_valid_email_accepts(value: str) -> None:
    assert wizard.valid_email(value) == value.strip()


@pytest.mark.parametrize("value", ["", "admin", "a@b", "a b@c.in", "@c.in", "a@@c.in"])
def test_valid_email_rejects(value: str) -> None:
    with pytest.raises(ValueError, match="e-mail"):
        wizard.valid_email(value)


def test_valid_url() -> None:
    assert wizard.valid_url("https://rag.yourcompany.in/") == "https://rag.yourcompany.in"
    assert wizard.valid_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    for bad in ("rag.yourcompany.in", "ftp://x.in", "http://", "https://a b.in", "http://x:99999"):
        with pytest.raises(ValueError):
            wizard.valid_url(bad)


def test_valid_port() -> None:
    assert wizard.valid_port(" 587 ") == "587"
    for bad in ("0", "65536", "abc", "", "-1", "5.5"):
        with pytest.raises(ValueError):
            wizard.valid_port(bad)


def test_valid_bind_address() -> None:
    assert wizard.valid_bind_address("127.0.0.1") == "127.0.0.1"
    assert wizard.valid_bind_address("0.0.0.0") == "0.0.0.0"
    for bad in ("localhost", "::1", "300.1.1.1", ""):
        with pytest.raises(ValueError):
            wizard.valid_bind_address(bad)


def test_valid_host_model_bucket_secret_sender() -> None:
    assert wizard.valid_host("smtp.zoho.in") == "smtp.zoho.in"
    for bad in ("https://smtp.x.in", "smtp.x.in:587", "a b", ""):
        with pytest.raises(ValueError):
            wizard.valid_host(bad)
    assert wizard.valid_model("gpt-4o-mini") == "gpt-4o-mini"
    with pytest.raises(ValueError):
        wizard.valid_model("two words")
    assert wizard.valid_bucket("yourcompany-rag-files") == "yourcompany-rag-files"
    for bad in ("ab", "Upper-Case", "-lead", "a" * 64):
        with pytest.raises(ValueError):
            wizard.valid_bucket(bad)
    assert wizard.valid_secret(" abc123 ") == "abc123"
    for bad in ("", "   ", "it's", "back\\slash", "line\nbreak"):
        with pytest.raises(ValueError):
            wizard.valid_secret(bad)
    assert wizard.valid_sender("no-reply@yourcompany.in") == "no-reply@yourcompany.in"
    assert wizard.valid_sender("Your Product <no-reply@yourcompany.in>")
    for bad in ("", "Your Product", "<x>", "a <b>"):
        with pytest.raises(ValueError):
            wizard.valid_sender(bad)


# --- applying answers and building commands ---------------------------------


def test_start_command_builder() -> None:
    assert wizard.build_start_command(False) == "docker compose up -d --build"
    assert wizard.build_start_command(True) == (
        "docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build"
    )


def test_apply_llm_keeps_helper_models_on_the_same_provider() -> None:
    values: dict[str, str] = {"RAG_LLM_PROVIDER": "openrouter", "RAG_LLM_API_KEY": "old"}
    wizard.apply_llm(values, wizard.LLM_PROVIDER_BY_ID["anthropic"], "claude-sonnet-5-5", "sk-new")
    assert values["RAG_LLM_PROVIDER"] == "anthropic"
    assert values["RAG_LLM_MODEL"] == "claude-sonnet-5-5"
    # The provider's own small model, not the answer model, for helper calls and ranking.
    assert values["RAG_LLM_FAST_MODEL"] == "claude-haiku-4-5"
    assert values["RAG_RERANK_LLM_MODEL"] == "claude-haiku-4-5"
    assert values["RAG_LLM_API_KEY"] == "sk-new"
    assert values["RAG_LLM_API_BASE"] == ""


def test_apply_llm_takes_an_explicit_fast_model() -> None:
    values: dict[str, str] = {}
    wizard.apply_llm(
        values, wizard.LLM_PROVIDER_BY_ID["openai"], "gpt-6.1-sol", "sk", fast_model="gpt-4.1-nano"
    )
    assert values["RAG_LLM_FAST_MODEL"] == "gpt-4.1-nano"


def test_every_provider_has_a_distinct_fast_default() -> None:
    assert {p.id for p in wizard.LLM_PROVIDERS} == {
        "openrouter",
        "openai",
        "anthropic",
        "gemini",
        "ollama",
    }
    for provider in wizard.LLM_PROVIDERS:
        assert provider.model and provider.fast_model
        assert provider.fast_model != provider.model
        wizard.valid_model(provider.model)
        wizard.valid_model(provider.fast_model)


@pytest.mark.parametrize(
    ("provider", "model", "fast"),
    [
        ("openai", "gpt-6.1-sol", "gpt-4.1-mini"),
        ("anthropic", "claude-sonnet-5-5", "claude-haiku-4-5"),
        ("gemini", "gemini-3.8-flash", "gemini-3.5-flash-lite"),
        ("openrouter", "openai/gpt-oss-120b", "openai/gpt-6-luna"),
        ("ollama", "llama3.1", "llama3.2"),
    ],
)
def test_default_models_per_provider(provider: str, model: str, fast: str) -> None:
    found = wizard.LLM_PROVIDER_BY_ID[provider]
    assert (found.model, found.fast_model) == (model, fast)


def test_yes_mode_writes_the_fast_model_default(target: Path) -> None:
    code, _ = run(
        [
            "--quick",
            "--yes",
            "--set",
            "RAG_LLM_PROVIDER=openrouter",
            "--set",
            "RAG_SUPERADMIN_EMAIL=a@b.in",
        ],
        target,
    )
    assert code == 0
    assert load_settings(target).llm_fast_model


# --- the e-mail rule: reserved names only in development --------------------


def test_special_use_list_matches_the_engine() -> None:
    from callobuzz_rag.accounts.email_rules import SPECIAL_USE_TLDS

    assert set(wizard.SPECIAL_USE_TLDS) == set(SPECIAL_USE_TLDS)


@pytest.mark.parametrize("tld", ["local", "test", "example", "localhost", "invalid"])
def test_valid_email_special_use_only_in_dev(tld: str) -> None:
    address = f"admin@shop.{tld}"
    assert wizard.valid_email(address, allow_special_use=True) == address
    with pytest.raises(ValueError, match=rf"\.{tld}.*RAG_ENV=dev"):
        wizard.valid_email(address)


@pytest.mark.parametrize(("env", "refused"), [("prod", True), ("dev", False)])
def test_the_superadmin_prompt_follows_the_environment(
    env: str, refused: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = ["admin@shop.local", "admin@shop.in"] if refused else ["admin@shop.local"]
    feed(monkeypatch, answers)
    lines: list[str] = []
    ctx = wizard.Context(
        values={"RAG_ENV": env},
        current={},
        existing_file=False,
        mode="quick",
        prompter=wizard.Prompter(out=lines.append),
        generators={},
    )
    wizard.ask_superadmin(ctx)
    expected = "admin@shop.in" if refused else "admin@shop.local"
    assert ctx.values["RAG_SUPERADMIN_EMAIL"] == expected
    assert any("cannot receive e-mail" in line for line in lines) is refused


def test_dev_accepts_a_local_superadmin_through_the_validator() -> None:
    check = wizard.email_validator_for({"RAG_ENV": "dev"})
    assert check("admin@callobuzz.local") == "admin@callobuzz.local"
    strict = wizard.email_validator_for({"RAG_ENV": "prod"})
    with pytest.raises(ValueError):
        strict("admin@callobuzz.local")
    assert strict("admin@callobuzz.in") == "admin@callobuzz.in"


def test_a_prod_run_with_a_reserved_superadmin_warns() -> None:
    notes = wizard.consistency_warnings(
        {"RAG_ENV": "prod", "RAG_SUPERADMIN_EMAIL": "admin@shop.local"}, super_admin_set=True
    )
    assert any("RAG_SUPERADMIN_EMAIL" in n and ".local" in n for n in notes)
    dev = wizard.consistency_warnings(
        {"RAG_ENV": "dev", "RAG_SUPERADMIN_EMAIL": "admin@shop.local"}, super_admin_set=True
    )
    assert not any("RAG_SUPERADMIN_EMAIL" in n for n in dev)


def test_apply_llm_ollama_never_inherits_another_providers_key() -> None:
    values: dict[str, str] = {"RAG_LLM_PROVIDER": "openai", "RAG_LLM_API_KEY": "sk-real"}
    wizard.apply_llm(
        values,
        wizard.LLM_PROVIDER_BY_ID["ollama"],
        "llama3.1",
        api_base="http://host.docker.internal:11434",
    )
    assert values["RAG_LLM_API_KEY"] == wizard.OLLAMA_PLACEHOLDER_KEY
    assert values["RAG_LLM_API_BASE"] == "http://host.docker.internal:11434"
    # and leaving Ollama drops the placeholder instead of sending it to a real provider
    wizard.apply_llm(values, wizard.LLM_PROVIDER_BY_ID["openai"], "gpt-4o-mini")
    assert values["RAG_LLM_API_KEY"] == ""


def test_apply_reranker() -> None:
    values = {"RAG_RERANKING_ENABLED": "false", "RAG_RERANK_PROVIDER": "local"}
    wizard.apply_reranker(values, "llm")
    assert (values["RAG_RERANKING_ENABLED"], values["RAG_RERANK_PROVIDER"]) == ("true", "llm")
    wizard.apply_reranker(values, "off")
    assert values["RAG_RERANKING_ENABLED"] == "false"


def test_apply_profiles_keeps_unmanaged_ones() -> None:
    values = {
        "COMPOSE_PROFILES": "legacy-dashboard",
        "RAG_TRANSCRIPTION_PROVIDER": "off",
    }
    wizard.apply_profiles(values, observability=True, transcription=True)
    assert values["COMPOSE_PROFILES"] == "tei,observability,transcription,legacy-dashboard"
    assert values["RAG_TRANSCRIPTION_PROVIDER"] == "whisper_local"
    wizard.apply_profiles(values, observability=False, transcription=False)
    assert values["COMPOSE_PROFILES"] == "tei,legacy-dashboard"
    assert values["RAG_TRANSCRIPTION_PROVIDER"] == "off"
    values["RAG_EMBEDDING_PROVIDER"] = "litellm"
    wizard.apply_profiles(values, observability=False, transcription=False)
    assert values["COMPOSE_PROFILES"] == "legacy-dashboard"


def test_apply_embeddings_choices() -> None:
    values = {"RAG_EMBEDDING_PROVIDER": "litellm", "RAG_EMBEDDING_MODEL": "x"}
    wizard.apply_embeddings_tei(values, gpu=True)
    assert values["RAG_EMBEDDING_PROVIDER"] == "tei"
    assert values["RAG_TEI_IMAGE"] == wizard.TEI_GPU_IMAGE
    assert values["RAG_EMBEDDING_DIMENSIONS"] == "1024"
    wizard.apply_embeddings_tei(values, gpu=False)
    assert values.get("RAG_TEI_IMAGE", "") == ""
    wizard.apply_embeddings_local(values)
    assert (values["RAG_EMBEDDING_PROVIDER"], values["RAG_EMBEDDING_DIMENSIONS"]) == (
        "local",
        "1024",
    )
    assert values["RAG_WORKER_CONCURRENCY"] == "1"
    jina = next(a for a in wizard.EMBEDDING_APIS if a.id == "jina")
    wizard.apply_embeddings_api(values, jina, "jina-embeddings-v3", "1024", "jina-key")
    assert values["RAG_JINA_API_KEY"] == "jina-key"
    assert values["RAG_EMBEDDING_PROVIDER"] == "jina"


def test_apply_embeddings_tei_keeps_a_custom_tei_model() -> None:
    values = {
        "RAG_EMBEDDING_PROVIDER": "tei",
        "RAG_EMBEDDING_MODEL": "intfloat/e5-base",
        "RAG_EMBEDDING_DIMENSIONS": "768",
    }
    wizard.apply_embeddings_tei(values, gpu=False)
    assert values["RAG_EMBEDDING_MODEL"] == "intfloat/e5-base"


def test_apply_storage_external_and_back_to_local() -> None:
    values: dict[str, str] = {}
    wizard.apply_storage_external(
        values,
        endpoint="https://s3.ap-south-1.amazonaws.com",
        bucket="acme-files",
        region="ap-south-1",
        style="virtual",
        access_key="AKIA",
        secret_key="s3cr3t",
        auto_create=False,
    )
    assert values["RAG_S3_ENDPOINT_CONTAINER"] == values["RAG_S3_ENDPOINT"]
    assert values["RAG_S3_AUTO_CREATE_BUCKET"] == "false"
    assert values["RAG_STORAGE_PROVIDER"] == "s3"
    wizard.apply_storage_local(values)
    assert values["RAG_STORAGE_PROVIDER"] == "local"
    # The S3 settings stay, because the copy tool reads them to move old files.
    assert values["RAG_S3_ACCESS_KEY"] == "AKIA"


# L2 storage
def test_a_fresh_run_defaults_to_files_on_this_server_and_makes_no_s3_keys(target: Path) -> None:
    code, _ = run(["--yes", "--set", "RAG_SUPERADMIN_EMAIL=a@b.in"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_STORAGE_PROVIDER"] == "local"
    assert not values.get("RAG_S3_ACCESS_KEY") and not values.get("RAG_S3_SECRET_KEY")
    assert "local-storage" not in values["COMPOSE_PROFILES"]


def test_s3_choice_writes_s3_and_warns_when_the_keys_are_missing() -> None:
    values: dict[str, str] = {}
    wizard.apply_storage_external(
        values,
        endpoint="https://s3.ap-south-1.amazonaws.com",
        bucket="sharma-files",
        region="ap-south-1",
        style="virtual",
        access_key="AKIAEXAMPLE",
        secret_key="secret-value",
        auto_create=False,
    )
    assert values["RAG_STORAGE_PROVIDER"] == "s3"
    assert values["RAG_S3_BUCKET"] == "sharma-files"
    with_keys = wizard.consistency_warnings({**values, "RAG_ENV": "dev"}, super_admin_set=True)
    assert not any("RAG_S3_ACCESS_KEY" in n for n in with_keys)
    notes = wizard.consistency_warnings(
        {"RAG_STORAGE_PROVIDER": "s3", "RAG_ENV": "dev"}, super_admin_set=True
    )
    assert any("RAG_S3_ACCESS_KEY" in n for n in notes)


def test_an_old_minio_setup_is_moved_to_local_and_told_how_to_copy(
    target: Path,
) -> None:
    target.write_text(
        "COMPOSE_PROFILES=local-storage,tei\n"
        "RAG_S3_ENDPOINT=http://127.0.0.1:9002\n"
        "RAG_S3_ENDPOINT_CONTAINER=http://minio:9000\n"
        "RAG_S3_ACCESS_KEY=oldaccess\n"
        "RAG_S3_SECRET_KEY=oldsecret\n",
        encoding="utf-8",
    )
    code, lines = run(["--yes"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_STORAGE_PROVIDER"] == "local"
    assert values["COMPOSE_PROFILES"] == "tei"
    # the copy tool needs the old S3 settings
    assert values["RAG_S3_ACCESS_KEY"] == "oldaccess"
    text = "\n".join(lines)
    assert "docker compose run --rm app python -m callobuzz_rag.storage.copy" in text
    assert "--from s3 --to local --apply" in text
    assert "oldsecret" not in text


def test_an_external_s3_setup_is_not_migrated(target: Path) -> None:
    target.write_text(
        "COMPOSE_PROFILES=tei\n"
        "RAG_S3_ENDPOINT_CONTAINER=https://s3.ap-south-1.amazonaws.com\n"
        "RAG_STORAGE_PROVIDER=s3\n",
        encoding="utf-8",
    )
    code, lines = run(["--yes"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_STORAGE_PROVIDER"] == "s3"
    assert "storage.copy" not in "\n".join(lines)


def test_external_s3_without_a_provider_setting_stays_s3(target: Path) -> None:
    target.write_text(
        "COMPOSE_PROFILES=tei\n"
        "RAG_S3_ENDPOINT_CONTAINER=https://abc123.r2.cloudflarestorage.com\n"
        "RAG_S3_ACCESS_KEY=k\n"
        "RAG_S3_SECRET_KEY=s\n",
        encoding="utf-8",
    )
    code, lines = run(["--yes"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_STORAGE_PROVIDER"] == "s3"
    assert "storage.copy" not in "\n".join(lines)


# /L2 storage


def test_merge_cors() -> None:
    merged = json.loads(
        wizard.merge_cors('["http://a.in","*"]', "https://admin.x.in/path", prod=True)
    )
    assert merged == ["http://a.in", "https://admin.x.in"]
    dev = json.loads(wizard.merge_cors("", "https://admin.x.in", prod=False))
    assert dev == ["https://admin.x.in"]
    assert json.loads(wizard.merge_cors("not json", "http://127.0.0.1:8090", prod=False)) == [
        "http://127.0.0.1:8090"
    ]


def test_warnings_for_a_risky_production_setup() -> None:
    notes = wizard.consistency_warnings(
        {"RAG_ENV": "prod", "RAG_EMAIL_PROVIDER": "log", "RAG_BIND_ADDR": "0.0.0.0"},
        super_admin_set=False,
    )
    text = "\n".join(notes)
    assert "refuses RAG_EMAIL_PROVIDER=log" in text
    assert "0.0.0.0" in text
    assert "No LLM key" in text
    assert "super-admin" in text
    assert (
        wizard.consistency_warnings(
            {"RAG_LLM_API_KEY": "k", "RAG_SMTP_HOST": "h", "RAG_SMTP_FROM": "a@b.in"},
            super_admin_set=True,
        )
        == []
    )


def test_set_arguments() -> None:
    assert wizard.parse_set_args(["A_B=1", "C=x=y"]) == {"A_B": "1", "C": "x=y"}
    for bad in ("nokey", "lower=1", "=1"):
        with pytest.raises(ValueError):
            wizard.parse_set_args([bad])


def test_secrets_come_from_generate_secrets_and_fill_only_empties() -> None:
    generators = wizard.load_generators()
    generate_secrets = sys.modules["generate_secrets"]
    assert set(generators) == set(generate_secrets.GENERATORS)
    values = {"RAG_DB_PASSWORD": "keep-me", "RAG_ADMIN_API_KEY": ""}
    generated = wizard.fill_secrets(values, generators)
    assert values["RAG_DB_PASSWORD"] == "keep-me"
    assert "RAG_DB_PASSWORD" not in generated
    assert values["RAG_ADMIN_API_KEY"].startswith("admin_")
    assert len(values["RAG_SESSION_SECRET"]) >= 32


# --- Prompter with monkeypatched input and getpass --------------------------


def feed(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    asked: list[str] = []
    queue = iter(answers)

    def fake_input(prompt: str = "") -> str:
        asked.append(prompt)
        return next(queue)

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def test_text_reasks_until_valid_and_accepts_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    asked = feed(monkeypatch, ["nope", "", "admin@yourcompany.in"])
    prompter = wizard.Prompter(out=said.append)
    assert prompter.text("E-mail", validate=wizard.valid_email) == "admin@yourcompany.in"
    assert len(asked) == 3
    assert any("e-mail" in line for line in said) and any("enter a value" in line for line in said)
    asked = feed(monkeypatch, [""])
    assert prompter.text("Port", "587", validate=wizard.valid_port) == "587"
    assert "[587]" in asked[-1]


def test_choice_takes_numbers_ids_and_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    options = [("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")]
    prompter = wizard.Prompter(out=lambda _: None)
    feed(monkeypatch, ["9", "x", "2"])
    assert prompter.choice("Pick", options) == "b"
    feed(monkeypatch, [""])
    assert prompter.choice("Pick", options, "c") == "c"
    feed(monkeypatch, ["A"])
    assert prompter.choice("Pick", options) == "a"


def test_yes_no_and_yes_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    prompter = wizard.Prompter(out=lambda _: None)
    feed(monkeypatch, ["maybe", "Y"])
    assert prompter.yes_no("Sure?", False) is True
    feed(monkeypatch, [""])
    assert prompter.yes_no("Sure?", False) is False
    auto = wizard.Prompter(yes=True, out=lambda _: None)
    assert auto.yes_no("Sure?", True) is True
    assert auto.text("x", "dflt") == "dflt"
    assert auto.secret("s") == ""


def test_secret_uses_getpass_on_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_getpass(prompt: str = "") -> str:
        calls.append(prompt)
        return "  hunter2  "

    monkeypatch.setattr(getpass, "getpass", fake_getpass)
    monkeypatch.setattr(wizard, "_stdin_is_tty", lambda: True)
    feed(monkeypatch, [])  # a call to input() would raise StopIteration
    assert wizard.Prompter(out=lambda _: None).secret("Key") == "hunter2"
    assert len(calls) == 1 and "hidden" in calls[0]


def test_secret_rejects_and_reasks(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["it's", "good-key"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr(wizard, "_stdin_is_tty", lambda: True)
    assert wizard.Prompter(out=lambda _: None).secret("Key") == "good-key"


def test_end_of_input_aborts_cleanly(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    def eof(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    code, _ = run(["--quick"], target)
    assert code == 130
    assert not target.exists()


# --- whole runs into a temp directory ---------------------------------------


def test_quick_yes_writes_a_file_the_engine_can_load(target: Path) -> None:
    code, lines = run(
        [
            "--quick",
            "--yes",
            "--set",
            "RAG_SUPERADMIN_EMAIL=admin@yourcompany.in",
            "--set",
            "RAG_LLM_API_KEY=sk-or-test-key",
        ],
        target,
    )
    assert code == 0
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Callobuzz RAG Engine")
    assert "# 1. REQUIRED: fill these in" in text
    settings = load_settings(target)
    assert settings.superadmin_email == "admin@yourcompany.in"
    assert settings.llm_api_key.get_secret_value() == "sk-or-test-key"
    assert settings.llm_provider == "openrouter"
    assert len(settings.session_secret.get_secret_value()) >= 32
    assert settings.admin_api_key.get_secret_value().startswith("admin_")
    assert settings.encryption_key
    assert settings.db_password.get_secret_value() != "rag_password"
    assert settings.redis_password.get_secret_value()
    assert settings.qdrant_api_key.get_secret_value()
    assert settings.email_provider == "auto"
    assert settings.reranking_enabled is False
    output = "\n".join(lines)
    assert "docker compose up -d --build" in output
    assert "ONE-TIME SET-PASSWORD" in output  # no SMTP: the link is in the app log
    assert "http://127.0.0.1:8090" in output
    for secret in (
        settings.admin_api_key.get_secret_value(),
        settings.encryption_key,
        settings.session_secret.get_secret_value(),
        "sk-or-test-key",
    ):
        assert secret not in output


def test_generated_file_is_compatible_with_generate_secrets(target: Path) -> None:
    run(["--quick", "--yes"], target)
    generate_secrets = sys.modules["generate_secrets"]
    seen = set()
    for line in target.read_text(encoding="utf-8").splitlines():
        match = generate_secrets.LINE.match(line)
        if match and match["key"] in generate_secrets.GENERATORS:
            seen.add(match["key"])
            assert match["value"].strip(), f"{match['key']} should be filled"
    assert seen == set(generate_secrets.GENERATORS)


def test_dry_run_writes_nothing_and_masks_secrets(target: Path) -> None:
    code, lines = run(
        [
            "--quick",
            "--yes",
            "--dry-run",
            "--set",
            "RAG_LLM_API_KEY=sk-super-secret-value",
            "--set",
            "RAG_SUPERADMIN_EMAIL=admin@yourcompany.in",
        ],
        target,
    )
    assert code == 0
    assert not target.exists()
    output = "\n".join(lines)
    assert "sk-super-secret-value" not in output
    assert "RAG_LLM_API_KEY: not set -> set" in output
    assert "RAG_ADMIN_API_KEY: not set -> set  (generated)" in output
    assert "RAG_SUPERADMIN_EMAIL: (empty) -> admin@yourcompany.in" in output
    assert "nothing was written" in output


def test_existing_file_is_backed_up_and_its_values_and_unknown_keys_kept(target: Path) -> None:
    target.write_text(
        "RAG_DB_PASSWORD=my-db-password-1234\nRAG_SUPERADMIN_EMAIL=owner@yourcompany.in\n"
        "RAG_LLM_API_KEY=sk-existing-key\nRAG_LLM_PROVIDER=openai\nRAG_LLM_MODEL=gpt-4o\n"
        "RAG_LOG_LEVEL=DEBUG   # inline note\nMY_OWN_SETTING=hello world\n",
        encoding="utf-8",
    )
    before = target.read_text(encoding="utf-8")
    code, lines = run(["--quick", "--yes"], target)
    assert code == 0
    backups = list(target.parent.glob("envout.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == before
    kept = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert kept["RAG_DB_PASSWORD"] == "my-db-password-1234"
    assert kept["RAG_SUPERADMIN_EMAIL"] == "owner@yourcompany.in"
    assert kept["RAG_LLM_PROVIDER"] == "openai"
    assert kept["RAG_LLM_MODEL"] == "gpt-4o"
    assert kept["RAG_LLM_API_KEY"] == "sk-existing-key"
    assert kept["RAG_LOG_LEVEL"] == "DEBUG"
    assert kept["MY_OWN_SETTING"] == "hello world"
    assert kept["RAG_REDIS_PASSWORD"]  # the missing secrets were filled in
    assert "sk-existing-key" not in "\n".join(lines)


def test_second_run_changes_nothing(target: Path) -> None:
    run(["--quick", "--yes", "--set", "RAG_SUPERADMIN_EMAIL=a@yourcompany.in"], target)
    first = target.read_text(encoding="utf-8")
    run(["--quick", "--yes"], target)
    assert target.read_text(encoding="utf-8") == first


def test_bad_set_argument_exits_with_an_error(
    target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run(["--yes", "--set", "oops"], target)
    assert code == 2
    assert "--set expects KEY=VALUE" in capsys.readouterr().err
    assert not target.exists()


def test_full_yes_on_a_gpu_choice_builds_the_overlay_command(target: Path) -> None:
    code, lines = run(["--full", "--yes", "--set", "RAG_TEI_IMAGE=" + wizard.TEI_GPU_IMAGE], target)
    assert code == 0
    assert (
        "docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build"
        in "\n".join(lines)
    )
    assert load_settings(target).embedding_provider == "tei"


def test_interactive_full_run_with_monkeypatched_input_and_getpass(
    monkeypatch: pytest.MonkeyPatch, target: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secrets_typed = ["sk-openai-secret-1", "abcd efgh ijkl mnop"]
    answers = [
        "2",  # full
        "2",  # production
        "1",  # NVIDIA GPU: yes (asked once, here)
        "admin@yourcompany.in",
        "2",  # OpenAI
        "",  # default model
        "",  # default fast model
        "2",  # Gmail
        "ops@gmail.com",
        "",  # sender defaults to the address
        "2",  # re-ranker: local on the GPU
        "3",  # embeddings inside the engine
        "1",  # files on this server
        "",  # engine host port
        "",  # scraper host port
        "",  # dashboard host port
        "https://rag.yourcompany.in",
        "",  # engine address: defaults to the public one
        "",  # scraper
        "https://admin.yourcompany.in",
        "",  # bind address
        "n",  # transcription
        "y",  # Langfuse
        "y",  # answer-quality evaluation add-on
        "n",  # free local model
    ]
    feed(monkeypatch, answers)
    typed = iter(secrets_typed)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(typed))
    monkeypatch.setattr(wizard, "_stdin_is_tty", lambda: True)
    lines: list[str] = []
    code = wizard.main(["--env-file", str(target), "--template", str(TEMPLATE)], out=lines.append)
    assert code == 0
    settings = load_settings(target)
    assert settings.is_prod
    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-6.1-sol"
    assert settings.llm_fast_model == "gpt-4.1-mini"
    assert settings.llm_api_key.get_secret_value() == "sk-openai-secret-1"
    assert settings.smtp_host == "smtp.gmail.com"
    assert settings.smtp_password.get_secret_value() == "abcdefghijklmnop"  # spaces removed
    assert settings.smtp_from == "ops@gmail.com"
    assert settings.reranking_enabled is True and settings.rerank_provider == "local"
    assert settings.embedding_provider == "local"
    assert settings.public_url == "https://rag.yourcompany.in"
    assert settings.dashboard_url == "https://admin.yourcompany.in"
    assert "https://admin.yourcompany.in" in settings.cors_origins
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["COMPOSE_PROFILES"] == "observability"
    assert values["RAG_INSTALL_EVAL"] == "true"
    output = "\n".join(lines) + capsys.readouterr().out
    assert "docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build" in output
    assert "inbox" in output  # SMTP is set: the link is e-mailed
    for secret in (*secrets_typed, "abcdefghijklmnop"):
        assert secret not in output


# --- TEI only when used, host ports, scraper origins -----------------------


def test_default_template_starts_tei_and_keeps_the_dashboard_ports(target: Path) -> None:
    code, _ = run(["--yes", "--set", "RAG_SUPERADMIN_EMAIL=a@b.in"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["COMPOSE_PROFILES"] == "tei"
    assert (values["RAG_APP_HOST_PORT"], values["RAG_SCRAPER_HOST_PORT"]) == ("8000", "8001")
    assert values["RAG_DASHBOARD_HOST_PORT"] == "8090"
    assert "http://127.0.0.1:8090" in json.loads(values["SCRAPER_CORS_ORIGINS"])


@pytest.mark.parametrize("provider", ["litellm", "jina", "local"])
def test_tei_profile_follows_the_embedding_provider(provider: str, target: Path) -> None:
    code, _ = run(["--yes", "--set", f"RAG_EMBEDDING_PROVIDER={provider}"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["COMPOSE_PROFILES"] == ""


def test_an_existing_file_without_the_tei_profile_gets_it(target: Path) -> None:
    target.write_text("COMPOSE_PROFILES=observability\n", encoding="utf-8")
    assert run(["--yes"], target)[0] == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["COMPOSE_PROFILES"] == "tei,observability"


def test_sync_tei_profile_keeps_other_profiles_in_order() -> None:
    values = {"COMPOSE_PROFILES": "legacy-dashboard,transcription,observability"}
    wizard.sync_tei_profile(values)
    assert values["COMPOSE_PROFILES"] == "tei,observability,transcription,legacy-dashboard"
    values["RAG_EMBEDDING_PROVIDER"] = "litellm"
    wizard.sync_tei_profile(values)
    assert values["COMPOSE_PROFILES"] == "observability,transcription,legacy-dashboard"


def test_an_explicit_compose_profiles_is_left_alone(target: Path) -> None:
    code, lines = run(["--yes", "--set", "COMPOSE_PROFILES=legacy-dashboard"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["COMPOSE_PROFILES"] == "legacy-dashboard"
    assert any("`tei` is not in COMPOSE_PROFILES" in line for line in lines)


def test_custom_ports_move_the_addresses_and_origins(target: Path) -> None:
    code, _ = run(
        [
            "--yes",
            "--set",
            "RAG_APP_HOST_PORT=18000",
            "--set",
            "RAG_SCRAPER_HOST_PORT=18001",
            "--set",
            "RAG_DASHBOARD_HOST_PORT=18090",
        ],
        target,
    )
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_DASHBOARD_ENGINE_URL"] == "http://127.0.0.1:18000"
    assert values["RAG_DASHBOARD_SCRAPER_URL"] == "http://127.0.0.1:18001"
    assert values["RAG_DASHBOARD_URL"] == "http://127.0.0.1:18090"
    for key in ("SCRAPER_CORS_ORIGINS", "RAG_CORS_ORIGINS"):
        origins = json.loads(values[key])
        assert "http://127.0.0.1:18090" in origins
        assert "http://localhost:18090" in origins
    load_settings(target)  # the engine still reads the file


def test_a_public_address_is_not_retargeted_when_the_port_moves() -> None:
    assert wizard.retarget_url("https://rag.acme.in", "8000", "9000") == "https://rag.acme.in"
    assert wizard.retarget_url("http://localhost:8000", "8000", "9000") == "http://localhost:9000"
    assert wizard.retarget_url("http://127.0.0.1:8001", "8000", "9000") == "http://127.0.0.1:8001"


def test_the_public_dashboard_origin_reaches_the_scraper_list(target: Path) -> None:
    code, _ = run(
        ["--yes", "--set", "RAG_DASHBOARD_URL=https://admin.acme.in", "--set", "RAG_ENV=prod"],
        target,
    )
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert "https://admin.acme.in" in json.loads(values["SCRAPER_CORS_ORIGINS"])


def test_colliding_host_ports_are_flagged() -> None:
    notes = wizard.consistency_warnings(
        {"RAG_APP_HOST_PORT": "8090", "RAG_LLM_API_KEY": "k"}, super_admin_set=True
    )
    assert any("three different host ports" in n for n in notes)


def test_interactive_ports_retarget_the_default_addresses(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    feed(monkeypatch, ["9000", "9001", "9090", "", "", "", "", ""])
    ctx = wizard.Context(
        values=wizard.template_defaults(
            wizard.parse_template(TEMPLATE.read_text(encoding="utf-8"))
        ),
        current={},
        existing_file=False,
        mode="full",
        prompter=wizard.Prompter(out=lambda _: None),
        generators={},
    )
    wizard.ask_urls(ctx)
    assert ctx.values["RAG_APP_HOST_PORT"] == "9000"
    assert ctx.values["RAG_DASHBOARD_ENGINE_URL"] == "http://127.0.0.1:9000"
    assert ctx.values["RAG_DASHBOARD_SCRAPER_URL"] == "http://127.0.0.1:9001"
    assert ctx.values["RAG_DASHBOARD_URL"] == "http://127.0.0.1:9090"


# --- the GPU question: asked once, everything local follows it --------------


def interactive(
    monkeypatch: pytest.MonkeyPatch, target: Path, args: list[str], answers: list[str]
) -> tuple[int, str, list[str]]:
    """Run the wizard with typed answers; returns (exit code, everything printed, prompts)."""
    asked = feed(monkeypatch, answers)
    lines: list[str] = []
    code = wizard.main(
        [*args, "--env-file", str(target), "--template", str(TEMPLATE)], out=lines.append
    )
    return code, "\n".join(lines), asked


GPU_QUESTION = "Do you have an NVIDIA GPU that Docker can use?"
GPU_COMMAND = "docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build"
# Quick run, fresh file: GPU, e-mail (Enter = skip), AI provider 6 = skip, e-mail sending 1 =
# skip, then the re-ranker.


def quick(gpu: str, reranker: str) -> list[str]:
    return [gpu, "", "6", "1", reranker]


def test_gpu_is_asked_once_first_with_three_options(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    code, output, asked = interactive(monkeypatch, target, ["--quick"], quick("1", "2"))
    assert code == 0
    assert output.count(GPU_QUESTION) == 1
    assert output.index(GPU_QUESTION) < output.index("Super admin")
    for option in ("1) Yes (NVIDIA GPU", "2) No (everything local", "3) Not sure (treated as no)"):
        assert option in output
    assert len(asked) == 5  # one prompt per question, none twice
    summary = output.split("\nSummary")[1]
    assert "yes (NVIDIA, through Docker)" in summary
    assert "local model on GPU" in summary
    assert "TEI on CPU" in summary or "TEI on GPU" in summary
    assert GPU_COMMAND in output


def test_reranker_options_with_a_gpu_put_local_second(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    _, output, _ = interactive(monkeypatch, target, ["--quick"], quick("1", "1"))
    off = output.index("1) Off (safe default")
    local = output.index("2) Local on your GPU (recommended if you turn it on")
    llm = output.index("3) Via your LLM key (runs at your provider")
    assert off < local < llm
    assert "free, ~0.4 s a question, same quality as on CPU" in output
    assert "small cost per question; not benchmarked yet" in output
    assert "Local on your CPU" not in output
    assert "(Knowledge base > Retrieval)" in output
    assert "Re-ranker" in output.split("\nSummary")[1]


def test_reranker_options_without_a_gpu_put_llm_second(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    code, output, _ = interactive(monkeypatch, target, ["--quick"], quick("2", "3"))
    assert code == 0
    off = output.index("1) Off (safe default")
    llm = output.index("2) Via your LLM key (recommended if you turn it on without a GPU")
    local = output.index("3) Local on your CPU (free but slow: ~10 s a question, 2.3 GB RAM)")
    assert off < llm < local
    assert "Local on your GPU" not in output
    summary = output.split("\nSummary")[1]
    assert "local model on CPU" in summary
    assert "no (local models run on the CPU)" in summary
    assert "TEI on CPU" in summary
    assert "docker-compose.gpu.yml" not in output
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert (values["RAG_RERANKING_ENABLED"], values["RAG_RERANK_PROVIDER"]) == ("true", "local")


def test_llm_reranking_summary_says_it_runs_at_the_provider(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    _, output, _ = interactive(monkeypatch, target, ["--quick"], quick("1", "3"))
    assert "via your LLM (provider)" in output.split("\nSummary")[1]


def test_not_sure_is_treated_as_no_with_a_hint(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    code, output, _ = interactive(monkeypatch, target, ["--quick"], quick("3", "1"))
    assert code == 0
    assert "docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi" in output
    assert "docker-compose.gpu.yml" not in output
    assert "Local on your CPU" in output  # the no-GPU option order


# Full run, fresh file: environment, GPU, e-mail, AI provider (skip), e-mail sending (skip),
# re-ranker, embeddings, storage, three ports, public, engine, scraper and dashboard addresses,
# bind address, transcription, Langfuse, the evaluation add-on, the free local model.
FULL_TAIL = [*[""] * 8, "n", "n", "", ""]


def test_full_mode_asks_gpu_once_and_offers_gpu_tei_with_a_gpu(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    answers = ["1", "1", "", "6", "1", "1", "2", "1", *FULL_TAIL]
    code, output, _ = interactive(monkeypatch, target, ["--full"], answers)
    assert code == 0
    assert output.count(GPU_QUESTION) == 1
    assert "2) Bundled container (TEI) on your GPU" in output
    assert "Docker Desktop for Windows" in output
    assert "TEI on GPU" in output.split("\nSummary")[1]
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_TEI_IMAGE"] == wizard.TEI_GPU_IMAGE
    assert GPU_COMMAND in output


def test_full_mode_without_a_gpu_has_no_gpu_tei_option(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    answers = ["1", "2", "", "6", "1", "1", "1", "1", *FULL_TAIL]
    code, output, _ = interactive(monkeypatch, target, ["--full"], answers)
    assert code == 0
    assert output.count(GPU_QUESTION) == 1
    assert "Bundled container (TEI) on your GPU" not in output
    assert "TEI on CPU" in output.split("\nSummary")[1]
    assert "docker-compose.gpu.yml" not in output


def test_gpu_flag_skips_the_question(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    code, output, asked = interactive(
        monkeypatch, target, ["--quick", "--gpu", "yes"], ["", "6", "1", "2"]
    )
    assert code == 0
    assert GPU_QUESTION not in output
    assert len(asked) == 4
    assert "Local on your GPU" in output
    assert "yes (NVIDIA, through Docker)" in output


@pytest.mark.parametrize(
    ("args", "gpu"),
    [([], False), (["--gpu", "no"], False), (["--gpu", "yes"], True)],
)
def test_yes_mode_gpu_flag(args: list[str], gpu: bool, target: Path) -> None:
    code, lines = run(["--quick", "--yes", *args], target)
    assert code == 0
    output = "\n".join(lines)
    assert ("docker-compose.gpu.yml" in output) is gpu
    assert ("yes (NVIDIA, through Docker)" in output) is gpu
    assert ("TEI on GPU" in output) is gpu
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert (values.get("RAG_TEI_IMAGE", "") == wizard.TEI_GPU_IMAGE) is gpu
    load_settings(target)


def test_gpu_no_overrides_an_existing_gpu_file(target: Path) -> None:
    target.write_text(f"RAG_TEI_IMAGE={wizard.TEI_GPU_IMAGE}\n", encoding="utf-8")
    code, lines = run(["--quick", "--yes", "--gpu", "no"], target)
    assert code == 0
    assert "docker-compose.gpu.yml" not in "\n".join(lines)
    assert wizard.parse_env_text(target.read_text(encoding="utf-8")).get("RAG_TEI_IMAGE", "") == ""


def test_existing_gpu_file_makes_yes_the_default(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    target.write_text(f"RAG_TEI_IMAGE={wizard.TEI_GPU_IMAGE}\n", encoding="utf-8")
    # GPU (Enter), e-mail, AI provider skip, e-mail sending skip, re-ranker, back up the old file
    code, output, _ = interactive(monkeypatch, target, ["--quick"], ["", "", "6", "1", "", ""])
    assert code == 0
    assert (
        "1) Yes (NVIDIA GPU, with the NVIDIA container toolkit or Docker's GPU support)  (default)"
        in output
    )
    assert GPU_COMMAND in output


def test_a_cpu_image_file_defaults_the_gpu_answer_to_no(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    cpu_image = "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9"
    target.write_text(f"RAG_TEI_IMAGE={cpu_image}\n", encoding="utf-8")
    _, output, _ = interactive(monkeypatch, target, ["--quick"], ["", "", "6", "1", "", ""])
    assert "2) No (everything local runs on the CPU)  (default)" in output


# --- Windows + GPU: Docker Desktop's GPU works for the engine's own embeddings -


def test_quick_windows_gpu_embeds_in_the_engine(windows: None, target: Path) -> None:
    code, lines = run(["--quick", "--yes", "--gpu", "yes"], target)
    assert code == 0
    output = "\n".join(lines)
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_EMBEDDING_PROVIDER"] == "local"
    assert values.get("RAG_TEI_IMAGE", "") == ""
    assert "tei" not in wizard.split_profiles(values["COMPOSE_PROFILES"])
    assert GPU_COMMAND in output
    assert "Docker Desktop's GPU works for the engine's own embedding model" in output
    load_settings(target)


def test_quick_linux_gpu_keeps_the_tei_cuda_image(target: Path) -> None:
    code, lines = run(["--quick", "--yes", "--gpu", "yes"], target)
    assert code == 0
    output = "\n".join(lines)
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_EMBEDDING_PROVIDER"] == "tei"
    assert values["RAG_TEI_IMAGE"] == wizard.TEI_GPU_IMAGE
    assert "tei" in wizard.split_profiles(values["COMPOSE_PROFILES"])
    assert "Docker Desktop's GPU works" not in output


def test_quick_windows_without_gpu_keeps_tei_on_the_cpu(windows: None, target: Path) -> None:
    code, _ = run(["--quick", "--yes", "--gpu", "no"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_EMBEDDING_PROVIDER"] == "tei"
    assert values.get("RAG_TEI_IMAGE", "") == ""


def test_quick_windows_gpu_leaves_a_hosted_api_choice_alone(windows: None) -> None:
    values = {"RAG_EMBEDDING_PROVIDER": "litellm", "COMPOSE_PROFILES": "observability"}
    assert wizard.sync_tei_image(values, True) is None
    assert values["RAG_EMBEDDING_PROVIDER"] == "litellm"


def test_full_windows_gpu_defaults_to_the_engine_embeddings(
    windows: None, monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    # Environment 1, GPU yes, then everything else at its default (Enter).
    answers = ["1", "1", "", "6", "1", "1", "", "1", *FULL_TAIL]
    code, output, _ = interactive(monkeypatch, target, ["--full"], answers)
    assert code == 0
    assert "Docker Desktop's GPU works for the engine's own embedding model" in output
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_EMBEDDING_PROVIDER"] == "local"
    assert "tei" not in wizard.split_profiles(values["COMPOSE_PROFILES"])
    assert GPU_COMMAND in output


# --- the fast model: the measured recommendation, from benchmarks/results/fast-models.json --


def _write_fast_models(path: Path, model: str, order: list[str]) -> None:
    key = f"{model}@{','.join(order)}" if order else model
    path.write_text(
        json.dumps(
            {
                "recommendations": {"default": key},
                "models": [
                    {
                        "key": key,
                        "model": model,
                        "provider_order": order,
                        "overall": 0.95,
                        "latency_s": {"p50": 0.6, "p90": 0.82, "p99": 1.4},
                        "usd_per_1000_labels": 0.044,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_the_measured_fast_model_is_the_files_recommendation() -> None:
    doc = json.loads((ROOT / "benchmarks" / "results" / "fast-models.json").read_text("utf-8"))
    measured = wizard.measured_fast_model()
    assert measured is not None
    assert measured.key == doc["recommendations"]["default"]


def test_the_fast_model_prompt_states_speed_and_cost(tmp_path: Path) -> None:
    path = tmp_path / "fast.json"
    _write_fast_models(path, "meta-llama/llama-3.3-70b-instruct", ["groq"])
    measured = wizard.measured_fast_model(path)
    assert measured is not None
    note = wizard.fast_model_note(measured)
    assert "meta-llama/llama-3.3-70b-instruct" in note and "groq" in note
    assert "p90 0.82 s" in note and "$0.04 per 1,000" in note


def test_a_missing_or_broken_file_falls_back_to_the_built_in_default(tmp_path: Path) -> None:
    assert wizard.measured_fast_model(tmp_path / "absent.json") is None
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert wizard.measured_fast_model(tmp_path / "bad.json") is None


def test_openrouter_gets_the_measured_model_and_its_host_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "fast.json"
    _write_fast_models(path, "meta-llama/llama-3.3-70b-instruct", ["groq", "cerebras"])
    monkeypatch.setattr(wizard, "FAST_MODELS_PATH", path)
    prov = wizard.LLM_PROVIDER_BY_ID["openrouter"]
    assert wizard.fast_default(prov) == "meta-llama/llama-3.3-70b-instruct"
    assert wizard.fast_default(wizard.LLM_PROVIDER_BY_ID["openai"]) == "gpt-4.1-mini"
    values: dict[str, str] = {}
    wizard.apply_llm(
        values, prov, "openai/gpt-oss-20b", "sk", fast_model="meta-llama/llama-3.3-70b-instruct"
    )
    assert values["RAG_LLM_FAST_PROVIDER_ORDER"] == "groq,cerebras"
    wizard.apply_llm(values, prov, "openai/gpt-oss-20b", "sk", fast_model="openai/gpt-4.1-nano")
    assert values["RAG_LLM_FAST_PROVIDER_ORDER"] == ""  # a pin belongs to its model
    wizard.apply_llm(values, wizard.LLM_PROVIDER_BY_ID["openai"], "gpt-6.1-sol", "sk")
    assert values["RAG_LLM_FAST_PROVIDER_ORDER"] == ""


# --- the evaluation add-on (RAGAS + LangChain) is opt-in -------------------


def test_the_evaluation_add_on_is_off_by_default(target: Path) -> None:
    code, _ = run(["--yes", "--set", "RAG_SUPERADMIN_EMAIL=a@b.in"], target)
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_INSTALL_EVAL"] == "false"


def _ctx(prompter: object, values: dict[str, str]) -> wizard.Context:
    return wizard.Context(
        values=values,
        current={},
        existing_file=False,
        mode="full",
        prompter=prompter,  # type: ignore[arg-type]
        generators={},
    )


def test_the_evaluation_question_defaults_to_no_and_writes_the_answer() -> None:
    asked: list[str] = []

    class Said:
        def yes_no(self, label: str, default: bool) -> bool:
            asked.append(label)
            return default

    ctx = _ctx(Said(), {})
    wizard.ask_evaluation(ctx)
    assert ctx.values["RAG_INSTALL_EVAL"] == "false"
    assert "Install answer-quality evaluation?" in asked[0] and "RAGAS + LangChain" in asked[0]

    ctx = _ctx(Said(), {"RAG_INSTALL_EVAL": "true"})
    wizard.ask_evaluation(ctx)
    assert ctx.values["RAG_INSTALL_EVAL"] == "true"  # an existing choice is the default


# L4 install
from callobuzz_rag.accounts.email_rules import EmailRuleError, check_email  # noqa: E402
from callobuzz_rag.core.install import is_dev_domain  # noqa: E402

SITE = "chat.sharmafurniture.in"
SITE_BASE = (
    "--quick",
    "--yes",
    "--set",
    f"RAG_SITE_DOMAIN={SITE}",
    "--set",
    "RAG_SUPERADMIN_EMAIL=owner@sharmafurniture.in",
)


def site_run(tmp_path: Path, *argv: str) -> tuple[int, dict[str, str], str]:
    """Run the wizard into tmp_path/env; returns (exit code, written values, printed text)."""
    env = tmp_path / "env"
    env.parent.mkdir(parents=True, exist_ok=True)
    code, lines = run(list(argv), env)
    values = wizard.parse_env_text(env.read_text(encoding="utf-8")) if env.is_file() else {}
    return code, values, "\n".join(lines)


def test_site_domain_derives_every_address(tmp_path: Path) -> None:
    rc, v, _ = site_run(tmp_path, *SITE_BASE)
    assert rc == 0
    for key in ("RAG_PUBLIC_URL", "RAG_DASHBOARD_URL", "RAG_DASHBOARD_ENGINE_URL"):
        assert v[key] == f"https://{SITE}", key
    assert v["RAG_DASHBOARD_SCRAPER_URL"] == f"https://{SITE}/scraper"
    assert v["RAG_ENV"] == "prod"
    assert v["RAG_TRUST_PROXY_HEADERS"] == "true" and v["RAG_TRUSTED_PROXY_HOPS"] == "1"
    assert v["RAG_ACME_EMAIL"] == "owner@sharmafurniture.in"
    profiles = v["COMPOSE_PROFILES"].split(",")
    assert "proxy" in profiles and "tei" in profiles
    for key in ("RAG_CORS_ORIGINS", "SCRAPER_CORS_ORIGINS"):
        origins = json.loads(v[key])
        assert f"https://{SITE}" in origins and "*" not in origins, key


def test_set_values_win_over_derived_ones(tmp_path: Path) -> None:
    rc, v, _ = site_run(
        tmp_path,
        *SITE_BASE,
        "--set",
        "RAG_PUBLIC_URL=https://api.sharmafurniture.in",
        "--set",
        "RAG_ACME_EMAIL=certs@sharmafurniture.in",
    )
    assert rc == 0
    assert v["RAG_PUBLIC_URL"] == "https://api.sharmafurniture.in"
    assert v["RAG_DASHBOARD_URL"] == f"https://{SITE}"
    assert v["RAG_ACME_EMAIL"] == "certs@sharmafurniture.in"


@pytest.mark.parametrize(
    "bad",
    [
        "https://chat.sharmafurniture.in",
        "chat.sharmafurniture.in:8443",
        "203.0.113.10",
        "localhost",
        "shop.local",
        "192.168.1.20",
        "chat..in",
    ],
)
def test_a_site_domain_must_be_a_public_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    rc, _, out = site_run(tmp_path, "--quick", "--yes", "--set", f"RAG_SITE_DOMAIN={bad}")
    said = (out + capsys.readouterr().err).lower()
    assert rc == 2 and "public web address" in said
    assert not (tmp_path / "env").exists()


@pytest.mark.parametrize(
    "host",
    [
        "chat.sharmafurniture.in",
        "testing.in",
        "local.store",
        "localhost",
        "app.localhost",
        "myshop.local",
        "myshop.test",
        "box.internal",
        "nas.lan",
        "x.home.arpa",
    ],
)
def test_the_wizard_domain_rule_agrees_with_the_engine(host: str) -> None:
    # a well-formed name is refused exactly when the engine calls it a development address
    assert (wizard.site_domain_error(host) is not None) == is_dev_domain(host)


def test_no_site_domain_changes_nothing(tmp_path: Path) -> None:
    rc, v, _ = site_run(tmp_path, "--quick", "--yes")
    assert rc == 0 and "proxy" not in v["COMPOSE_PROFILES"].split(",")
    assert v.get("RAG_PUBLIC_URL", "") == "" and v.get("RAG_ENV") == "dev"


def test_rerun_keeps_secrets_and_answers(tmp_path: Path) -> None:
    code = "1a2b3c4d-1a2b-3c4d-5e6f-1a2b3c4d5e6f"
    _, first, _ = site_run(tmp_path, *SITE_BASE, "--set", f"RAG_LICENSE_KEY={code}")
    _, second, out = site_run(tmp_path, "--quick", "--yes")
    for key in (
        "RAG_DB_PASSWORD",
        "RAG_ENCRYPTION_KEY",
        "RAG_SESSION_SECRET",
        "RAG_SITE_DOMAIN",
        "RAG_LICENSE_KEY",
        "RAG_PUBLIC_URL",
        "COMPOSE_PROFILES",
    ):
        assert second[key] == first[key], key
    assert code not in out  # secret keys are masked in the summary


def test_a_hand_edited_address_survives_a_rerun(tmp_path: Path) -> None:
    site_run(tmp_path, *SITE_BASE)
    env = tmp_path / "env"
    env.write_text(
        env.read_text(encoding="utf-8").replace(
            f"RAG_PUBLIC_URL=https://{SITE}", "RAG_PUBLIC_URL=https://api.sharmafurniture.in"
        ),
        encoding="utf-8",
    )
    _, v, _ = site_run(tmp_path, "--quick", "--yes")
    assert v["RAG_PUBLIC_URL"] == "https://api.sharmafurniture.in"


@pytest.mark.parametrize("addr", ["owner@sharmafurniture.in", "owner@shop.test", "owner@x.example"])
def test_admin_email_for_production_follows_the_engine_rule(tmp_path: Path, addr: str) -> None:
    engine_ok = True
    try:
        check_email(addr, allow_special_use=False)
    except EmailRuleError:
        engine_ok = False
    rc, _, _ = site_run(
        tmp_path / addr.replace("@", "_"),
        "--quick",
        "--yes",
        "--set",
        f"RAG_SITE_DOMAIN={SITE}",
        "--set",
        f"RAG_SUPERADMIN_EMAIL={addr}",
    )
    assert (rc == 0) == engine_ok, addr


def test_a_new_domain_on_a_rerun_moves_every_derived_address(tmp_path: Path) -> None:
    site_run(tmp_path, *SITE_BASE)
    new = "bot.sharmafurniture.in"
    rc, v, _ = site_run(tmp_path, "--quick", "--yes", "--set", f"RAG_SITE_DOMAIN={new}")
    assert rc == 0
    for key in ("RAG_PUBLIC_URL", "RAG_DASHBOARD_URL", "RAG_DASHBOARD_ENGINE_URL"):
        assert v[key] == f"https://{new}", key
    assert v["RAG_DASHBOARD_SCRAPER_URL"] == f"https://{new}/scraper"
    for key in ("RAG_CORS_ORIGINS", "SCRAPER_CORS_ORIGINS"):
        origins = json.loads(v[key])
        assert f"https://{new}" in origins and f"https://{SITE}" not in origins, key


# /L4 install


# No purchase code (open source) ---------------------------------------------------------------


def test_the_wizard_never_asks_for_a_purchase_code() -> None:
    source = (ROOT / "scripts" / "setup.py").read_text("utf-8").lower()
    for word in ("purchase code", "rag_license_", "evaluation copy", "codecanyon", "ask_licence"):
        assert word not in source, word


def test_a_full_run_writes_no_licence_setting(target: Path) -> None:
    code, lines = run(
        [
            "--full",
            "--yes",
            "--set",
            "RAG_PUBLIC_URL=https://chat.sharmafurniture.in",
            "--set",
            "RAG_SUPERADMIN_EMAIL=admin@yourcompany.in",
        ],
        target,
    )
    assert code == 0, lines
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    example = wizard.parse_env_text((ROOT / ".env.example").read_text(encoding="utf-8"))
    for key in (k for k in values if k.startswith("RAG_LICENSE_")):
        assert values[key] == example.get(key), key  # the wizard sets none of them
