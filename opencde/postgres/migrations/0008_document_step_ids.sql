-- This file was generated with the assistance of an AI coding tool.
-- The highest STEP id a document's versions have had (UL-48, LD-61, SA-103): raised with every version of an IFC
-- file and never lowered, so that no writer gives an id again that a version deleted. NULL while it is unknown.
ALTER TABLE documents ADD COLUMN max_step_id bigint;
