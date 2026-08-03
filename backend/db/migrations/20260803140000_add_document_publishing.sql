-- migrate:up
ALTER TABLE analyses ADD COLUMN document_urn TEXT;
ALTER TABLE analyses ADD COLUMN dataset_baseline_json TEXT;
ALTER TABLE analyses ADD COLUMN published_at TEXT;
ALTER TABLE analyses ADD COLUMN freshness_status TEXT;
ALTER TABLE analyses ADD COLUMN freshness_reason TEXT;
ALTER TABLE analyses ADD COLUMN last_freshness_checked_at TEXT;
CREATE UNIQUE INDEX idx_analyses_document_urn ON analyses(document_urn) WHERE document_urn IS NOT NULL;

-- migrate:down
DROP INDEX idx_analyses_document_urn;
ALTER TABLE analyses DROP COLUMN last_freshness_checked_at;
ALTER TABLE analyses DROP COLUMN freshness_reason;
ALTER TABLE analyses DROP COLUMN freshness_status;
ALTER TABLE analyses DROP COLUMN published_at;
ALTER TABLE analyses DROP COLUMN dataset_baseline_json;
ALTER TABLE analyses DROP COLUMN document_urn;
