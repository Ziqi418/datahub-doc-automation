-- migrate:up
ALTER TABLE analyses ADD COLUMN freshness_evidence_json TEXT;

-- migrate:down
ALTER TABLE analyses DROP COLUMN freshness_evidence_json;
