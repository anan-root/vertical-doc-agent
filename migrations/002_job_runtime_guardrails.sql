CREATE INDEX IF NOT EXISTS idx_jobs_project_type_status_updated
ON jobs(project_id, job_type, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_jobs_active_workflow
ON jobs(project_id, job_type, created_at DESC)
WHERE status IN ('pending', 'running');
