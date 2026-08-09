import pytest

from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService


class FakeSplitwiseService(SplitwiseService):
    def __init__(self, response):
        self.response = response

    def _request(self, method, path, *, params=None, json_body=None):
        self.last_request = (method, path, params, json_body)
        return self.response


def test_create_expense_rejects_body_errors_even_with_ok_http_semantics():
    service = FakeSplitwiseService({"expenses": [], "errors": {"base": ["bad split"]}})
    with pytest.raises(SplitwiseAPIError):
        service.create_expense({"cost": "10.00"})


def test_create_expense_requires_expense_object():
    service = FakeSplitwiseService({"expenses": [], "errors": {}})
    with pytest.raises(SplitwiseAPIError):
        service.create_expense({"cost": "10.00"})


def test_create_expense_accepts_empty_errors_and_expense():
    service = FakeSplitwiseService({"expenses": [{"id": 123}], "errors": {}})
    assert service.create_expense({"cost": "10.00"})["expenses"][0]["id"] == 123


def test_create_group_flattens_selected_user_ids_for_splitwise():
    service = FakeSplitwiseService({"group": {"id": 44, "name": "House"}, "errors": {}})

    group = service.create_group(
        name="House",
        group_type="home",
        simplify_by_default=True,
        user_ids=[7, 9],
    )

    assert group["id"] == 44
    assert service.last_request == (
        "POST",
        "/create_group",
        None,
        {
            "name": "House",
            "group_type": "home",
            "simplify_by_default": True,
            "users__0__user_id": 7,
            "users__1__user_id": 9,
        },
    )


@pytest.mark.parametrize("response", [{"success": False}, {"errors": {"base": ["no"]}}])
def test_add_user_to_group_requires_explicit_success(response):
    service = FakeSplitwiseService(response)

    with pytest.raises(SplitwiseAPIError):
        service.add_user_to_group(44, 7)


def test_remove_user_from_group_posts_expected_payload():
    service = FakeSplitwiseService({"success": True})

    service.remove_user_from_group(44, 7)

    assert service.last_request == (
        "POST",
        "/remove_user_from_group",
        None,
        {"group_id": 44, "user_id": 7},
    )


def test_create_friend_posts_invitation_details():
    service = FakeSplitwiseService(
        {"friend": {"id": 73, "registration_status": "dummy"}, "errors": {}}
    )

    friend = service.create_friend(
        email="new.person@example.com", first_name="New", last_name="Person"
    )

    assert friend["id"] == 73
    assert service.last_request == (
        "POST",
        "/create_friend",
        None,
        {
            "user_email": "new.person@example.com",
            "user_first_name": "New",
            "user_last_name": "Person",
        },
    )


def test_create_friend_requires_friend_response():
    service = FakeSplitwiseService({"errors": {}})

    with pytest.raises(SplitwiseAPIError):
        service.create_friend(email="new.person@example.com", first_name="New")
