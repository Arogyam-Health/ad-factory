from __future__ import annotations

import unittest
from unittest.mock import patch


class InviteAcceptRedirectTests(unittest.TestCase):
    def test_repeat_accept_by_the_same_member_is_success(self) -> None:
        from dashboard.backend.services.invite_routes import accept_invite

        invite = {
            "_id": "oid",
            "status": "accepted",
            "org_id": "org-1",
            "email": "a@example.com",
            "accepted_by_user_id": "user-1",
        }
        org = {"org_id": "org-1", "name": "Arogyam"}
        member = {"user_id": "user-1", "org_id": "org-1", "status": "active"}
        with (
            patch(
                "dashboard.backend.services.invite_routes.get_invite_by_token",
                return_value=invite,
            ),
            patch(
                "dashboard.backend.services.invite_routes.get_org_by_id",
                return_value=org,
            ),
            patch(
                "dashboard.backend.services.invite_routes.find_active_membership",
                return_value=member,
            ),
        ):
            result = accept_invite(
                "token",
                user={"user_id": "user-1", "email": "a@example.com"},
            )
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["already_accepted"])
        self.assertEqual(result["redirect"], "/")
