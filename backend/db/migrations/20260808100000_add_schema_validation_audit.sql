-- migrate:up
CREATE TABLE schema_validation_confirmations (
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    reference_id TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (analysis_id, reference_id)
);

-- migrate:down
DROP TABLE schema_validation_confirmations;
