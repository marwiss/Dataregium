-- This file was generated with the assistance of an AI coding tool.
-- A whole IFC file as a proposal (F204 a, F124 a, ADR 0031): a project whose topics take change sets as their
-- snippet takes whole files too, as a new project does.
UPDATE projects
SET extensions = jsonb_set(extensions, '{snippet_type}', (extensions -> 'snippet_type') || '["DataregiumIfcFile"]'::jsonb)
WHERE jsonb_typeof(extensions -> 'snippet_type') = 'array'
  AND extensions -> 'snippet_type' ? 'DataregiumIfcChangeSet'
  AND NOT extensions -> 'snippet_type' ? 'DataregiumIfcFile';

-- A working copy of a version's IFC file that an AI edits through IfcMCP (ADR 0031, F60): one user's, in one
-- project, until it expires; its file is copies/<copy_id>.ifc. The topic its proposal went on, once it went.
CREATE TABLE ifc_copies (
    copy_id text PRIMARY KEY,
    user_id text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    project_id text NOT NULL REFERENCES projects (project_id) ON DELETE CASCADE,
    document_id text NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    version_index integer NOT NULL,
    storage_key text NOT NULL,
    topic_guid text,
    created_at iso_utc NOT NULL,
    expires_at iso_utc NOT NULL
);
CREATE INDEX ifc_copies_user ON ifc_copies (user_id);
CREATE INDEX ifc_copies_expires_at ON ifc_copies (expires_at);
