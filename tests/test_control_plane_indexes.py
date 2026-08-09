from __future__ import annotations

import unittest


class _Collection:
    def __init__(self, indexes: list[dict]) -> None:
        self.indexes = [dict(index) for index in indexes]
        self.dropped: list[str] = []
        self.created: list[dict] = []

    def list_indexes(self):
        return [dict(index) for index in self.indexes]

    def drop_index(self, name: str) -> None:
        self.dropped.append(name)
        self.indexes = [index for index in self.indexes if index["name"] != name]

    def create_indexes(self, indexes) -> None:
        for index in indexes:
            document = dict(index.document)
            self.created.append(document)
            self.indexes.append(document)


class _DB(dict):
    def __missing__(self, key):
        collection = _Collection([])
        self[key] = collection
        return collection


class ControlPlaneIndexTests(unittest.TestCase):
    def test_each_collection_declares_unique_index_names(self) -> None:
        from dashboard.backend.db.indexes import INDEX_SPECS

        for collection, indexes in INDEX_SPECS.items():
            with self.subTest(collection=collection):
                names = [index.document["name"] for index in indexes]
                self.assertEqual(len(names), len(set(names)))

    def test_stale_owner_active_index_is_replaced_with_unique_partial_index(
        self,
    ) -> None:
        from dashboard.backend.db.collections import COLL_USER_CONFIGS
        from dashboard.backend.db.indexes import _fix_indexes

        stale_name = "owner_type_1_owner_id_1_is_active_1"
        collection = _Collection(
            [
                {
                    "name": stale_name,
                    "key": {
                        "owner_type": 1,
                        "owner_id": 1,
                        "is_active": 1,
                    },
                }
            ]
        )
        db = _DB({COLL_USER_CONFIGS: collection})

        result = _fix_indexes(db)

        self.assertEqual(result[f"{COLL_USER_CONFIGS}.{stale_name}"], 1)
        self.assertEqual(collection.dropped, [stale_name])
        self.assertEqual(len(collection.created), 1)
        self.assertTrue(collection.created[0]["unique"])
        self.assertEqual(
            collection.created[0]["partialFilterExpression"],
            {"is_active": True},
        )


if __name__ == "__main__":
    unittest.main()
