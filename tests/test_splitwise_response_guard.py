import pytest

from app.services.splitwise_service import SplitwiseAPIError, SplitwiseService


class FakeSplitwiseService(SplitwiseService):
    def __init__(self, response):
        self.response = response

    def _request(self, method, path, *, params=None, json_body=None):
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
