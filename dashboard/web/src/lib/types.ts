export type Persona = { number: number; name: string };

export type FormatOption = { id: string; label?: string };

export type LanguageMode = { id: string; label?: string; languages?: string[] };

export type FormatPattern = { id: string; label?: string };

export type HypothesisOption = { id: string; label: string };

export type HypothesisVariable = {
  label?: string;
  description?: string;
  options?: HypothesisOption[];
};

export type ConceptOption = {
  id: string;
  label: string;
  description?: string;
};

export type StudioPayload = {
  source?: string;
  config?: Record<string, unknown>;
  personas?: Persona[];
  formats?: Array<string | FormatOption>;
  format_patterns?: Record<string, FormatPattern[]>;
  language_modes?: Array<string | LanguageMode>;
  concepts?: ConceptOption[];
  hypothesis?: {
    variables?: Record<string, HypothesisVariable>;
    default?: { type?: string; variant?: string };
  };
  can_run?: boolean;
  batch_size?: number;
};

export type Run = {
  run_id?: string;
  status?: string;
  prompt_count?: number;
  image_count?: number;
  display_batch?: string;
  created_at?: number;
  flow?: string;
  flow_type?: string;
  owner_type?: string;
  owner_id?: string;
  device_id?: string;
  agent_id?: string;
  copy_generation?: {
    status?: string;
    delivery_status?: string;
    last_error?: string;
    error_code?: string;
    error_detail?: string;
  };
  image_generation?: {
    status?: string;
    last_error?: string;
    error_code?: string;
    job_id?: string;
  };
};

export type ProviderSafe = {
  provider?: string;
  config?: {
    has_secret?: boolean;
    key_fingerprint?: string;
    api_url?: string;
    default_model?: string;
  };
};

export type OpencodeCatalog = {
  api_url?: string;
  default_model?: string;
  models_by_provider?: Record<string, string[]>;
};

export type Org = {
  org_id: string;
  name?: string;
  domain?: string;
  config_mode?: string;
  permissions?: {
    can_edit_org_config?: boolean;
    can_manage_org?: boolean;
    can_invite_members?: boolean;
  };
};

export type Membership = { org_id: string; role?: string };

export type OrgMember = {
  user_id: string;
  email?: string;
  display_name?: string;
  role?: string;
  joined_at?: number;
  permissions?: Record<string, boolean>;
};

export type OrgInvite = {
  invite_id: string;
  email?: string;
  role?: string;
  status?: string;
  expires_at?: number;
  invite_url?: string;
};

export type ConfigSource = {
  type?: string;
  label?: string;
  org_id?: string;
  org_name?: string;
  config_mode?: string;
  has_custom?: boolean;
};

export type EffectiveConfig = {
  config?: Record<string, unknown>;
  can_edit?: boolean;
  can_view_versions?: boolean;
  can_rollback?: boolean;
  can_copy?: boolean;
  mode?: string;
  source?: string;
  owner_type?: string;
  config_id?: string;
  version?: number;
  org?: { org_id?: string; name?: string };
  available_orgs?: { org_id: string; name?: string }[];
};

export type ConfigVersion = {
  version_id: string;
  created_at?: number;
  changed_by_display_name?: string;
  changed_by_email?: string;
  changed_keys?: string[];
  change_reason?: string;
};

export type Trace = {
  trace_id?: string;
  run_id?: string;
  batch?: string;
  label?: string;
  model?: string;
  provider?: string;
  status?: string;
  http_status?: number;
  duration_ms?: number;
  created_at?: number;
  error_detail?: string;
  org_id?: string;
  scope?: "personal" | "org";
  actor_email?: string;
  display_name?: string;
  request?: { prompt?: unknown };
  response?: { content?: unknown };
};

export type TraceList = {
  personal?: Trace[];
  org?: Trace[];
  org_name?: string;
  traces?: Trace[];
};
