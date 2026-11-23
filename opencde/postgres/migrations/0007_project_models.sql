-- This file was generated with the assistance of an AI coding tool.
-- The accounts' view of a project's models (0005) also names its merges' models: a preparation's copy stays in the
-- IFC graph for a day after it is done, and goes with the project instead of being left there with no row to find it.
CREATE OR REPLACE VIEW project_models (project_id, model_id) AS
    SELECT c.project_id, c.id FROM conversions c
    UNION ALL SELECT m.project_id, m.model_id FROM merges m WHERE m.model_id IS NOT NULL;
