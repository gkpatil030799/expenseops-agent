from app.config import Settings
from app.models import PlaidItem
from app.services import transaction_service
from app.services.transaction_service import TransactionService, _plaid_token_environment


def test_plaid_token_environment_is_inferred_from_token_prefix():
    assert _plaid_token_environment("access-sandbox-token") == "sandbox"
    assert _plaid_token_environment("access-production-token") == "production"
    assert _plaid_token_environment("access-development-token") == "development"
    assert _plaid_token_environment("access-custom-token") is None


def test_sync_all_items_skips_items_from_different_plaid_environment(monkeypatch):
    sandbox_item = PlaidItem(
        item_id="sandbox-item",
        access_token_encrypted="encrypted-sandbox",
        institution_name="Sandbox Bank",
    )
    production_item = PlaidItem(
        item_id="production-item",
        access_token_encrypted="encrypted-production",
        institution_name="Production Bank",
    )
    token_by_encrypted_value = {
        "encrypted-sandbox": "access-sandbox-token",
        "encrypted-production": "access-production-token",
    }

    class FakeScalars:
        def __iter__(self):
            return iter([sandbox_item, production_item])

    class FakeExecuteResult:
        def scalars(self):
            return FakeScalars()

    class FakeDb:
        def execute(self, _query):
            return FakeExecuteResult()

        def commit(self):
            pass

    class FakePlaidService:
        def transactions_sync(self, *, access_token, cursor):
            assert access_token == "access-production-token"
            assert cursor is None
            return {
                "added": [],
                "modified": [],
                "removed": [],
                "next_cursor": "cursor",
                "has_more": False,
            }

    monkeypatch.setattr(
        transaction_service,
        "decrypt_secret",
        lambda encrypted: token_by_encrypted_value[encrypted],
    )

    service = TransactionService(
        FakeDb(),
        settings=Settings(plaid_env="production"),
        plaid_service=FakePlaidService(),
        splitwise_service=object(),
    )

    result = service.sync_all_items()

    assert result["sandbox-item"]["skipped"] == 1
    assert "linked in sandbox" in result["sandbox-item"]["reason"]
    assert result["production-item"] == {"added": 0, "modified": 0, "removed": 0}
