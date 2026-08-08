-- migrate:up
CREATE TABLE document_conflicts (
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    document_urn TEXT NOT NULL,
    title TEXT NOT NULL,
    related_dataset_urns_json TEXT NOT NULL,
    score REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    high_risk INTEGER NOT NULL,
    confirmed_at TEXT,
    PRIMARY KEY (analysis_id, document_urn)
);
CREATE INDEX idx_document_conflicts_analysis ON document_conflicts(analysis_id, score DESC);

-- migrate:down
DROP INDEX idx_document_conflicts_analysis;
DROP TABLE document_conflicts;
