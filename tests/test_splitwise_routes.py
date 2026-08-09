from fastapi.testclient import TestClient

from app.api import splitwise_routes
from app.main import app
from app.services.splitwise_service import SplitwiseAPIError


def test_splitwise_groups_can_be_searched(monkeypatch):
    class FakeSplitwiseService:
        def search_groups(self, query):
            assert query == "house"
            return [{"id": 44, "name": "House"}]

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).get("/splitwise/groups?q=house")

    assert response.status_code == 200
    assert response.json() == [{"id": 44, "name": "House", "invite_link": None}]


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
            "registration_status": None,
        },
        {
            "id": 9,
            "first_name": "Akash",
            "last_name": "Rao",
            "email": None,
            "display_name": "Akash Rao",
            "registration_status": None,
        },
    ]


def test_splitwise_group_can_be_created_with_selected_friends(monkeypatch):
    class FakeSplitwiseService:
        def create_group(self, **kwargs):
            assert kwargs == {
                "name": "Arizona Trip",
                "group_type": "trip",
                "simplify_by_default": True,
                "user_ids": [7, 9],
            }
            return {"id": 55, "name": "Arizona Trip"}

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).post(
        "/splitwise/groups",
        json={
            "name": "  Arizona Trip  ",
            "group_type": "trip",
            "simplify_by_default": True,
            "user_ids": [7, 9, 7],
        },
    )

    assert response.status_code == 201
    assert response.json() == {"id": 55, "name": "Arizona Trip", "invite_link": None}


def test_splitwise_group_rejects_blank_name():
    response = TestClient(app).post("/splitwise/groups", json={"name": "   "})

    assert response.status_code == 422


def test_splitwise_group_member_can_be_added(monkeypatch):
    class FakeSplitwiseService:
        def add_user_to_group(self, group_id, user_id):
            assert (group_id, user_id) == (44, 7)

        def get_group_members(self, group_id):
            assert group_id == 44
            return [{"id": 7, "first_name": "Rahul", "last_name": "Shah"}]

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).post("/splitwise/groups/44/members", json={"user_id": 7})

    assert response.status_code == 200
    assert response.json()[0]["display_name"] == "Rahul Shah"


def test_splitwise_group_member_can_be_removed(monkeypatch):
    class FakeSplitwiseService:
        def remove_user_from_group(self, group_id, user_id):
            assert (group_id, user_id) == (44, 7)

        def get_group_members(self, group_id):
            assert group_id == 44
            return [{"id": 9, "first_name": "Akash", "last_name": "Rao"}]

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).delete("/splitwise/groups/44/members/7")

    assert response.status_code == 200
    assert [member["id"] for member in response.json()] == [9]


def test_splitwise_group_mutation_error_is_returned(monkeypatch):
    class FakeSplitwiseService:
        def remove_user_from_group(self, group_id, user_id):
            raise SplitwiseAPIError("participant has a non-zero balance")

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).delete("/splitwise/groups/44/members/7")

    assert response.status_code == 400
    assert response.json() == {"detail": "participant has a non-zero balance"}


def test_splitwise_group_returns_invite_link(monkeypatch):
    class FakeSplitwiseService:
        def get_group(self, group_id):
            assert group_id == 44
            return {
                "id": 44,
                "name": "House",
                "invite_link": "https://www.splitwise.com/join/example",
            }

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).get("/splitwise/groups/44")

    assert response.status_code == 200
    assert response.json() == {
        "id": 44,
        "name": "House",
        "invite_link": "https://www.splitwise.com/join/example",
    }


def test_non_splitwise_participant_can_be_invited_to_group(monkeypatch):
    class FakeSplitwiseService:
        def create_friend(self, **kwargs):
            assert kwargs == {
                "email": "new.person@example.com",
                "first_name": "New",
                "last_name": "Person",
            }
            return {"id": 73}

        def add_user_to_group(self, group_id, user_id):
            assert (group_id, user_id) == (44, 73)

        def get_group_members(self, group_id):
            assert group_id == 44
            return [
                {
                    "id": 73,
                    "first_name": "New",
                    "last_name": "Person",
                    "email": "new.person@example.com",
                    "registration_status": "dummy",
                }
            ]

    monkeypatch.setattr(splitwise_routes, "SplitwiseService", FakeSplitwiseService)

    response = TestClient(app).post(
        "/splitwise/groups/44/invite",
        json={
            "first_name": " New ",
            "last_name": " Person ",
            "email": "NEW.PERSON@EXAMPLE.COM",
        },
    )

    assert response.status_code == 200
    assert response.json()[0]["registration_status"] == "dummy"


def test_group_invitation_rejects_invalid_email():
    response = TestClient(app).post(
        "/splitwise/groups/44/invite",
        json={"first_name": "New", "email": "not-an-email"},
    )

    assert response.status_code == 422
