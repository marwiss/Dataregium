-- This file was generated with the assistance of an AI coding tool.
-- A project's repository mirrored to a forge (ADR 0032, F206 c, F207 c, F208 c, F209 c): the address, the token
-- sealed with a key from the server's secret, until it expires and the row goes; what was pushed last by the forge's
-- refs and the server's refs it was made from; how the last attempt went, and when the next may be made.
CREATE TABLE project_remotes (
    project_id text PRIMARY KEY REFERENCES projects (project_id) ON DELETE CASCADE,
    url text NOT NULL,
    token text NOT NULL,
    max_file_bytes bigint NOT NULL,
    connected_by text REFERENCES users (id) ON DELETE SET NULL,
    connected_at iso_utc NOT NULL,
    expires_at iso_utc NOT NULL,
    pushed jsonb NOT NULL DEFAULT '{}',
    sources jsonb NOT NULL DEFAULT '{}',
    pushed_at iso_utc,
    attempt_at iso_utc NOT NULL,
    status text NOT NULL,
    error text,
    left_out jsonb NOT NULL DEFAULT '[]'
);
CREATE INDEX project_remotes_attempt_at ON project_remotes (attempt_at);
CREATE INDEX project_remotes_expires_at ON project_remotes (expires_at);
