from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class LocalAgentStorageTests(unittest.TestCase):
    def test_default_root_is_stable_and_not_cwd_relative(self) -> None:
        from local_agent_runtime.storage import resolve_data_root

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            first_cwd = Path(tmp) / "one"
            second_cwd = Path(tmp) / "two"
            first_cwd.mkdir(parents=True)
            second_cwd.mkdir(parents=True)

            original_cwd = Path.cwd()
            try:
                os.chdir(first_cwd)
                first = resolve_data_root(home=home, environ={})
                os.chdir(second_cwd)
                second = resolve_data_root(home=home, environ={})
            finally:
                os.chdir(original_cwd)

            self.assertEqual(first, home / "ad-factory-agent")
            self.assertEqual(second, first)

    def test_content_store_deduplicates_identical_inputs(self) -> None:
        from local_agent_runtime.storage import AgentPaths, ContentStore

        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp) / "agent")
            paths.ensure()
            source_a = Path(tmp) / "a.png"
            source_b = Path(tmp) / "b.png"
            content = b"same-image-content"
            source_a.write_bytes(content)
            source_b.write_bytes(content)

            store = ContentStore(paths)
            first = store.put_file(source_a)
            second = store.put_file(source_b)

            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.path, second.path)
            self.assertEqual(first.path.read_bytes(), content)
            self.assertEqual(len(list(paths.objects.rglob("*.blob"))), 1)

    def test_only_one_process_can_hold_the_data_root_lock(self) -> None:
        from local_agent_runtime.storage import AgentPaths, InstanceLock, LockHeldError

        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp) / "agent")
            paths.ensure()
            first = InstanceLock(paths)
            second = InstanceLock(paths)

            first.acquire()
            try:
                with self.assertRaises(LockHeldError):
                    second.acquire()
            finally:
                first.release()

            second.acquire()
            second.release()

    def test_abandoned_staging_trees_are_swept_but_recent_work_survives(self) -> None:
        import os
        import time

        from local_agent_runtime.storage import AgentPaths, AgentState

        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp) / "agent")
            paths.ensure()
            state = AgentState(paths)
            stale = paths.staging / "structured-browser" / "job-old"
            active = paths.staging / "structured-browser" / "job-new"
            for directory in (stale, active):
                directory.mkdir(parents=True)
                (directory / "upload.png").write_bytes(b"staged-bytes")
            old = time.time() - 90000
            os.utime(stale, (old, old))

            self.assertEqual(state.sweep_staging(), 1)
            self.assertFalse(stale.exists())
            self.assertTrue(active.exists())

    def test_reset_local_data_preserves_product_images_and_device_config(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            legacy = home / "ad-factory-agent-output"
            legacy.mkdir()
            (legacy / "old.png").write_bytes(b"dead")
            paths = AgentPaths(Path(tmp) / "agent")
            state = AgentState(paths)
            (paths.config / "agent.json").write_text('{"device_id": "keep-me"}', encoding="utf-8")
            (paths.staging / "tmp" / "job-1").mkdir(parents=True)
            (paths.staging / "tmp" / "job-1" / "copy.txt").write_text("staged", encoding="utf-8")

            product_src = paths.staging / "product.png"
            product_src.write_bytes(b"\x89PNG\r\n\x1a\nproduct")
            product = state.put_resource(
                source=product_src,
                owner_key="user:user-1",
                kind="product_image",
                logical_key="hero",
                operation_id="put-product",
                media_type="image/png",
            )
            prompt_src = paths.staging / "prompt.txt"
            prompt_src.write_text("prompt body", encoding="utf-8")
            state.put_resource(
                source=prompt_src,
                owner_key="user:user-1",
                kind="prompt",
                logical_key="prm_one",
                operation_id="put-prompt",
                media_type="text/plain; charset=utf-8",
            )
            state.create_run(
                run_id="run-reset",
                owner_key="user:user-1",
                device_id="dev_" + "c" * 32,
                workspace_id="wrk-reset",
                run_number=1,
                flow_type="structured",
                operation_id="create-reset-run",
            )

            report = state.reset_local_data(home=home)

            with state._connect() as conn:
                run_count = conn.execute("SELECT COUNT(*) AS count FROM runs").fetchone()["count"]
                kinds = [
                    row["kind"]
                    for row in conn.execute("SELECT kind FROM resources ORDER BY kind")
                ]
            self.assertEqual(run_count, 0)
            self.assertEqual(kinds, ["product_image"])
            self.assertTrue((paths.config / "agent.json").exists())
            self.assertFalse(legacy.exists())
            self.assertFalse((paths.staging / "tmp" / "job-1").exists())
            self.assertGreaterEqual(report["deleted_runs"], 1)
            self.assertTrue(product.path.exists())

    def test_one_account_cannot_delete_or_reset_another_accounts_local_runs(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState

        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp) / "agent")
            state = AgentState(paths)
            for index, owner in enumerate(("user:user-a", "user:user-b"), start=1):
                state.create_run(
                    run_id=f"run-{owner.split(':')[1]}",
                    owner_key=owner,
                    device_id="dev_" + "d" * 32,
                    workspace_id=f"wrk-{index}",
                    run_number=index,
                    flow_type="structured",
                    operation_id=f"create-{index}",
                )

            with self.assertRaises(ValueError):
                state.delete_run(
                    "run-user-b",
                    operation_id="cross-account-delete",
                    purge_resources=True,
                    owner_key="user:user-a",
                )

            state.delete_run(
                "run-user-a",
                operation_id="own-delete",
                purge_resources=True,
                owner_key="user:user-a",
            )
            with state._connect() as conn:
                remaining = [
                    str(row["run_id"])
                    for row in conn.execute("SELECT run_id FROM runs ORDER BY run_id")
                ]
            self.assertEqual(remaining, ["run-user-b"])

            state.create_run(
                run_id="run-user-a-2",
                owner_key="user:user-a",
                device_id="dev_" + "d" * 32,
                workspace_id="wrk-3",
                run_number=2,
                flow_type="structured",
                operation_id="create-3",
            )
            report = state.reset_local_data(owner_key="user:user-a")
            with state._connect() as conn:
                survivors = [
                    str(row["run_id"])
                    for row in conn.execute("SELECT run_id FROM runs ORDER BY run_id")
                ]
            self.assertEqual(survivors, ["run-user-b"])
            self.assertEqual(report["owner_key"], "user:user-a")
            self.assertFalse(report["staging_removed"])


class RunNumberTests(unittest.TestCase):
    def test_physical_batch_keys_do_not_collide_across_runs(self) -> None:
        from dashboard.backend.services.run_storage import build_storage_batch

        first = build_storage_batch(1, "run-owner-a")
        second = build_storage_batch(1, "run-owner-b")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("v1-"))

    def test_owner_run_numbers_are_atomic_and_independent(self) -> None:
        from dashboard.backend.services.run_storage import reserve_run_number

        class CounterCollection:
            def __init__(self) -> None:
                self.values: dict[tuple[str, str], int] = {}
                self.lock = threading.Lock()

            def find_one_and_update(self, query, update, **kwargs):
                key = (
                    query["owner_type"],
                    query["owner_id"],
                    query.get("flow_family") or "structured",
                )
                with self.lock:
                    self.values[key] = self.values.get(key, 0) + update["$inc"]["value"]
                    return {**query, "value": self.values[key]}

        collection = CounterCollection()
        numbers: list[int] = []
        output_lock = threading.Lock()

        def reserve() -> None:
            value = reserve_run_number("user", "u1", collection=collection)
            with output_lock:
                numbers.append(value)

        threads = [threading.Thread(target=reserve) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(numbers), list(range(1, 21)))
        self.assertEqual(reserve_run_number("org", "o1", collection=collection), 1)
        self.assertEqual(reserve_run_number("user", "u1", collection=collection), 21)
        self.assertEqual(
            reserve_run_number("user", "u1", flow_type="reference", collection=collection),
            1,
        )

    def test_next_number_reuses_deleted_slot_for_this_account_only(self) -> None:
        from dashboard.backend.db.collections import COLL_RUNS
        from dashboard.backend.services.run_storage import (
            display_batch_label,
            reserve_run_number,
        )

        remaining = [
            {
                "user_id": "u1",
                "status": "copy_completed",
                "run_number": number,
                "flow_type": "structured",
                "flow_family": "structured",
            }
            for number in (1, 2)
        ] + [
            {
                "user_id": "other",
                "status": "completed",
                "run_number": number,
                "flow_type": "structured",
                "flow_family": "structured",
            }
            for number in (1, 2, 3, 4, 5)
        ]

        def matches(doc: dict, query: dict) -> bool:
            if doc.get("user_id") != query.get("user_id"):
                return False
            status = str(doc.get("status") or "")
            excluded = (query.get("status") or {}).get("$nin") or []
            if status in excluded:
                return False
            if "$and" in query:
                return str(doc.get("flow_family") or "structured") != "reference"
            if "$or" in query:
                return str(doc.get("flow_family") or "") == "reference"
            return True

        class Runs:
            def find(self, query, projection=None):
                return [doc for doc in remaining if matches(doc, query)]

        db = {COLL_RUNS: Runs()}
        with patch(
            "dashboard.backend.services.run_storage.get_sync_db", return_value=db
        ):
            self.assertEqual(reserve_run_number("user", "u1", user_id="u1"), 3)
            remaining.append(
                {
                    "user_id": "u1",
                    "status": "allocated",
                    "run_number": 3,
                    "flow_type": "structured",
                    "flow_family": "structured",
                }
            )
            self.assertEqual(reserve_run_number("user", "u1", user_id="u1"), 4)
            remaining[:] = [
                doc
                for doc in remaining
                if not (doc.get("user_id") == "u1" and doc.get("run_number") == 3)
            ]
            self.assertEqual(reserve_run_number("user", "u1", user_id="u1"), 3)
            self.assertEqual(reserve_run_number("user", "u2", user_id="u2"), 1)
            self.assertEqual(display_batch_label("structured", 3), "v3")
            self.assertEqual(display_batch_label("reference", 1), "ref_v1")

    def test_structured_and_reference_numbers_are_independent(self) -> None:
        from dashboard.backend.db.collections import COLL_RUNS
        from dashboard.backend.services.run_storage import reserve_run_number

        remaining = [
            {
                "user_id": "u1",
                "status": "completed",
                "run_number": number,
                "flow_type": "structured",
                "flow_family": "structured",
            }
            for number in range(1, 3)
        ] + [
            {
                "user_id": "u1",
                "status": "completed",
                "run_number": number,
                "flow_type": "reference",
                "flow_family": "reference",
            }
            for number in (1,)
        ]

        def family_of(doc: dict) -> str:
            return str(
                doc.get("flow_family")
                or (
                    "reference"
                    if doc.get("flow_type") in {"reference", "reference_image"}
                    else "structured"
                )
            )

        def matches(doc: dict, query: dict) -> bool:
            if doc.get("user_id") != query.get("user_id"):
                return False
            status = str(doc.get("status") or "")
            excluded = (query.get("status") or {}).get("$nin") or []
            if status in excluded:
                return False
            if "$or" in query:
                return family_of(doc) == "reference"
            if "$and" in query:
                return family_of(doc) != "reference"
            return family_of(doc) == (query.get("flow_family") or "structured")

        class Runs:
            def find(self, query, projection=None):
                return [doc for doc in remaining if matches(doc, query)]

        db = {COLL_RUNS: Runs()}
        with patch(
            "dashboard.backend.services.run_storage.get_sync_db", return_value=db
        ):
            self.assertEqual(reserve_run_number("user", "u1", flow_type="reference", user_id="u1"), 2)
            remaining.append(
                {
                    "user_id": "u1",
                    "status": "allocated",
                    "run_number": 2,
                    "flow_type": "reference",
                    "flow_family": "reference",
                }
            )
            self.assertEqual(reserve_run_number("user", "u1", flow_type="structured", user_id="u1"), 3)
            self.assertEqual(reserve_run_number("user", "u1", flow_type="reference", user_id="u1"), 3)


class ScopedPromptNameTests(unittest.TestCase):
    def test_scoped_prompt_names_remain_parseable_and_unique(self) -> None:
        from local_agent_runtime.chatgpt_web_sutomation import discover_prompt_jobs as discover_chatgpt
        from local_agent_runtime.gemini_web_automation import discover_prompt_jobs as discover_gemini

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for scope in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
                (root / f"run_{scope}__HERO_always_hungry_EN_pain_point.txt").write_text(
                    "Create an ad.", encoding="utf-8"
                )

            for discover in (discover_chatgpt, discover_gemini):
                jobs, duplicates = discover(root, "*.txt", False, aspect_ratio="4:5")
                self.assertEqual(len(jobs), 2)
                self.assertEqual(duplicates, [])
                self.assertEqual({job.format_id for job in jobs}, {"HERO"})
                self.assertEqual({job.lang_id for job in jobs}, {"EN"})
                self.assertTrue(all(job.output_stem.removesuffix("_4_5") == job.prompt_path.stem for job in jobs))
                self.assertEqual(
                    {job.output_stem.split("__", 1)[0] for job in jobs},
                    {"run_aaaaaaaaaaaa", "run_bbbbbbbbbbbb"},
                )


class AgentAuthTests(unittest.TestCase):
    def test_saved_agent_credentials_are_scoped_by_dashboard_account(self) -> None:
        import local_agent_runtime.local_agent as local_agent
        from local_agent_runtime.storage import AgentPaths

        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp) / "agent")
            paths.ensure()
            previous_paths = local_agent.AGENT_PATHS
            try:
                local_agent.AGENT_PATHS = paths
                paths.config.joinpath("agent.json").write_text(
                    '{"api_base":"https://dashboard.example",'
                    '"agent_id":"legacy-agent","token":"legacy-token"}',
                    encoding="utf-8",
                )
                local_agent._save_agent_token(
                    "https://dashboard.example",
                    "user-1",
                    "agent-1",
                    "token-1",
                )
                local_agent._save_agent_token(
                    "https://dashboard.example",
                    "user-2",
                    "agent-2",
                    "token-2",
                )

                self.assertEqual(
                    local_agent._load_saved_registration(
                        "https://dashboard.example", "user-1"
                    ),
                    {"agent_id": "agent-1", "token": "token-1"},
                )
                self.assertEqual(
                    local_agent._load_saved_registration(
                        "https://dashboard.example", "user-2"
                    ),
                    {"agent_id": "agent-2", "token": "token-2"},
                )
                self.assertEqual(
                    local_agent._load_only_saved_registration(
                        "https://dashboard.example"
                    ),
                    {"agent_id": "legacy-agent", "token": "legacy-token"},
                )
                self.assertEqual(
                    paths.config.joinpath("agent.json").stat().st_mode & 0o777,
                    0o600,
                )
            finally:
                local_agent.AGENT_PATHS = previous_paths

    def test_runtime_paths_can_use_agent_bearer_without_browser_cookie(self) -> None:
        from dashboard.backend.agent.auth import is_agent_runtime_path

        self.assertTrue(is_agent_runtime_path("/api/agents/heartbeat"))
        self.assertTrue(is_agent_runtime_path("/api/agents/jobs/poll"))
        self.assertTrue(is_agent_runtime_path("/api/agent-runtime/ws"))
        self.assertFalse(is_agent_runtime_path("/api/agents/register"))
        self.assertFalse(is_agent_runtime_path("/api/agents"))
        self.assertFalse(is_agent_runtime_path("/api/agents/jobs/job-1/cancel"))

    def test_connected_websocket_still_sends_http_heartbeat(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "local_agent_runtime" / "local_agent.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'api_request("POST", "/api/agents/heartbeat"',
            source,
        )
        self.assertNotIn("if not WS_CLIENT.connected and now - last_heartbeat", source)


if __name__ == "__main__":
    unittest.main()
