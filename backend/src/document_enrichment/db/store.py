from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from document_enrichment.models import (
    AnalysisRecord,
    AnalysisStatus,
    DocumentConflictCandidate,
    DocumentFreshnessStatus,
    RecommendationSet,
    ReviewAction,
    ReviewSelection,
)


class AnalysisNotFoundError(KeyError):
    pass


class InvalidStateError(ValueError):
    pass


_TRANSITIONS: dict[AnalysisStatus, set[AnalysisStatus]] = {
    AnalysisStatus.UPLOADED: {AnalysisStatus.ANALYZING},
    AnalysisStatus.ANALYZING: {AnalysisStatus.READY_FOR_REVIEW, AnalysisStatus.ANALYSIS_FAILED},
    AnalysisStatus.ANALYSIS_FAILED: {AnalysisStatus.ANALYZING},
    AnalysisStatus.READY_FOR_REVIEW: {AnalysisStatus.APPROVED},
    AnalysisStatus.APPROVED: {AnalysisStatus.PUBLISHING, AnalysisStatus.READY_FOR_REVIEW},
    AnalysisStatus.PUBLISHING: {AnalysisStatus.PUBLISHED, AnalysisStatus.PUBLISH_FAILED},
    AnalysisStatus.PUBLISH_FAILED: {AnalysisStatus.PUBLISHING, AnalysisStatus.READY_FOR_REVIEW},
    AnalysisStatus.PUBLISHED: set(),
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class SQLiteAnalysisStore:
    """Small, explicit SQLite store for one-process MVP workflow state."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        """Verify that dbmate has applied the versioned schema before serving requests."""
        if not self.database_path.exists():
            raise RuntimeError("Database is not initialized; run `make migrate` before starting the API")
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1 FROM schema_migrations LIMIT 1")
                connection.execute("SELECT 1 FROM analyses LIMIT 1")
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                "Database schema is not initialized; run `make migrate` before starting the API"
            ) from exc

    def create(
        self, *, analysis_id: str, filename: str, content: str, sha256: str
    ) -> AnalysisRecord:
        now = utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses (id, source_filename, source_sha256, content, character_count, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    filename,
                    sha256,
                    content,
                    len(content),
                    AnalysisStatus.UPLOADED.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return self.get(analysis_id)

    def get(self, analysis_id: str) -> AnalysisRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        if row is None:
            raise AnalysisNotFoundError(analysis_id)
        return self._record(row)

    def content(self, analysis_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        if row is None:
            raise AnalysisNotFoundError(analysis_id)
        return str(row["content"])

    def transition(
        self, analysis_id: str, target: AnalysisStatus, *, error_code: str | None = None
    ) -> AnalysisRecord:
        current = self.get(analysis_id)
        if target not in _TRANSITIONS[current.status]:
            raise InvalidStateError(f"Cannot transition {current.status.value} to {target.value}")
        now = utcnow().isoformat()
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE analyses SET status = ?, error_code = ?, updated_at = ? WHERE id = ? AND status = ?",
                (target.value, error_code, now, analysis_id, current.status.value),
            )
        if result.rowcount != 1:
            raise InvalidStateError("Analysis changed concurrently; retry the request")
        return self.get(analysis_id)

    def save_recommendations(
        self, analysis_id: str, recommendations: RecommendationSet
    ) -> AnalysisRecord:
        current = self.get(analysis_id)
        if current.status != AnalysisStatus.ANALYZING:
            raise InvalidStateError("Recommendations can only be saved while analyzing")
        now = utcnow().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE analyses
                SET status = ?, recommendations_json = ?, error_code = NULL, updated_at = ?, review_started_at = COALESCE(review_started_at, ?)
                WHERE id = ? AND status = ?
                """,
                (
                    AnalysisStatus.READY_FOR_REVIEW.value,
                    recommendations.model_dump_json(),
                    now,
                    now,
                    analysis_id,
                    AnalysisStatus.ANALYZING.value,
                ),
            )
        return self.get(analysis_id)

    def save_review(
        self, analysis_id: str, selection: ReviewSelection, actions: list[ReviewAction]
    ) -> AnalysisRecord:
        current = self.get(analysis_id)
        if current.status != AnalysisStatus.READY_FOR_REVIEW:
            raise InvalidStateError(
                "An analysis must be ready for review before it can be approved"
            )
        now = utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                prior_selection = current.final_selection
                updated = connection.execute(
                    """
                    UPDATE analyses SET status = ?, final_selection_json = ?, updated_at = ?, review_completed_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        AnalysisStatus.APPROVED.value,
                        selection.model_dump_json(),
                        now.isoformat(),
                        now.isoformat(),
                        analysis_id,
                        AnalysisStatus.READY_FOR_REVIEW.value,
                    ),
                )
                if updated.rowcount != 1:
                    raise InvalidStateError("Analysis changed concurrently; retry the request")
                if prior_selection != selection:
                    # A confirmation is meaningful only for the exact selected
                    # catalog context that was reviewed.  Recommendations and
                    # the user's new selection remain intact for a quick retry.
                    connection.execute(
                        "DELETE FROM schema_validation_confirmations WHERE analysis_id = ?", (analysis_id,)
                    )
                    connection.execute("DELETE FROM document_conflicts WHERE analysis_id = ?", (analysis_id,))
                connection.executemany(
                    """
                    INSERT INTO review_actions (analysis_id, entity_type, urn, action, replaced_urn, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            analysis_id,
                            action.entity_type.value,
                            action.urn,
                            action.action,
                            action.replaced_urn,
                            now.isoformat(),
                        )
                        for action in actions
                    ],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get(analysis_id)

    def return_to_review(self, analysis_id: str) -> AnalysisRecord:
        """Re-open an unpublished draft without discarding recommendations or selection."""
        current = self.get(analysis_id)
        if current.status not in {AnalysisStatus.APPROVED, AnalysisStatus.PUBLISH_FAILED}:
            raise InvalidStateError("Only unpublished reviewed analyses can return to review")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE analyses SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (AnalysisStatus.READY_FOR_REVIEW.value, utcnow().isoformat(), analysis_id, current.status.value),
            )
        if result.rowcount != 1:
            raise InvalidStateError("Analysis changed concurrently; retry the request")
        return self.get(analysis_id)

    def review_actions(self, analysis_id: str) -> list[ReviewAction]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT entity_type, urn, action, replaced_urn, created_at FROM review_actions WHERE analysis_id = ? ORDER BY id",
                (analysis_id,),
            ).fetchall()
        return [
            ReviewAction(
                entity_type=row["entity_type"],
                urn=row["urn"],
                action=row["action"],
                replaced_urn=row["replaced_urn"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def save_publish_result(
        self,
        analysis_id: str,
        *,
        document_urn: str,
        dataset_baseline_json: str,
        published_at: datetime,
    ) -> AnalysisRecord:
        now = utcnow().isoformat()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE analyses
                SET status = ?, document_urn = ?, dataset_baseline_json = ?, published_at = ?,
                    freshness_status = ?, freshness_reason = NULL, last_freshness_checked_at = ?,
                    error_code = NULL, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (AnalysisStatus.PUBLISHED.value, document_urn, dataset_baseline_json,
                 published_at.isoformat(), DocumentFreshnessStatus.ACTIVE.value,
                 published_at.isoformat(), now, analysis_id, AnalysisStatus.PUBLISHING.value),
            )
        if result.rowcount != 1:
            raise InvalidStateError("Analysis changed concurrently; retry the request")
        return self.get(analysis_id)

    def replace_conflicts(self, analysis_id: str, candidates: list[DocumentConflictCandidate]) -> None:
        """Persist the deterministic detector output; confirmations survive rechecks."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = {
                    row["document_urn"]: row["confirmed_at"]
                    for row in connection.execute(
                        "SELECT document_urn, confirmed_at FROM document_conflicts WHERE analysis_id = ?", (analysis_id,)
                    )
                }
                connection.execute("DELETE FROM document_conflicts WHERE analysis_id = ?", (analysis_id,))
                connection.executemany(
                    """INSERT INTO document_conflicts
                    (analysis_id, document_urn, title, related_dataset_urns_json, score, evidence_json, detector_version, detected_at, high_risk, confirmed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(
                        analysis_id, item.document_urn, item.title, json.dumps(item.related_dataset_urns), item.score,
                        json.dumps(item.evidence), item.detector_version, item.detected_at.isoformat(), int(item.high_risk),
                        existing.get(item.document_urn),
                    ) for item in candidates],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def conflicts(self, analysis_id: str) -> list[DocumentConflictCandidate]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_conflicts WHERE analysis_id = ? ORDER BY score DESC, document_urn", (analysis_id,)
            ).fetchall()
        return [DocumentConflictCandidate(
            document_urn=row["document_urn"], title=row["title"],
            related_dataset_urns=json.loads(row["related_dataset_urns_json"]), score=row["score"],
            evidence=json.loads(row["evidence_json"]), detector_version=row["detector_version"],
            detected_at=datetime.fromisoformat(row["detected_at"]), high_risk=bool(row["high_risk"]),
            confirmed=bool(row["confirmed_at"]),
        ) for row in rows]

    def confirm_conflict(self, analysis_id: str, document_urn: str) -> list[DocumentConflictCandidate]:
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE document_conflicts SET confirmed_at = ? WHERE analysis_id = ? AND document_urn = ?",
                (utcnow().isoformat(), analysis_id, document_urn),
            )
        if updated.rowcount != 1:
            raise KeyError(document_urn)
        return self.conflicts(analysis_id)

    def confirmed_schema_references(self, analysis_id: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT reference_id FROM schema_validation_confirmations WHERE analysis_id = ?", (analysis_id,)
            ).fetchall()
        return {str(row["reference_id"]) for row in rows}

    def confirm_schema_reference(self, analysis_id: str, reference_id: str) -> None:
        # The reference id is deterministic over source location/text.  This is an
        # audit acknowledgement only; it never changes the source Document.
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO schema_validation_confirmations (analysis_id, reference_id, confirmed_at) VALUES (?, ?, ?)",
                (analysis_id, reference_id, utcnow().isoformat()),
            )

    def save_publish_failure(self, analysis_id: str, error_code: str) -> AnalysisRecord:
        return self.transition(analysis_id, AnalysisStatus.PUBLISH_FAILED, error_code=error_code)

    def save_freshness_check(
        self, analysis_id: str, *, reason: str | None, checked_at: datetime
    ) -> AnalysisRecord:
        record = self.get(analysis_id)
        if record.status != AnalysisStatus.PUBLISHED:
            raise InvalidStateError("Freshness can only be checked for published analyses")
        if reason is None:
            # No mutation when the baseline is still current. If a prior check already
            # requested human review, that flag remains until a new review is completed.
            return record
        freshness = DocumentFreshnessStatus.NEEDS_REVIEW if reason else DocumentFreshnessStatus.ACTIVE
        with self._connect() as connection:
            if reason and record.freshness_reason == reason:
                # A repeated, identical check is intentionally not a new audit event.
                return record
            connection.execute(
                """UPDATE analyses SET freshness_status = ?, freshness_reason = ?,
                   last_freshness_checked_at = ?, updated_at = ? WHERE id = ?""",
                (freshness.value, reason, checked_at.isoformat(), utcnow().isoformat(), analysis_id),
            )
        return self.get(analysis_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> AnalysisRecord:
        return AnalysisRecord(
            id=row["id"],
            source_filename=row["source_filename"],
            source_sha256=row["source_sha256"],
            character_count=row["character_count"],
            status=AnalysisStatus(row["status"]),
            recommendations=RecommendationSet.model_validate_json(row["recommendations_json"])
            if row["recommendations_json"]
            else None,
            final_selection=ReviewSelection.model_validate_json(row["final_selection_json"])
            if row["final_selection_json"]
            else None,
            error_code=row["error_code"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            review_started_at=datetime.fromisoformat(row["review_started_at"])
            if row["review_started_at"]
            else None,
            review_completed_at=datetime.fromisoformat(row["review_completed_at"])
            if row["review_completed_at"]
            else None,
            document_urn=row["document_urn"],
            published_at=datetime.fromisoformat(row["published_at"]) if row["published_at"] else None,
            freshness_status=DocumentFreshnessStatus(row["freshness_status"])
            if row["freshness_status"] else None,
            freshness_reason=row["freshness_reason"],
            last_freshness_checked_at=datetime.fromisoformat(row["last_freshness_checked_at"])
            if row["last_freshness_checked_at"] else None,
        )
