export type Persona = { number: number; name: string };

export type FormatPattern = { id: string; label?: string };

export type HypothesisOption = { id: string; label: string };

export type HypothesisVariable = {
  label?: string;
  description?: string;
  options?: HypothesisOption[];
};

export type StudioPayload = {
  source?: string;
  config?: Record<string, unknown>;
  personas?: Persona[];
  formats?: string[];
  format_patterns?: Record<string, FormatPattern[]>;
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
  device_id?: string;
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
  label?: string;
  model?: string;
  provider?: string;
  status?: string;
  http_status?: number;
  duration_ms?: number;
  created_at?: number;
  error_detail?: string;
  request?: { prompt?: unknown };
  response?: { content?: unknown };
};
