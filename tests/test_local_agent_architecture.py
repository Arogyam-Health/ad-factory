from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path


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
                key = (query["owner_type"], query["owner_id"])
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


class ScopedPromptNameTests(unittest.TestCase):
    def test_scoped_prompt_names_remain_parseable_and_unique(self) -> None:
        from scripts.chatgpt_web_sutomation import discover_prompt_jobs as discover_chatgpt
        from scripts.gemini_web_automation import discover_prompt_jobs as discover_gemini

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
        import scripts.local_agent as local_agent
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


if __name__ == "__main__":
    unittest.main()
