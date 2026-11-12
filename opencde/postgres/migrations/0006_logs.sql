-- This file was generated with the assistance of an AI coding tool.
-- The logs area (ADR 0029): the audit log, the request log, and the sign-in throttle's failures.

-- They name users and clients by value only, without foreign keys, as the graph does: a log outlives what it names.
-- Text that is filtered or ordered on is COLLATE "C", by code point, as the graphs compare it.

-- Who did what, when and from where. The details are the JSON string the server wrote, with the secrets masked;
-- nothing secret is ever written here. Events are kept: none expires.
CREATE TABLE audit_events (
    id text PRIMARY KEY,
    type text COLLATE "C" NOT NULL,
    at iso_utc COLLATE "C" NOT NULL,
    actor_user_id text,
    client_id text,
    ip text,
    details text NOT NULL,
    request_id text
);
CREATE INDEX audit_events_at ON audit_events (at);

-- One entry per request while OPENCDE_REQUEST_LOG_ENABLED is on: the path without its query string, never a body,
-- a header or a token. The cleaner deletes an entry once it expires.
CREATE TABLE request_log_entries (
    id text PRIMARY KEY,
    at iso_utc COLLATE "C" NOT NULL,
    method text NOT NULL,
    path text COLLATE "C" NOT NULL,
    operation text,
    status integer NOT NULL,
    duration_ms double precision NOT NULL,
    request_id text COLLATE "C",
    bytes_in bigint NOT NULL,
    bytes_out bigint NOT NULL,
    username text COLLATE "C",
    client_id text COLLATE "C",
    ip text,
    expires_at iso_utc NOT NULL
);
CREATE INDEX request_log_entries_at ON request_log_entries (at);
CREATE INDEX request_log_entries_expires_at ON request_log_entries (expires_at);

-- Failed sign-ins in a row per user name, keyed by the name's hash, never the name; forgotten when it expires.
CREATE TABLE sign_in_throttles (
    key text PRIMARY KEY,
    failures integer NOT NULL,
    blocked_until iso_utc,
    expires_at iso_utc NOT NULL
);
CREATE INDEX sign_in_throttles_expires_at ON sign_in_throttles (expires_at);
