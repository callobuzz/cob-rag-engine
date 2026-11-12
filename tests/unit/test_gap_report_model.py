"""Unit tests for GapReport model definition."""

from callobuzz_rag.db.models.gap_report import GapReport


class TestGapReportModel:
    def test_tablename(self) -> None:
        assert GapReport.__tablename__ == "gap_report"

    def test_has_required_columns(self) -> None:
        cols = {c.name for c in GapReport.__table__.columns}
        expected = {
            "id",
            "status",
            "started_at",
            "completed_at",
            "query_count",
            "cluster_count",
            "clusters",
            "config_snapshot",
            "error_message",
        }
        assert expected.issubset(cols)

    def test_clusters_is_jsonb(self) -> None:
        report = GapReport()
        report.clusters = [{"topic_id": 0, "keywords": ["test"], "query_count": 5}]
        assert report.clusters[0]["topic_id"] == 0
