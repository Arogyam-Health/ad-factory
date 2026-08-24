# Graph Report - info  (2026-08-24)

## Corpus Check
- 204 files · ~897,360 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2757 nodes · 8231 edges · 27 communities detected
- Extraction: 55% EXTRACTED · 45% INFERRED · 0% AMBIGUOUS · INFERRED: 3685 edges (avg confidence: 0.78)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 69|Community 69]]

## God Nodes (most connected - your core abstractions)
1. `get_sync_db()` - 182 edges
2. `AgentState` - 124 edges
3. `AgentPaths` - 79 edges
4. `LocalDataPlane` - 61 edges
5. `LocalDataPlaneClient` - 53 edges
6. `run()` - 44 edges
7. `readJson()` - 41 edges
8. `APIError` - 37 edges
9. `ReferenceWorkflowExecutor` - 34 edges
10. `run()` - 32 edges

## Surprising Connections (you probably didn't know these)
- `AgentPaths` --uses--> `Dry-run-first metadata-only cleanup for legacy Mongo agent jobs.`  [INFERRED]
  local_agent_runtime/storage.py → dashboard/backend/agent/migration.py
- `mask_key()` --calls--> `test_encryption()`  [INFERRED]
  dashboard/backend/security/crypto.py → tests/test_smoke.py
- `image_upload_suffix()` --calls--> `_write_revision_upload_manifest()`  [INFERRED]
  local_agent_runtime/structured_browser.py → scripts/local_agent.py
- `StructuredBrowserExecutor` --uses--> `JobProgressReporter`  [INFERRED]
  local_agent_runtime/structured_browser.py → scripts/local_agent.py
- `StructuredBrowserExecutor` --uses--> `Resolve the human-readable stem shared by a prompt and its generated images.`  [INFERRED]
  local_agent_runtime/structured_browser.py → scripts/local_agent.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.02
Nodes (113): Accept an approval delivered by the authenticated agent channel., rollback(), APIError, _chmod_private(), _decode_metadata(), _digest(), _expected_version(), load_or_create_internal_token() (+105 more)

### Community 1 - "Community 1"
Cohesion: 0.02
Nodes (269): bootstrap_super_admin(), admin_delete_config(), admin_delete_user(), admin_disable_org(), admin_export_audit_logs(), admin_export_configs(), admin_export_orgs(), admin_export_users() (+261 more)

### Community 2 - "Community 2"
Cohesion: 0.01
Nodes (304): get_super_admin_emails(), require_active_user(), require_super_admin(), require_super_admin_dependency(), dashboard_subprocess_env(), debugger_endpoint_reachable(), detect_wsl_windows_host_ip(), extension_browser_required_for_chatgpt() (+296 more)

### Community 3 - "Community 3"
Cohesion: 0.02
Nodes (202): admin_copy_config(), api_readyz(), get_generic_config_key_public(), get_generic_config_public(), retired_extension_websocket(), can_copy_config(), can_edit_config(), can_rollback_config() (+194 more)

### Community 4 - "Community 4"
Cohesion: 0.02
Nodes (98): adminFetch(), load(), onHash(), patchOrg(), sectionFromHash(), cacheKey(), clearCache(), fetchJSON() (+90 more)

### Community 5 - "Community 5"
Cohesion: 0.03
Nodes (129): _attachment_spinner_count(), build_browser_context(), build_image_metadata(), build_local_image_paths(), build_test_variables(), _capture_download_from_click(), chatgpt_app_ready(), clear_composer_keyboard() (+121 more)

### Community 6 - "Community 6"
Cohesion: 0.03
Nodes (96): admin_health(), browser_candidates(), _first_env(), Find Chrome/Brave on the current machine without hardcoding a user home path., resolve_browser_executable(), forward(), handle_client(), parse_args() (+88 more)

### Community 7 - "Community 7"
Cohesion: 0.03
Nodes (46): ArtifactServer, ArtifactServerConfig, run_artifact_server(), BackupWriter, _canonical_bytes(), EncryptedBackupVault, _extract_content(), LocalAgentMigrationClient (+38 more)

### Community 8 - "Community 8"
Cohesion: 0.03
Nodes (139): assert_not_temporary_chat(), _attachment_spinner_count(), build_browser_context(), build_image_metadata(), build_local_image_paths(), build_test_variables(), _capture_download_from_click(), clear_composer_keyboard() (+131 more)

### Community 9 - "Community 9"
Cohesion: 0.03
Nodes (95): dashboard_defaults(), _hypothesis_variables(), _parse_json(), _persona_summaries(), public_studio(), Unauthenticated generic plate: personas, files, and rules for visitors., Return bounded UI defaults derived from Mongo-backed dashboard config., _studio_payload() (+87 more)

### Community 10 - "Community 10"
Cohesion: 0.06
Nodes (80): install_chatgpt_watchdog(), Install the watchdog for reference generation, conversions, and revisions., persona_slug(), ensure_dirs(), make_run_id(), now_iso(), scan_image_files_for_batch(), scan_prompt_files_for_batch() (+72 more)

### Community 11 - "Community 11"
Cohesion: 0.05
Nodes (27): check_route(), main(), AgentConnection, AgentConnectionManager, accept(), alreadyAccepted(), goHome(), next_free_opencode_model() (+19 more)

### Community 12 - "Community 12"
Cohesion: 0.05
Nodes (25): auth_middleware(), startup(), is_agent_runtime_path(), load_env_file(), control_plane_boundary(), Keep stale dashboards quiet without re-enabling the Render CDP bridge., readyz(), retired_extension_status() (+17 more)

### Community 13 - "Community 13"
Cohesion: 0.06
Nodes (40): decrypt_value(), _derive_fernet_key(), encrypt_value(), _get_fernet(), mask_key(), sign_session(), verify_session(), api_opencode_catalog() (+32 more)

### Community 14 - "Community 14"
Cohesion: 0.06
Nodes (42): ABC, Return safe view of a provider config (no decrypted keys, no ciphertext, no hash, safe_provider_config(), Abstract storage backend for image files.      Implementations: LocalStorageBack, StorageBackend, BaseModel, toggle(), LocalStorageBackend (+34 more)

### Community 15 - "Community 15"
Cohesion: 0.08
Nodes (14): _clean_mongo_job(), cleanup_mongo_job_documents(), Dry-run-first metadata-only cleanup for legacy Mongo agent jobs., _bounded_identifier(), Return a bounded metadata-only job document or reject it., _safe_parameter_value(), validate_job_envelope(), AgentMetadataJobTests (+6 more)

### Community 16 - "Community 16"
Cohesion: 0.16
Nodes (6): BaseHTTPRequestHandler, BrowserLocalDataPlaneE2ETests, _certificate(), _DashboardHandler, _LocalDataPlaneHandler, _RecordingServer

### Community 17 - "Community 17"
Cohesion: 0.2
Nodes (12): detect_wsl_user(), Return the current WSL user (matches /mnt/c/Users/<name> for that user's home)., _kill_chrome(), _launch_visible_browser(), api_kill_chrome(), api_launch_visible_browser(), api_stop_generation(), Launch a visible Chrome instance with CDP enabled so the user can log in     bef (+4 more)

### Community 18 - "Community 18"
Cohesion: 0.6
Nodes (5): _disabled_reference_execution(), _disabled_reference_images(), _disabled_reference_status(), _disabled_reference_workspace(), _local_only()

### Community 19 - "Community 19"
Cohesion: 0.47
Nodes (3): candidatePorts(), detectPort(), probe()

### Community 20 - "Community 20"
Cohesion: 0.4
Nodes (1): RevisionPromptTests

### Community 24 - "Community 24"
Cohesion: 0.67
Nodes (1): LegacyStorageInspectionTests

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): LocalDataPlaneClient

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (1): Upload a local file and return metadata.          Returns dict with at minimum:

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (1): Delete a file by its public ID / path.

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (1): Get the accessible URL for a stored file.

### Community 69 - "Community 69"
Cohesion: 1.0
Nodes (1): Extract Basic snapshot bullet lines from existing persona file.      Keeps exist

## Knowledge Gaps
- **227 isolated node(s):** `Read the canonical stem Render assigned, falling back to the prompt id.`, `Bound a local job row to IDs and scalar control metadata.`, `Resolve job IDs to authoritative local manifest/resource records.`, `Hard-delete soft-deleted resources, then reclaim their unreferenced blobs.`, `Remove abandoned per-job staging trees left behind by interrupted jobs.` (+222 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 20`** (5 nodes): `RevisionPromptTests`, `.setUp()`, `.test_45_revision_keeps_original_prompt_and_editable_safezone()`, `.test_916_revision_uses_conversion_and_916_safezone_not_45_prompt()`, `test_revision_prompt.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (3 nodes): `LegacyStorageInspectionTests`, `.test_legacy_output_root_import_path_is_retired()`, `test_local_agent_migration.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (2 nodes): `local-data-plane.d.ts`, `LocalDataPlaneClient`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `Upload a local file and return metadata.          Returns dict with at minimum:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `Delete a file by its public ID / path.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `Get the accessible URL for a stored file.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 69`** (1 nodes): `Extract Basic snapshot bullet lines from existing persona file.      Keeps exist`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run()` connect `Community 5` to `Community 2`, `Community 6`, `Community 7`, `Community 8`, `Community 9`, `Community 10`, `Community 17`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Why does `get_sync_db()` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 6`, `Community 7`, `Community 11`, `Community 12`, `Community 13`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Why does `fetchJSON()` connect `Community 4` to `Community 0`, `Community 2`, `Community 11`, `Community 14`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Are the 180 inferred relationships involving `get_sync_db()` (e.g. with `startup()` and `readyz()`) actually correct?**
  _`get_sync_db()` has 180 INFERRED edges - model-reasoned connections that need verification._
- **Are the 75 inferred relationships involving `AgentState` (e.g. with `LocalProviderStore` and `ProviderResult`) actually correct?**
  _`AgentState` has 75 INFERRED edges - model-reasoned connections that need verification._
- **Are the 91 inferred relationships involving `ValueError` (e.g. with `.secret_path()` and `.set()`) actually correct?**
  _`ValueError` has 91 INFERRED edges - model-reasoned connections that need verification._
- **Are the 87 inferred relationships involving `list` (e.g. with `.__init__()` and `.execute()`) actually correct?**
  _`list` has 87 INFERRED edges - model-reasoned connections that need verification._