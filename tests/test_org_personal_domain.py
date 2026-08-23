from __future__ import annotations

import unittest


class _Orgs:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = [dict(doc) for doc in docs]
        self.updates: list[tuple[dict, dict]] = []

    def find(self, query, projection=None):
        del query, projection
        return [dict(doc) for doc in self.docs]

    def update_one(self, filt, update) -> None:
        self.updates.append((filt, update))
        for doc in self.docs:
            if doc.get("_id") == filt.get("_id"):
                doc.update(update.get("$set") or {})


class _DB(dict):
    pass


class OrgPersonalDomainTests(unittest.TestCase):
    def test_assign_personal_org_domains_rewrites_nulls(self) -> None:
        from dashboard.backend.db.collections import COLL_ORGS
        from dashboard.backend.services.org_helper import (
            assign_personal_org_domains,
            personal_org_domain,
        )

        orgs = _Orgs(
            [
                {"_id": "a", "org_id": "org_one", "domain": None},
                {"_id": "b", "org_id": "org_two"},
            ]
        )
        updated = assign_personal_org_domains(_DB({COLL_ORGS: orgs}))

        self.assertEqual(updated, 2)
        self.assertEqual(
            orgs.docs[0]["domain"],
            personal_org_domain("org_one"),
        )
        self.assertEqual(
            orgs.docs[1]["domain"],
            personal_org_domain("org_two"),
        )

    def test_create_org_always_stores_a_unique_domain(self) -> None:
        from pathlib import Path

        from dashboard.backend.services.org_helper import personal_org_domain
        from dashboard.backend.services import org_routes

        source = Path(org_routes.__file__).read_text(encoding="utf-8")
        self.assertIn("personal_org_domain(org_id)", source)
        self.assertIn("domain or personal_org_domain(org_id)", source)
        self.assertEqual(personal_org_domain("org_abc"), "personal:org_abc")

    def test_purge_org_owned_documents_hard_deletes_configs(self) -> None:
        from dashboard.backend.db.collections import (
            COLL_CONFIG_VERSIONS,
            COLL_LOCAL_CONFIG_REFERENCES,
            COLL_USER_CONFIGS,
        )
        from dashboard.backend.services.org_helper import purge_org_owned_documents

        class _Coll:
            def __init__(self) -> None:
                self.queries: list[dict] = []

            def delete_many(self, query):
                self.queries.append(query)
                return type("R", (), {"deleted_count": 2})()

        configs, versions, refs = _Coll(), _Coll(), _Coll()
        report = purge_org_owned_documents(
            {
                COLL_USER_CONFIGS: configs,
                COLL_CONFIG_VERSIONS: versions,
                COLL_LOCAL_CONFIG_REFERENCES: refs,
            },
            "org_dead",
        )
        self.assertEqual(configs.queries, [{"owner_type": "org", "owner_id": "org_dead"}])
        self.assertEqual(
            versions.queries,
            [{"$or": [{"owner_type": "org", "owner_id": "org_dead"}, {"org_id": "org_dead"}]}],
        )
        self.assertEqual(refs.queries, [{"owner_id": "org_dead"}])
        self.assertEqual(report["configs"], 2)
        self.assertEqual(report["versions"], 2)
        self.assertEqual(report["local_refs"], 2)

    def test_delete_org_purges_configs_instead_of_soft_deleting_them(self) -> None:
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from dashboard.backend.services import org_routes

        source = Path(org_routes.__file__).read_text(encoding="utf-8")
        self.assertIn("purge_org_owned_documents", source)
        self.assertNotIn("Deactivate org config", source)

        db = MagicMock()
        user = {"user_id": "usr_owner", "email": "owner@example.com"}
        org = {
            "org_id": "org_dead",
            "owner_user_id": "usr_owner",
            "name": "test",
            "is_active": True,
        }
        with (
            patch.object(org_routes, "_get_active_user_org", return_value=org),
            patch.object(org_routes, "get_sync_db", return_value=db),
            patch.object(org_routes, "write_audit_event"),
            patch.object(org_routes, "purge_org_owned_documents") as purge,
        ):
            result = org_routes.delete_org("org_dead", user)
        purge.assert_called_once_with(db, "org_dead")
        self.assertTrue(result["ok"])
        db[org_routes.COLL_ORGS].update_one.assert_not_called()
        db[org_routes.COLL_ORG_MEMBERS].update_many.assert_not_called()

    def test_purge_org_owned_documents_removes_members_invites_runs_and_org(self) -> None:
        from dashboard.backend.db.collections import (
            COLL_AGENT_JOBS,
            COLL_AUDIT_LOGS,
            COLL_FILE_MAP,
            COLL_IMAGES,
            COLL_LLM_TRACES,
            COLL_ORG_INVITES,
            COLL_ORG_MEMBERS,
            COLL_ORGS,
            COLL_PROMPT_DELIVERIES,
            COLL_PROMPTS,
            COLL_RENDER_COPY_JOBS,
            COLL_RUN_COUNTERS,
            COLL_RUNS,
        )
        from dashboard.backend.services.org_helper import purge_org_owned_documents

        class _Coll:
            def __init__(self, docs=None) -> None:
                self.docs = list(docs or [])
                self.deleted: list[dict] = []
                self.deleted_one: list[dict] = []

            def find(self, query, projection=None):
                del query, projection
                return list(self.docs)

            def delete_many(self, query):
                self.deleted.append(query)
                return type("R", (), {"deleted_count": 1})()

            def delete_one(self, query):
                self.deleted_one.append(query)
                return type("R", (), {"deleted_count": 1})()

        runs = _Coll([{"run_id": "run_org_1", "owner_id": "org_dead"}])
        members, invites, orgs = _Coll(), _Coll(), _Coll()
        report = purge_org_owned_documents(
            {
                COLL_RUNS: runs,
                COLL_ORG_MEMBERS: members,
                COLL_ORG_INVITES: invites,
                COLL_ORGS: orgs,
                COLL_PROMPTS: _Coll(),
                COLL_IMAGES: _Coll(),
                COLL_FILE_MAP: _Coll(),
                COLL_PROMPT_DELIVERIES: _Coll(),
                COLL_RENDER_COPY_JOBS: _Coll(),
                COLL_AGENT_JOBS: _Coll(),
                COLL_LLM_TRACES: _Coll(),
                COLL_RUN_COUNTERS: _Coll(),
                COLL_AUDIT_LOGS: _Coll(),
            },
            "org_dead",
        )
        self.assertIn({"run_id": {"$in": ["run_org_1"]}}, runs.deleted)
        self.assertEqual(members.deleted, [{"org_id": "org_dead"}])
        self.assertEqual(invites.deleted, [{"org_id": "org_dead"}])
        self.assertEqual(orgs.deleted_one, [{"org_id": "org_dead"}])
        self.assertGreaterEqual(report["runs"], 1)
        self.assertGreaterEqual(report["orgs"], 1)


if __name__ == "__main__":
    unittest.main()
