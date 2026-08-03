-- migrate:up
CREATE TABLE analyses (
    id TEXT PRIMARY KEY,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    content TEXT NOT NULL,
    character_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    recommendations_json TEXT,
    final_selection_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    review_started_at TEXT,
    review_completed_at TEXT
);

CREATE TABLE review_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    entity_type TEXT NOT NULL,
    urn TEXT NOT NULL,
    action TEXT NOT NULL,
    replaced_urn TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_review_actions_analysis_id ON review_actions(analysis_id);

-- migrate:down
DROP TABLE review_actions;
DROP TABLE analyses;
