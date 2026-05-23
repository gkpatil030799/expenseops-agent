from fastapi.testclient import TestClient

from app.api import splitwise_routes
from app.main import app


def test_splitwise_groups_can_be_searched(monkeypatch):
    class FakeSplitwiseService:
        def search_groups(self, query):
            assert query == "house"
            return [{"id": 44, "name": "House"}]

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).get("/splitwise/groups?q=house")

    assert response.status_code == 200
    assert response.json() == [{"id": 44, "name": "House"}]


def test_splitwise_group_members_are_returned(monkeypatch):
    class FakeSplitwiseService:
        def get_group_members(self, group_id):
            assert group_id == 44
            return [
                {"id": 7, "first_name": "Rahul", "last_name": "Shah", "email": None},
                {"id": 9, "first_name": "Akash", "last_name": "Rao", "email": None},
            ]

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).get("/splitwise/groups/44/members")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 7,
            "first_name": "Rahul",
            "last_name": "Shah",
            "email": None,
            "display_name": "Rahul Shah",
        },
        {
            "id": 9,
            "first_name": "Akash",
            "last_name": "Rao",
            "email": None,
            "display_name": "Akash Rao",
        },
    ]
