from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalhostFrontendPairingTests(unittest.TestCase):
    def test_dashboard_pairing_client_wires_complete_challenge_exchange(self) -> None:
        source = (ROOT / "dashboard/frontend/js/local-data-plane.js").read_text(
            encoding="utf-8"
        )
        for fragment in (
            "/v1/info",
            "/v1/pairing/challenges",
            "/api/agents/pairing/challenges",
            "/v1/pairing/sessions",
            "sessionStorage",
            "credentials: \"same-origin\"",
        ):
            self.assertIn(fragment, source)
        render_submission = source[
            source.index("/api/agents/pairing/challenges") :
        ]
        self.assertNotIn("localBaseUrl", render_submission[:1200])
        self.assertNotIn("localhostUrl", render_submission[:1200])

    def test_pairing_client_is_integrated_with_dashboard_modules(self) -> None:
        html = (ROOT / "dashboard/frontend/index.html").read_text(encoding="utf-8")
        self.assertNotIn('<script type="module" src="/js/local-data-plane.js"></script>', html)
        main = (ROOT / "dashboard/frontend/js/main.js").read_text(encoding="utf-8")
        reference = (ROOT / "dashboard/frontend/js/reference-flow.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('from "./local-data-plane.js"', main)
        self.assertIn('from "./local-data-plane.js"', reference)

    def test_legacy_scripts_can_call_safe_global_client(self) -> None:
        source = (ROOT / "dashboard/frontend/js/local-data-plane.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("window.AdFactoryLocalDataPlane", source)
        self.assertNotIn("access_token:", source)

    def test_all_file_blob_and_formdata_uploads_target_loopback(self) -> None:
        for relative in (
            "dashboard/frontend/js/main.js",
            "dashboard/frontend/js/reference-flow.js",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            for render_path in (
                "/api/runs/execute",
                "/api/runs/execute-reference",
                "/api/reference-images",
                "/api/reference-workspace/product-images",
                "/api/reference-workspace/product-document",
                "/api/upload-input-images",
                "/api/user/provider-config",
                "/api/google/models",
            ):
                self.assertNotIn(render_path, source, f"{relative} still uses {render_path}")
        source = (ROOT / "dashboard/frontend/js/local-data-plane.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("FormData", source)
        self.assertIn("/v1/assets", source)
        self.assertIn("authorizedFetch", source)
        main = (ROOT / "dashboard/frontend/js/main.js").read_text(encoding="utf-8")
        self.assertIn("putProviderConfig(provider, config", main)
        self.assertIn("/v1/provider-configs", source)

    def test_images_use_authenticated_blob_urls_not_tokenized_urls(self) -> None:
        local_source = (
            ROOT / "dashboard/frontend/js/local-data-plane.js"
        ).read_text(encoding="utf-8")
        reference_source = (
            ROOT / "dashboard/frontend/js/reference-flow.js"
        ).read_text(encoding="utf-8")
        combined = local_source + reference_source
        self.assertIn("URL.createObjectURL", combined)
        self.assertIn("/content", combined)
        self.assertNotIn("?token=", combined)
        self.assertNotIn("searchParams.set(\"token\"", combined)

    def test_structured_run_stages_versioned_conversion_prompt_only_to_localhost(self) -> None:
        main = (ROOT / "dashboard/frontend/js/main.js").read_text(encoding="utf-8")
        self.assertIn("conversionPromptResource", main)
        self.assertIn('role: "conversion_prompt"', main)
        self.assertIn('"conversion_prompt": {', main)
        self.assertIn("resource_id: conversionPromptResource.resource_id", main)
        self.assertIn("version: conversionPromptResource.version", main)
        generation_clients = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in (
                "dashboard/frontend/js/prompts.js",
                "dashboard/frontend/js/runs.js",
            )
        )
        self.assertNotIn("conversion_916_prompt", generation_clients)
        self.assertNotIn("conversion_prompt_text", generation_clients)

    def test_browser_network_harness_keeps_bytes_local_and_render_metadata_only(self) -> None:
        module_uri = (
            ROOT / "dashboard/frontend/js/local-data-plane.js"
        ).resolve().as_uri()
        script = f"""
globalThis.window = globalThis;
globalThis.sessionStorage = {{
  values: new Map(),
  getItem(key) {{ return this.values.get(key) || null; }},
  setItem(key, value) {{ this.values.set(key, value); }},
  removeItem(key) {{ this.values.delete(key); }},
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
sessionStorage.setItem("ad_factory_local_session:dev_" + "a".repeat(32), JSON.stringify({{
  access_token: "memory-only",
  expires_at: Date.now() / 1000 + 60,
  device_id: "dev_" + "a".repeat(32),
  agent_id: "agent_test",
}}));
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


if __name__ == "__main__":
    unittest.main()
