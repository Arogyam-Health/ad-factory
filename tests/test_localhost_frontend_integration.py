from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLANE = ROOT / "dashboard/web/src/lib/local-data-plane.js"
STUDIO = ROOT / "dashboard/web/src/pages/Studio.tsx"
REFERENCE = ROOT / "dashboard/web/src/pages/studio/ReferencePanel.tsx"
PROMPT_COPY = ROOT / "dashboard/web/src/lib/prompt-copy.js"


class LocalhostFrontendPairingTests(unittest.TestCase):
    def test_dashboard_pairing_client_wires_complete_challenge_exchange(self) -> None:
        source = PLANE.read_text(encoding="utf-8")
        for fragment in (
            "/v1/info",
            "/v1/pairing/challenges",
            "/api/agents/pairing/challenges",
            "/v1/pairing/sessions",
            "localStorage",
            "credentials: \"same-origin\"",
        ):
            self.assertIn(fragment, source)
        render_submission = source[
            source.index("/api/agents/pairing/challenges") :
        ]
        self.assertNotIn("localBaseUrl", render_submission[:1200])
        self.assertNotIn("localhostUrl", render_submission[:1200])

    def test_pairing_client_is_integrated_with_dashboard_modules(self) -> None:
        html = (ROOT / "dashboard/web/index.html").read_text(encoding="utf-8")
        studio = STUDIO.read_text(encoding="utf-8")
        reference = REFERENCE.read_text(encoding="utf-8")
        self.assertNotIn('<script type="module" src="/js/local-data-plane.js"></script>', html)
        self.assertIn("local-data-plane.js", studio)
        self.assertIn("local-data-plane.js", reference)
        self.assertIn("localDataPlane.allocateLocalRun", reference)
        self.assertNotIn("await ensureReferenceLocal();", reference)

    def test_legacy_scripts_can_call_safe_global_client(self) -> None:
        source = PLANE.read_text(encoding="utf-8")
        self.assertIn("window.AdFactoryLocalDataPlane", source)
        self.assertNotIn("access_token:", source)

    def test_all_file_blob_and_formdata_uploads_target_loopback(self) -> None:
        for path in (STUDIO, REFERENCE):
            source = path.read_text(encoding="utf-8")
            for render_path in (
                "/api/runs/execute",
                "/api/runs/execute-reference",
                "/api/reference-images",
                "/api/reference-workspace/product-images",
                "/api/reference-workspace/product-document",
                "/api/upload-input-images",
                "/api/google/models",
            ):
                self.assertNotIn(render_path, source, f"{path} still uses {render_path}")
        source = PLANE.read_text(encoding="utf-8")
        self.assertIn("FormData", source)
        self.assertIn("/v1/assets", source)
        self.assertIn("authorizedFetch", source)
        self.assertNotIn("localDataPlane.putProviderConfig(", STUDIO.read_text(encoding="utf-8"))
        self.assertIn("/v1/provider-configs", source)

    def test_images_use_authenticated_blob_urls_not_tokenized_urls(self) -> None:
        local_source = PLANE.read_text(encoding="utf-8")
        reference_source = REFERENCE.read_text(encoding="utf-8")
        combined = local_source + reference_source
        self.assertIn("URL.createObjectURL", combined)
        self.assertIn("/content", combined)
        self.assertNotIn("?token=", combined)
        self.assertNotIn("searchParams.set(\"token\"", combined)

    def test_authenticated_local_event_stream_resumes_from_sequence(self) -> None:
        client = PLANE.read_text(encoding="utf-8")
        self.assertIn("async streamEvents({", client)
        self.assertIn("/v1/events?after=", client)
        self.assertIn("Authorization", client)
        self.assertIn("cursor = Math.max", client)

    def test_conversion_prompt_is_materialized_only_when_local_image_generation_starts(self) -> None:
        studio = STUDIO.read_text(encoding="utf-8")
        agent = (ROOT / "local_agent_runtime" / "local_agent.py").read_text(encoding="utf-8")
        routes = (
            ROOT / "dashboard" / "backend" / "agent" / "routes.py"
        ).read_text(encoding="utf-8")
        start = studio[studio.index("async function startStructured()"):]
        self.assertNotIn("conversionPromptResource", studio)
        self.assertNotIn("conversion_prompt_text", start)
        self.assertIn("/image-context", agent)
        self.assertIn("conversion_916_prompt", routes)

    def test_browser_network_harness_keeps_bytes_local_and_render_metadata_only(self) -> None:
        module_uri = PLANE.resolve().as_uri()
        script = f"""
globalThis.window = globalThis;
globalThis.localStorage = {{
  values: new Map(),
  getItem(key) {{ return this.values.get(key) || null; }},
  setItem(key, value) {{ this.values.set(key, value); }},
  removeItem(key) {{ this.values.delete(key); }},
  key(index) {{ return [...this.values.keys()][index] || null; }},
  get length() {{ return this.values.size; }},
}};
globalThis.sessionStorage = {{
  values: new Map(),
  getItem(key) {{ return this.values.get(key) || null; }},
  setItem(key, value) {{ this.values.set(key, value); }},
  removeItem(key) {{ this.values.delete(key); }},
  key(index) {{ return [...this.values.keys()][index] || null; }},
  get length() {{ return this.values.size; }},
}};
const calls = [];
globalThis.fetch = async (url, options = {{}}) => {{
  calls.push({{ url: String(url), options }});
  const body = String(url).includes("/api/runs/allocate")
    ? {{ run_id: "run_test", run_number: 7, display_batch: "v7", agent_id: "agent_test", device_id: "dev_" + "a".repeat(32) }}
    : String(url).endsWith("/v1/runs")
      ? {{ run_id: "run_test", workspace_id: "wrk_test" }}
      : {{ resource_id: "res_test", version: 1, sha256: "a".repeat(64), bytes: 8, kind: "product_image" }};
  return new Response(JSON.stringify(body), {{ status: String(url).endsWith("/v1/runs") || String(url).includes("/v1/assets") ? 201 : 200 }});
}};
const mod = await import({json.dumps(module_uri)});
const client = new mod.LocalDataPlaneClient();
client.storeSession({{
  access_token: "memory-only",
  expires_at: Date.now() / 1000 + 60,
  device_id: "dev_" + "a".repeat(32),
  agent_id: "agent_test",
  owner_type: "user",
  owner_id: "user_test",
}});
await client.allocateRun({{
  agentId: "agent_test",
  deviceId: "dev_" + "a".repeat(32),
  ownerType: "user",
  ownerId: "user_test",
  flowType: "structured",
  settings: {{ provider: "google", batch_size: 10 }},
}});
await client.createRun({{
  runId: "run_test", workspaceId: "wrk_test", runNumber: 7,
  flowType: "structured", deviceId: "dev_" + "a".repeat(32),
}});
await client.uploadAssets([new Blob(["pngbytes"], {{ type: "image/png" }})], {{
  kind: "product_image", deviceId: "dev_" + "a".repeat(32), operationId: "op_test",
}});
const simplified = calls.map((call) => ({{
  url: call.url,
  bodyType: call.options.body?.constructor?.name || "",
  body: typeof call.options.body === "string" ? JSON.parse(call.options.body) : null,
}}));
console.log(JSON.stringify(simplified));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        calls = json.loads(completed.stdout)
        render_calls = [item for item in calls if item["url"].startswith("/api/")]
        local_uploads = [
            item
            for item in calls
            if item["url"].startswith("http://127.0.0.1:8765/v1/assets")
        ]
        self.assertEqual(len(render_calls), 1)
        self.assertEqual(render_calls[0]["bodyType"], "String")
        self.assertNotIn("file", json.dumps(render_calls[0]["body"]).lower())
        self.assertEqual(local_uploads[0]["bodyType"], "FormData")

    def test_local_prompt_card_uses_the_delivered_prompt_identifier(self) -> None:
        studio = STUDIO.read_text(encoding="utf-8")
        self.assertNotIn("pp?.prompt_id", studio)
        self.assertIn("prompt_count", studio)

    def test_local_output_poll_preserves_expanded_prompt_editor(self) -> None:
        studio = STUDIO.read_text(encoding="utf-8")
        self.assertIn("prompt_count", studio)
        self.assertIn("image_count", studio)

    def test_prompt_loading_repairs_missing_local_pairing_session(self) -> None:
        client = PLANE.read_text(encoding="utf-8")
        studio = STUDIO.read_text(encoding="utf-8")
        self.assertIn("registeredAgent(deviceId, preferredAgentId", client)
        self.assertIn("item.agent_id === preferredAgentId", client)
        self.assertIn("_isOnlineAgent", client)
        self.assertIn("this.session(info.device_id, owner)", client)
        self.assertIn("targetAddressSpace: \"loopback\"", client)
        self.assertIn("--api-base pointing at this site", client)
        self.assertNotIn("The selected run belongs to a different local agent", client)
        self.assertIn("ensurePaired({", studio)
        self.assertIn("{ silent: true }", studio)
        self.assertIn("localDataPlane.discover()", studio)
        self.assertIn("restoreStoredSession", studio)
        self.assertIn("restoreStoredSession(preferredOwners", client)
        self.assertIn("Pair local agent", studio)

    def test_prompt_copy_parser_only_returns_exact_on_image_copy(self) -> None:
        script = """
import { exactOnImageCopyLines } from './dashboard/web/src/lib/prompt-copy.js';
const formats = [
  `PERSONA INPUT BLOCK
- Persona: Busy parent
EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING
- Headline: A calmer routine
- Support line: Built for real days
- CTA: Learn more
Render every character exactly as written. No paraphrasing.
- Proof bar is present exactly once.
NEGATIVE CONSTRAINTS
- Do not add badges.`,
  `EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING
- Headline: Before and after
- Left situation 1: Constant cravings
- Right shift 1: Feel in control
- CTA: Start today
Render every character exactly as written.`,
  `EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING
- Headline: Three practical benefits
- Bullet 1: Easy routine
- Bullet 2: Verified ingredients
- CTA: See how it works
Render every character exactly as written.`,
];
console.log(JSON.stringify(formats.map(exactOnImageCopyLines)));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        parsed = json.loads(result.stdout)
        self.assertEqual(
            [line["label"] for line in parsed[0]],
            ["Headline", "Support line", "CTA"],
        )
        self.assertEqual(
            [line["label"] for line in parsed[1]],
            ["Headline", "Left situation 1", "Right shift 1", "CTA"],
        )
        self.assertEqual(
            [line["label"] for line in parsed[2]],
            ["Headline", "Bullet 1", "Bullet 2", "CTA"],
        )

    def test_prompt_copy_edit_preserves_full_generation_prompt(self) -> None:
        script = """
import { replaceExactOnImageCopy } from './dashboard/web/src/lib/prompt-copy.js';
const original = `PRODUCT LOCK BLOCK
- Keep packaging exact.
EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING
- Headline: Old headline
- CTA: Old CTA
Render every character exactly as written. No paraphrasing.
NEGATIVE CONSTRAINTS
- Do not add badges.`;
console.log(replaceExactOnImageCopy(original, [
  { label: 'Headline', value: 'New headline' },
  { label: 'CTA', value: 'New CTA' },
]));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        updated = result.stdout
        self.assertIn("- Headline: New headline", updated)
        self.assertIn("- CTA: New CTA", updated)
        self.assertNotIn("Old headline", updated)
        self.assertIn("PRODUCT LOCK BLOCK", updated)
        self.assertIn("- Keep packaging exact.", updated)
        self.assertIn("NEGATIVE CONSTRAINTS", updated)
        self.assertIn("- Do not add badges.", updated)

    def test_cas_outputs_are_the_only_image_source_and_retain_blobs_on_failure(self) -> None:
        plane = PLANE.read_text(encoding="utf-8")
        studio = STUDIO.read_text(encoding="utf-8")
        self.assertNotIn("refreshLocalArtifactManifest", studio)
        self.assertNotIn("LOCAL_ARTIFACT_CACHE_KEY", studio)
        self.assertIn("CAS_CACHE_NAME", plane)
        self.assertIn("LazyAsset", studio)
        self.assertIn("displayRunStatus", studio)


if __name__ == "__main__":
    unittest.main()
