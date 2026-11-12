"""POST /api/v1/golden/import answers a bad file with a 422, not a 500."""

from tests.unit.scope_harness import call, scoped_app


async def test_an_invalid_chunk_id_is_a_422_naming_row_and_value() -> None:
    body = "query,expected_answer,expected_chunk_ids\nq1,a1,chunk-7\n"
    with scoped_app() as (app, world):
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/golden/import?corpus_id={world.corpus_a.id}",
            files={"file": ("golden.csv", body.encode(), "text/csv")},
        )
        world.session.commit.assert_not_awaited()
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert "row 2" in detail and "chunk-7" in detail


async def test_a_bom_csv_imports_its_rows() -> None:
    body = "query,expected_answer\nWhen do you open?,9am\n".encode("utf-8-sig")
    with scoped_app() as (app, world):
        res = await call(
            app,
            "ck_a",
            "POST",
            f"/api/v1/golden/import?corpus_id={world.corpus_a.id}",
            files={"file": ("golden.csv", body, "text/csv")},
        )
    assert res.status_code == 201, res.text
    assert res.json()["imported_count"] == 1
