# Graph Report - info  (2026-08-24)

## Corpus Check
- 205 files · ~677,115 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2763 nodes · 8245 edges · 31 communities detected
- Extraction: 55% EXTRACTED · 45% INFERRED · 0% AMBIGUOUS · INFERRED: 3690 edges (avg confidence: 0.78)
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
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 73|Community 73]]

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
- `Dry-run-first metadata-only cleanup for legacy Mongo agent jobs.` --uses--> `AgentPaths`  [INFERRED]
  dashboard/backend/agent/migration.py → local_agent_runtime/storage.py
- `test_org_system()` --calls--> `_can_invite_in_phase2()`  [INFERRED]
  tests/test_smoke.py → dashboard/backend/services/invite_routes.py
- `JobProgressReporter` --uses--> `StructuredBrowserExecutor`  [INFERRED]
  scripts/local_agent.py → local_agent_runtime/structured_browser.py
- `Resolve the human-readable stem shared by a prompt and its generated images.` --uses--> `StructuredBrowserExecutor`  [INFERRED]
  scripts/local_agent.py → local_agent_runtime/structured_browser.py
- `Coalesce remote updates so Render latency never blocks terminal output.` --uses--> `StructuredBrowserExecutor`  [INFERRED]
  scripts/local_agent.py → local_agent_runtime/structured_browser.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.02
Nodes (317): bootstrap_super_admin(), get_super_admin_emails(), require_active_user(), require_super_admin(), require_super_admin_dependency(), admin_delete_config(), admin_delete_user(), admin_disable_org() (+309 more)

### Community 1 - "Community 1"
Cohesion: 0.02
Nodes (328): dashboard_subprocess_env(), debugger_endpoint_reachable(), detect_wsl_windows_host_ip(), extension_browser_required_for_chatgpt(), Return the Windows host IP from WSL's perspective (e.g., 172.18.160.1).     Retu, Return the CDP URL for Windows Chrome from WSL.     Uses the portproxy on 9223 →, render_chatgpt_uses_local_agent(), start_extension_cdp_proxy_for_user() (+320 more)

### Community 2 - "Community 2"
Cohesion: 0.02
Nodes (96): Accept an approval delivered by the authenticated agent channel., rollback(), APIError, _chmod_private(), _decode_metadata(), _digest(), _expected_version(), load_or_create_internal_token() (+88 more)

### Community 3 - "Community 3"
Cohesion: 0.02
Nodes (173): ABC, admin_copy_config(), Return safe view of a provider config (no decrypted keys, no ciphertext, no hash, safe_provider_config(), api_readyz(), get_generic_config_key_public(), get_generic_config_public(), retired_extension_websocket() (+165 more)

### Community 4 - "Community 4"
Cohesion: 0.02
Nodes (152): admin_health(), detect_wsl_user(), Return the current WSL user (matches /mnt/c/Users/<name> for that user's home)., forward(), handle_client(), _attachment_spinner_count(), build_browser_context(), build_image_metadata() (+144 more)

### Community 5 - "Community 5"
Cohesion: 0.02
Nodes (113): Run ChatGPT generation with terminal-error detection and a hard process limit., run_chatgpt_generation_watchdog(), dashboard_defaults(), _hypothesis_variables(), _parse_json(), _persona_summaries(), public_studio(), Unauthenticated generic plate: personas, files, and rules for visitors. (+105 more)

### Community 6 - "Community 6"
Cohesion: 0.03
Nodes (93): cacheKey(), clearCache(), fetchJSON(), invalidateDefaults(), invalidateRuns(), loadPersistent(), networkFetch(), peekCache() (+85 more)

### Community 7 - "Community 7"
Cohesion: 0.03
Nodes (55): ArtifactServer, ArtifactServerConfig, run_artifact_server(), BackupWriter, _canonical_bytes(), EncryptedBackupVault, _extract_content(), LocalAgentMigrationClient (+47 more)

### Community 8 - "Community 8"
Cohesion: 0.03
Nodes (83): install_chatgpt_watchdog(), Install the watchdog for reference generation, conversions, and revisions., parse_args(), load_or_create_device_id(), Local-agent scripts package so `from scripts import generate_ads` works from the, _agent_job_cancel_requested(), api_request(), api_request_retry() (+75 more)

### Community 9 - "Community 9"
Cohesion: 0.03
Nodes (139): assert_not_temporary_chat(), _attachment_spinner_count(), build_browser_context(), build_image_metadata(), build_local_image_paths(), build_test_variables(), _capture_download_from_click(), clear_composer_keyboard() (+131 more)

### Community 10 - "Community 10"
Cohesion: 0.04
Nodes (23): auth_middleware(), is_agent_runtime_path(), control_plane_boundary(), Keep stale dashboards quiet without re-enabling the Render CDP bridge., Reject installed legacy extensions before they reach StaticFiles., readyz(), retired_extension_status(), retired_extension_websocket() (+15 more)

### Community 11 - "Community 11"
Cohesion: 0.06
Nodes (43): decrypt_value(), _derive_fernet_key(), encrypt_value(), _get_fernet(), mask_key(), sign_session(), verify_session(), api_opencode_catalog() (+35 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (40): ensure_dirs(), make_run_id(), store_uploaded_input_images(), api_run_execute_reference(), _load_persona_map(), Reserve a vN folder immediately so structured and reference runs cannot collide., _reserve_batch_name(), _save_product_doc() (+32 more)

### Community 13 - "Community 13"
Cohesion: 0.08
Nodes (13): _clean_mongo_job(), cleanup_mongo_job_documents(), Dry-run-first metadata-only cleanup for legacy Mongo agent jobs., _bounded_identifier(), Return a bounded metadata-only job document or reject it., _safe_parameter_value(), validate_job_envelope(), AgentMetadataJobTests (+5 more)

### Community 14 - "Community 14"
Cohesion: 0.1
Nodes (11): check_route(), main(), AgentConnection, AgentConnectionManager, accept(), alreadyAccepted(), goHome(), agent_runtime_websocket() (+3 more)

### Community 15 - "Community 15"
Cohesion: 0.11
Nodes (18): browser_candidates(), _first_env(), Find Chrome/Brave on the current machine without hardcoding a user home path., resolve_browser_executable(), included_files(), main(), Files that make up the local-agent zip. Paths are repo-relative and must stay th, write_zip() (+10 more)

### Community 16 - "Community 16"
Cohesion: 0.17
Nodes (21): can_copy_config(), can_edit_config(), can_rollback_config(), can_view_config(), can_view_version_snapshot(), can_view_versions(), _get_role_and_org(), copy_config_to_org() (+13 more)

### Community 17 - "Community 17"
Cohesion: 0.22
Nodes (8): create_indexes(), _drop_obsolete_indexes(), _fix_indexes(), Remove unique indexes that would keep Structured and Reference sharing vN., Drop and recreate indexes whose options changed between code versions.      crea, _Collection, ControlPlaneIndexTests, _DB

### Community 18 - "Community 18"
Cohesion: 0.16
Nodes (6): BaseHTTPRequestHandler, BrowserLocalDataPlaneE2ETests, _certificate(), _DashboardHandler, _LocalDataPlaneHandler, _RecordingServer

### Community 19 - "Community 19"
Cohesion: 0.18
Nodes (3): _delete_collections(), request(), RunReconciliationTests

### Community 20 - "Community 20"
Cohesion: 0.36
Nodes (5): adminFetch(), load(), onHash(), patchOrg(), sectionFromHash()

### Community 21 - "Community 21"
Cohesion: 0.6
Nodes (5): _disabled_reference_execution(), _disabled_reference_images(), _disabled_reference_status(), _disabled_reference_workspace(), _local_only()

### Community 22 - "Community 22"
Cohesion: 0.47
Nodes (3): candidatePorts(), detectPort(), probe()

### Community 23 - "Community 23"
Cohesion: 0.4
Nodes (1): RevisionPromptTests

### Community 24 - "Community 24"
Cohesion: 0.67
Nodes (3): mount_react_spa(), Serve the React press-room build as the only dashboard UI., react_dist()

### Community 28 - "Community 28"
Cohesion: 0.67
Nodes (1): LegacyStorageInspectionTests

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (1): LocalDataPlaneClient

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (1): Upload a local file and return metadata.          Returns dict with at minimum:

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (1): Delete a file by its public ID / path.

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (1): Get the accessible URL for a stored file.

### Community 73 - "Community 73"
Cohesion: 1.0
Nodes (1): Extract Basic snapshot bullet lines from existing persona file.      Keeps exist

## Knowledge Gaps
- **227 isolated node(s):** `Read the canonical stem Render assigned, falling back to the prompt id.`, `Bound a local job row to IDs and scalar control metadata.`, `Resolve job IDs to authoritative local manifest/resource records.`, `Hard-delete soft-deleted resources, then reclaim their unreferenced blobs.`, `Remove abandoned per-job staging trees left behind by interrupted jobs.` (+222 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 23`** (5 nodes): `RevisionPromptTests`, `.setUp()`, `.test_45_revision_keeps_original_prompt_and_editable_safezone()`, `.test_916_revision_uses_conversion_and_916_safezone_not_45_prompt()`, `test_revision_prompt.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (3 nodes): `LegacyStorageInspectionTests`, `.test_legacy_output_root_import_path_is_retired()`, `test_local_agent_migration.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (2 nodes): `local-data-plane.d.ts`, `LocalDataPlaneClient`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `Upload a local file and return metadata.          Returns dict with at minimum:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `Delete a file by its public ID / path.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (1 nodes): `Get the accessible URL for a stored file.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 73`** (1 nodes): `Extract Basic snapshot bullet lines from existing persona file.      Keeps exist`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_sync_db()` connect `Community 0` to `Community 1`, `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 7`, `Community 10`, `Community 11`, `Community 16`, `Community 17`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Why does `run()` connect `Community 4` to `Community 1`, `Community 5`, `Community 7`, `Community 8`, `Community 12`?**
  _High betweenness centrality (0.033) - this node is a cross-community bridge._
- **Why does `fetchJSON()` connect `Community 6` to `Community 1`, `Community 2`, `Community 3`, `Community 14`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Are the 180 inferred relationships involving `get_sync_db()` (e.g. with `startup()` and `readyz()`) actually correct?**
  _`get_sync_db()` has 180 INFERRED edges - model-reasoned connections that need verification._
- **Are the 75 inferred relationships involving `AgentState` (e.g. with `LocalProviderStore` and `ProviderResult`) actually correct?**
  _`AgentState` has 75 INFERRED edges - model-reasoned connections that need verification._
- **Are the 91 inferred relationships involving `ValueError` (e.g. with `.secret_path()` and `.set()`) actually correct?**
  _`ValueError` has 91 INFERRED edges - model-reasoned connections that need verification._
- **Are the 88 inferred relationships involving `list` (e.g. with `.__init__()` and `.execute()`) actually correct?**
  _`list` has 88 INFERRED edges - model-reasoned connections that need verification._