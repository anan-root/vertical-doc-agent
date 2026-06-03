CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS projects (
  project_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  project_type TEXT CHECK (project_type IN ('construction', 'epc') OR project_type IS NULL),
  stage TEXT NOT NULL DEFAULT 'draft',
  stage_label TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS uploaded_files (
  file_id TEXT PRIMARY KEY,
  project_id TEXT,
  business_type TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_ext TEXT,
  mime_type TEXT,
  file_size INTEGER,
  page_count INTEGER,
  storage_uri TEXT NOT NULL,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'uploaded',
  related_source_bid_id TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  project_id TEXT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  progress_total INTEGER,
  progress_completed INTEGER,
  progress_failed INTEGER,
  progress_percent DOUBLE PRECISION,
  message TEXT,
  result_ref TEXT,
  error_code TEXT,
  error_message TEXT,
  started_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  config_snapshot_json JSONB,
  metadata_json JSONB,
  FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS tender_parse_results (
  parse_result_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  source_file_ids_json JSONB,
  project_info_json JSONB,
  score_points_json JSONB,
  technical_requirements_json JSONB,
  report_storage_uri TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  confirmed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS technical_bid_outlines (
  outline_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  parse_result_id TEXT,
  version_no INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  outline_json JSONB NOT NULL,
  report_storage_uri TEXT,
  confirmed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (project_id) REFERENCES projects(project_id),
  FOREIGN KEY (parse_result_id) REFERENCES tender_parse_results(parse_result_id)
);

CREATE TABLE IF NOT EXISTS chapter_generation_runs (
  generation_run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  outline_id TEXT NOT NULL,
  status TEXT NOT NULL,
  task_count INTEGER,
  completed_count INTEGER,
  failed_count INTEGER,
  duration_seconds DOUBLE PRECISION,
  result_storage_uri TEXT,
  report_storage_uri TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  config_snapshot_json JSONB,
  metadata_json JSONB,
  FOREIGN KEY (project_id) REFERENCES projects(project_id),
  FOREIGN KEY (outline_id) REFERENCES technical_bid_outlines(outline_id)
);

CREATE TABLE IF NOT EXISTS chapter_generation_tasks (
  task_id TEXT PRIMARY KEY,
  generation_run_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  outline_node_id TEXT,
  chapter_path_json JSONB NOT NULL,
  status TEXT NOT NULL,
  input_chars INTEGER,
  estimated_tokens INTEGER,
  image_candidate_count INTEGER,
  table_reference_count INTEGER,
  model TEXT,
  duration_seconds DOUBLE PRECISION,
  result_json JSONB,
  error_code TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (generation_run_id) REFERENCES chapter_generation_runs(generation_run_id),
  FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS document_versions (
  doc_version_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  document_type TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  status TEXT NOT NULL,
  output_mode TEXT NOT NULL,
  file_name TEXT NOT NULL,
  storage_uri TEXT NOT NULL,
  source_generation_run_id TEXT,
  export_result_json JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  created_by TEXT,
  metadata_json JSONB,
  FOREIGN KEY (project_id) REFERENCES projects(project_id),
  FOREIGN KEY (source_generation_run_id) REFERENCES chapter_generation_runs(generation_run_id)
);

CREATE TABLE IF NOT EXISTS review_sessions (
  review_session_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  doc_version_id TEXT NOT NULL,
  status TEXT NOT NULL,
  total_items INTEGER DEFAULT 0,
  pending_items INTEGER DEFAULT 0,
  passed_items INTEGER DEFAULT 0,
  needs_revision_items INTEGER DEFAULT 0,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (project_id) REFERENCES projects(project_id),
  FOREIGN KEY (doc_version_id) REFERENCES document_versions(doc_version_id)
);

CREATE TABLE IF NOT EXISTS review_items (
  item_id TEXT PRIMARY KEY,
  review_session_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  type TEXT NOT NULL,
  severity TEXT NOT NULL,
  confidence TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  chapter_path_json JSONB,
  title TEXT NOT NULL,
  description TEXT,
  basis_json JSONB,
  evidence_json JSONB,
  suggestion TEXT,
  actions_json JSONB,
  anchor_json JSONB,
  reviewer_comment TEXT,
  created_by TEXT NOT NULL DEFAULT 'system',
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  FOREIGN KEY (review_session_id) REFERENCES review_sessions(review_session_id),
  FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE IF NOT EXISTS model_provider_configs (
  provider_config_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  api_type TEXT NOT NULL,
  base_url TEXT NOT NULL,
  model TEXT NOT NULL,
  api_key_encrypted TEXT,
  api_key_masked TEXT,
  enable_thinking BOOLEAN NOT NULL DEFAULT FALSE,
  default_temperature DOUBLE PRECISION,
  default_top_p DOUBLE PRECISION,
  default_max_tokens INTEGER,
  default_timeout_seconds INTEGER,
  default_max_retries INTEGER,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  last_test_status TEXT,
  last_test_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS llm_task_profiles (
  profile_id TEXT PRIMARY KEY,
  task_key TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  provider_config_id TEXT,
  model_override TEXT,
  api_type_override TEXT,
  temperature DOUBLE PRECISION,
  top_p DOUBLE PRECISION,
  max_tokens INTEGER,
  timeout_seconds INTEGER,
  max_retries INTEGER,
  max_workers INTEGER,
  enable_thinking BOOLEAN,
  structured_output BOOLEAN,
  json_mode BOOLEAN,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (provider_config_id) REFERENCES model_provider_configs(provider_config_id)
);

CREATE TABLE IF NOT EXISTS config_versions (
  version_id TEXT PRIMARY KEY,
  config_type TEXT NOT NULL,
  config_id TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  change_summary TEXT,
  before_json JSONB,
  after_json JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  created_by TEXT,
  metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS excellent_bid_sources (
  source_bid_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  original_file_name TEXT,
  original_storage_uri TEXT,
  converted_storage_uri TEXT,
  source_type TEXT,
  status TEXT NOT NULL,
  file_sha256 TEXT,
  page_count INTEGER,
  section_count INTEGER,
  table_count INTEGER,
  image_count INTEGER,
  image_group_count INTEGER,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS excellent_bid_sections (
  section_id TEXT PRIMARY KEY,
  source_bid_id TEXT NOT NULL,
  parent_section_id TEXT,
  level INTEGER,
  title TEXT NOT NULL,
  chapter_path_json JSONB,
  start_page INTEGER,
  end_page INTEGER,
  text_storage_uri TEXT,
  status TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (source_bid_id) REFERENCES excellent_bid_sources(source_bid_id)
);

CREATE TABLE IF NOT EXISTS excellent_bid_tables (
  table_id TEXT PRIMARY KEY,
  source_bid_id TEXT NOT NULL,
  section_id TEXT,
  title TEXT,
  row_count INTEGER,
  column_count INTEGER,
  storage_uri TEXT,
  semantic_text TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (source_bid_id) REFERENCES excellent_bid_sources(source_bid_id),
  FOREIGN KEY (section_id) REFERENCES excellent_bid_sections(section_id)
);

CREATE TABLE IF NOT EXISTS excellent_bid_images (
  image_asset_id TEXT PRIMARY KEY,
  source_bid_id TEXT NOT NULL,
  section_id TEXT,
  image_group_id TEXT,
  storage_uri TEXT NOT NULL,
  source_part_name TEXT,
  caption TEXT,
  semantic_text TEXT,
  semantic_confidence DOUBLE PRECISION,
  canonical_image_id TEXT,
  sha256 TEXT,
  perceptual_hash TEXT,
  reuse_level TEXT,
  chapter_adaptation_json JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (source_bid_id) REFERENCES excellent_bid_sources(source_bid_id),
  FOREIGN KEY (section_id) REFERENCES excellent_bid_sections(section_id)
);

CREATE TABLE IF NOT EXISTS excellent_bid_image_groups (
  image_group_id TEXT PRIMARY KEY,
  source_bid_id TEXT NOT NULL,
  section_id TEXT,
  title TEXT,
  semantic_text TEXT,
  member_count INTEGER,
  must_keep_with_group BOOLEAN NOT NULL DEFAULT FALSE,
  canonical_image_ids_json JSONB,
  group_canonical_image_key TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (source_bid_id) REFERENCES excellent_bid_sources(source_bid_id),
  FOREIGN KEY (section_id) REFERENCES excellent_bid_sections(section_id)
);

CREATE TABLE IF NOT EXISTS excellent_bid_chunks (
  chunk_id TEXT PRIMARY KEY,
  source_bid_id TEXT NOT NULL,
  section_id TEXT,
  chunk_type TEXT NOT NULL,
  title TEXT,
  text TEXT,
  storage_uri TEXT,
  token_estimate INTEGER,
  tags_json JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (source_bid_id) REFERENCES excellent_bid_sources(source_bid_id),
  FOREIGN KEY (section_id) REFERENCES excellent_bid_sections(section_id)
);

CREATE TABLE IF NOT EXISTS excellent_bid_fingerprints (
  fingerprint_id TEXT PRIMARY KEY,
  fingerprint_type TEXT NOT NULL,
  fingerprint_value TEXT NOT NULL,
  canonical_image_id TEXT,
  image_asset_ids_json JSONB,
  source_bid_ids_json JSONB,
  duplicate_count INTEGER,
  cross_source_duplicate BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB
);

CREATE TABLE IF NOT EXISTS project_material_usage (
  usage_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  doc_version_id TEXT,
  generation_run_id TEXT,
  source_bid_id TEXT,
  section_id TEXT,
  table_id TEXT,
  image_asset_id TEXT,
  image_group_id TEXT,
  usage_type TEXT NOT NULL,
  chapter_path_json JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  metadata_json JSONB,
  FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE INDEX IF NOT EXISTS idx_uploaded_files_project ON uploaded_files(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_document_versions_project ON document_versions(project_id);
CREATE INDEX IF NOT EXISTS idx_review_items_session ON review_items(review_session_id);
CREATE INDEX IF NOT EXISTS idx_review_items_status ON review_items(status);
CREATE INDEX IF NOT EXISTS idx_chapter_tasks_run ON chapter_generation_tasks(generation_run_id);
CREATE INDEX IF NOT EXISTS idx_excellent_images_source ON excellent_bid_images(source_bid_id);
CREATE INDEX IF NOT EXISTS idx_excellent_images_sha ON excellent_bid_images(sha256);
CREATE INDEX IF NOT EXISTS idx_excellent_images_canonical ON excellent_bid_images(canonical_image_id);
CREATE INDEX IF NOT EXISTS idx_excellent_groups_source ON excellent_bid_image_groups(source_bid_id);
CREATE INDEX IF NOT EXISTS idx_material_usage_project ON project_material_usage(project_id);
