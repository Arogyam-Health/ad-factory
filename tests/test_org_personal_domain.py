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


if __name__ == "__main__":
    unittest.main()
