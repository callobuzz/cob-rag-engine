"""RAGAS and LangChain are the optional evaluation add-on: nothing may import them at module
level, or the default install (which has neither) would fail at start-up."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPTIONAL = ("ragas", "langchain")


def _optional(name: str) -> bool:
    return name.split(".")[0].startswith(OPTIONAL)


def _module_level_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Imports that run when the module loads: the body, `if`/`try`/`with` blocks and class
    bodies, but not function bodies and not `if TYPE_CHECKING:` blocks."""
    found: list[tuple[int, str]] = []

    def visit(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                found.extend((node.lineno, a.name) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.append((node.lineno, node.module or ""))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            elif isinstance(node, ast.If):
                test = ast.unparse(node.test)
                if test not in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
                    visit(node.body)
                visit(node.orelse)
            elif isinstance(node, ast.ClassDef | ast.With | ast.AsyncWith):
                visit(node.body)
            elif isinstance(node, ast.Try):
                for block in (
                    node.body,
                    node.orelse,
                    node.finalbody,
                    *(h.body for h in node.handlers),
                ):
                    visit(block)

    visit(tree.body)
    return found


def _sources() -> list[Path]:
    files: list[Path] = []
    for folder in ("src", "scripts", "benchmarks"):
        files += [p for p in (ROOT / folder).rglob("*.py") if ".venv" not in p.parts]
    return files


def test_the_scan_sees_the_source_tree() -> None:
    assert len(_sources()) > 100


def test_the_scan_catches_a_module_level_import() -> None:
    bad = ast.parse("import os\ntry:\n    import ragas\nexcept ImportError:\n    pass\n")
    assert _module_level_imports(bad) == [(1, "os"), (3, "ragas")]
    ok = ast.parse(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import langchain_core\n"
        "def f():\n    from ragas import evaluate\n"
    )
    assert [n for _, n in _module_level_imports(ok) if _optional(n)] == []


def test_no_module_level_ragas_or_langchain_import() -> None:
    offenders: list[str] = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, name in _module_level_imports(tree):
            if _optional(name):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line} imports {name}")
    assert not offenders, "optional evaluation add-on imported at module level: " + "; ".join(
        offenders
    )
