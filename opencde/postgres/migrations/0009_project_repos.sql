-- This file was generated with the assistance of an AI coding tool.
-- A git repository per project (ADR 0030, UL-48, fas 5.2): the commit main names as the server holds it, and
-- each version's commit on it. NULL for a version whose file is not text, or from before the repositories.
CREATE TABLE project_repos (
    project_id text PRIMARY KEY REFERENCES projects (project_id) ON DELETE CASCADE,
    head text NOT NULL,
    created_at iso_utc NOT NULL
);
ALTER TABLE document_versions ADD COLUMN commit text;
