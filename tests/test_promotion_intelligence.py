from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.promotion_routes import (
    dismiss,
    feedback,
    list_promotions,
    restore,
    save,
    unmute_merchant,
    unsave,
)
from app.config import Settings
from app.db import Base
from app.models import (
    GmailSyncCheckpoint,
    HouseholdItem,
    PromotionDigestRun,
    PromotionFeedback,
    PromotionMessage,
    PromotionOffer,
    PurchaseReceipt,
)
from app.promotion_schemas import PromotionFeedbackRequest
from app.services.gmail_promotion_ingestion_service import GmailPromotionIngestionService
from app.services.promotion_digest_service import PromotionDigestService
from app.services.promotion_extraction_service import ExtractedOffer, PromotionExtractionService
from app.services.promotion_ranking_service import PromotionRankingService
from app.services.promotion_trust_service import safe_destination


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'promotions.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def config(**values):
    return Settings(
        _env_file=None,
        promotions_enabled=True,
        gmail_client_id="id",
        gmail_client_secret="secret",
        gmail_refresh_token="refresh",
        **values,
    )


def persist_minimum_score(db, value: float) -> None:
    PromotionRankingService(db, config(promotions_min_score=value)).settings()
    db.commit()


def message(
    message_id: str,
    subject: str,
    body: str,
    *,
    labels=None,
    sender="Target <deals@target.com>",
    thread="thread",
):
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "id": message_id,
        "threadId": thread,
        "historyId": "12",
        "labelIds": labels or ["CATEGORY_PROMOTIONS"],
        "internalDate": "1786320000000",
        "snippet": body[:100],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": "Sun, 09 Aug 2026 12:00:00 +0000"},
            ],
            "body": {"data": encoded},
        },
    }


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("Save 20% off laundry", "percent_off", 20),
        ("Get $10 off your order", "amount_off", 10),
        ("BOGO burritos this week", "bogo", None),
        ("Free entrée with purchase", "free_item", None),
    ],
)
def test_deterministic_offer_types(text, kind, value):
    offer = PromotionExtractionService().extract(subject=text, body="", sender_name="Shop")[0]
    assert offer.offer_type == kind
    assert (offer.percent_off or offer.amount_off) == value


def test_extracts_code_minimum_spend_and_date_only_expiry():
    offer = PromotionExtractionService().extract(
        subject="$10 off $100", body="Use code SAVE10. Expires 08/31/2026", sender_name="Shop"
    )[0]
    assert offer.minimum_spend == 100
    assert offer.promo_code == "SAVE10"
    assert offer.expiry_precision == "date"
    assert offer.expires_at.date().isoformat() == "2026-08-31"


def test_unknown_expiry_remains_unknown():
    offer = PromotionExtractionService().extract(subject="20% off", body="", sender_name="Shop")[0]
    assert offer.expires_at is None
    assert offer.expiry_precision == "unknown"


def test_generic_marketing_is_not_an_offer():
    assert (
        PromotionExtractionService().extract(
            subject="Explore our latest arrivals", body="Shop the collection", sender_name="Shop"
        )
        == []
    )


def test_structured_discount_offer_and_multiple_offers():
    html = """<script type="application/ld+json">
    [{"@type":"DiscountOffer","description":"20% off laundry"},
    {"@type":"Offer","description":"$5 off groceries"}]
    </script>"""
    offers = PromotionExtractionService().extract(
        subject="Deals", body="", sender_name="Target", html=html
    )
    assert len(offers) == 2
    assert all(value.confidence == 0.95 for value in offers)


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/plain,no", "file:///tmp/a", "http://example.com"]
)
def test_unsafe_destination_is_rejected(url):
    assert safe_destination(url, "target.com", "Target") == (None, None, "suppressed")


def test_domain_mismatch_lowers_trust():
    url, domain, trust = safe_destination(
        "https://strange-example.biz/deal", "target.com", "Target"
    )
    assert url and domain == "strange-example.biz" and trust == "review"


def test_message_processing_is_idempotent_and_independent_of_receipts(db):
    service = GmailPromotionIngestionService(db, config())
    raw = message("m1", "20% off laundry", "Use code CLEAN20")
    assert service.process_message(raw) == (True, 1)
    assert service.process_message(raw) == (False, 0)
    assert db.query(PromotionMessage).count() == 1
    assert db.query(PromotionOffer).count() == 1


def test_duplicate_fingerprints_within_one_message_are_collapsed(db, monkeypatch):
    db.autoflush = False
    extracted = [
        ExtractedOffer(
            merchant="IRCTC Tourism Packages",
            headline=headline,
            offer_type="fixed price package",
            confidence=0.95,
        )
        for headline in ("Dwarka package", "Kashi package", "Hyderabad package")
    ]
    monkeypatch.setattr(
        PromotionExtractionService,
        "extract",
        lambda *_args, **_kwargs: extracted,
    )

    result = GmailPromotionIngestionService(db, config()).process_message(
        message("same-fingerprint", "Tour packages", "Limited seats")
    )

    assert result == (True, 1)
    assert db.query(PromotionOffer).count() == 1


def test_receipt_and_promotion_processors_have_independent_state(db):
    db.add(PurchaseReceipt(source="gmail", source_external_id="both", parse_status="parsed"))
    db.commit()
    service = GmailPromotionIngestionService(db, config())
    assert service.process_message(message("both", "20% off laundry", "")) == (True, 1)
    assert db.query(PurchaseReceipt).count() == 1
    assert db.query(PromotionMessage).count() == 1


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def json(self):
        return self.value

    def raise_for_status(self):
        return None


class FakeGmail:
    configured = True

    def __init__(self, messages):
        self.messages = messages
        self.requests = []

    def access_token(self):
        return "token"

    def get_message(self, message_id, token):
        return self.messages[message_id]

    def request(self, method, url, **kwargs):
        self.requests.append((url, kwargs.get("params", {})))
        if url.endswith("/messages"):
            return FakeResponse(
                {"messages": [{"id": key} for key in self.messages], "historyId": "22"}
            )
        return FakeResponse({"history": [], "historyId": "23"})


def test_sync_uses_promotions_label_bounded_lookback_and_excludes_spam_trash(db):
    gmail = FakeGmail(
        {
            "good": message("good", "20% off laundry", ""),
            "spam": message("spam", "80% off", "", labels=["CATEGORY_PROMOTIONS", "SPAM"]),
            "trash": message("trash", "50% off", "", labels=["CATEGORY_PROMOTIONS", "TRASH"]),
        }
    )
    result = GmailPromotionIngestionService(
        db, config(promotions_initial_lookback_days=14, promotions_max_messages_per_sync=3), gmail
    ).sync()
    params = gmail.requests[0][1]
    assert params["labelIds"] == "CATEGORY_PROMOTIONS"
    assert "newer_than:14d" in params["q"] and "-in:spam" in params["q"]
    assert result.processed == 1 and result.skipped == 2


def test_second_sync_uses_history_checkpoint(db):
    gmail = FakeGmail({"good": message("good", "20% off laundry", "")})
    service = GmailPromotionIngestionService(db, config(), gmail)
    service.sync()
    gmail.requests.clear()
    service.sync()
    assert gmail.requests[0][0].endswith("/history")


def test_capped_backfill_resumes_next_page_before_history_sync(db):
    class PagedGmail(FakeGmail):
        def request(self, method, url, **kwargs):
            params = kwargs.get("params", {})
            self.requests.append((url, params))
            if params.get("pageToken") == "page-2":
                return FakeResponse({"messages": [{"id": "c"}]})
            return FakeResponse(
                {
                    "messages": [{"id": "a"}, {"id": "b"}],
                    "nextPageToken": "page-2",
                }
            )

    gmail = PagedGmail(
        {
            "a": message("a", "10% off laundry", ""),
            "b": message("b", "20% off groceries", "", sender="Aldi <deals@aldi.com>"),
            "c": message("c", "30% off styles", "", sender="Gap <deals@gap.com>"),
        }
    )
    service = GmailPromotionIngestionService(db, config(promotions_max_messages_per_sync=2), gmail)
    assert service.sync().scanned == 2
    checkpoint = db.query(GmailSyncCheckpoint).one()
    assert checkpoint.initial_backfill_complete is False
    assert checkpoint.backfill_page_token == "page-2"
    assert service.sync().scanned == 1
    assert checkpoint.initial_backfill_complete is True
    assert checkpoint.backfill_page_token is None
    assert gmail.requests[-1][1]["pageToken"] == "page-2"


def test_incremental_history_page_does_not_advance_checkpoint_early(db):
    class PagedHistoryGmail(FakeGmail):
        def request(self, method, url, **kwargs):
            params = kwargs.get("params", {})
            self.requests.append((url, params))
            if url.endswith("/messages"):
                return FakeResponse({"messages": [], "historyId": "22"})
            if params.get("pageToken") == "history-page-2":
                return FakeResponse(
                    {
                        "history": [{"messagesAdded": [{"message": {"id": "b"}}]}],
                        "historyId": "30",
                    }
                )
            return FakeResponse(
                {
                    "history": [{"messagesAdded": [{"message": {"id": "a"}}]}],
                    "historyId": "30",
                    "nextPageToken": "history-page-2",
                }
            )

    gmail = PagedHistoryGmail(
        {
            "a": message("a", "10% off laundry", ""),
            "b": message("b", "20% off groceries", "", sender="Aldi <deals@aldi.com>"),
        }
    )
    checkpoint = GmailSyncCheckpoint(
        account_key="me:promotions",
        history_id="22",
        initial_backfill_complete=True,
    )
    db.add(checkpoint)
    db.commit()
    service = GmailPromotionIngestionService(db, config(), gmail)

    assert service.sync(max_results=1).scanned == 1
    assert checkpoint.history_id == "22"
    assert checkpoint.incremental_page_token == "history-page-2"
    assert service.sync(max_results=1).scanned == 1
    assert gmail.requests[-1][1]["pageToken"] == "history-page-2"
    assert checkpoint.incremental_page_token is None
    assert checkpoint.history_id == "30"


def test_deterministic_extraction_does_not_call_llm():
    class NeverClient:
        def post(self, *args, **kwargs):
            raise AssertionError("LLM should not be called")

    settings = config(openai_api_key="configured")
    offers = PromotionExtractionService(settings, NeverClient()).extract(
        subject="20% off laundry", body="", sender_name="Target"
    )
    assert len(offers) == 1


def test_duplicate_campaign_reminders_collapse_but_distinct_deals_remain(db):
    service = GmailPromotionIngestionService(db, config())
    service.process_message(
        message(
            "m1",
            "40% off sale styles",
            "Expires 08/31/2026",
            sender="Banana Republic <offers@bananarepublic.com>",
            thread="a",
        )
    )
    service.process_message(
        message(
            "m2",
            "Final hours: 40% off",
            "Expires 08/31/2026",
            sender="Banana Republic <offers@bananarepublic.com>",
            thread="b",
        )
    )
    service.process_message(
        message(
            "m3",
            "20% off sale styles",
            "Expires 08/31/2026",
            sender="Banana Republic <offers@bananarepublic.com>",
            thread="c",
        )
    )
    offers = list(db.execute(select(PromotionOffer)).scalars())
    assert len(offers) == 2
    assert max(len(v.source_message_ids) for v in offers) == 2


def test_expired_offers_leave_active_feed_on_rescore(db):
    service = GmailPromotionIngestionService(db, config())
    service.process_message(message("old", "20% off", "Expires 08/09/2026"))
    PromotionRankingService(db).rescore_all()
    assert db.query(PromotionOffer).one().status == "expired"


def test_expiry_before_email_received_is_rejected(db):
    service = GmailPromotionIngestionService(db, config())
    service.process_message(message("bad-date", "20% off", "Expires 08/01/2023"))
    offer = db.query(PromotionOffer).one()
    assert offer.expires_at is None
    assert offer.expiry_precision == "unknown"
    assert offer.status == "active"


def test_rescore_repairs_legacy_impossible_expiry(db):
    service = GmailPromotionIngestionService(db, config())
    service.process_message(message("repair-date", "20% off", ""))
    offer = db.query(PromotionOffer).one()
    offer.expires_at = datetime(2023, 8, 1, tzinfo=UTC)
    offer.expiry_precision = "date"
    offer.status = "expired"
    db.commit()
    PromotionRankingService(db).rescore_all()
    assert offer.expires_at is None
    assert offer.status == "active"


def test_main_feed_enforces_minimum_score_but_keeps_saved_deals(db):
    service = GmailPromotionIngestionService(db, config(promotions_min_score=50))
    service.process_message(message("low-score", "20% off", ""))
    offer = db.query(PromotionOffer).one()
    PromotionRankingService(db, config(promotions_min_score=50)).score(offer)
    db.commit()
    assert offer.score < 50
    assert list_promotions(db, status="active", limit=50) == []
    offer.saved = True
    db.commit()
    assert [value["id"] for value in list_promotions(db, status="active", limit=50)] == [offer.id]


def test_dismissed_offer_can_be_restored_to_active_feed(db):
    persist_minimum_score(db, 0)
    service = GmailPromotionIngestionService(db, config(promotions_min_score=0))
    service.process_message(message("restore", "20% off laundry", "Today only"))
    offer = db.query(PromotionOffer).one()

    dismiss(offer.id, db)
    assert offer.status == "dismissed"

    restored = restore(offer.id, db)
    assert restored["status"] == "active"
    assert [value["id"] for value in list_promotions(db, status="active", limit=50)] == [offer.id]


def test_promotion_page_reports_truthful_total_and_cursor(db):
    persist_minimum_score(db, 0)
    service = GmailPromotionIngestionService(db, config(promotions_min_score=0))
    for index in range(3):
        service.process_message(message(f"page-{index}", f"{20 + index}% off", ""))

    page = list_promotions(
        db,
        status="active",
        limit=2,
        offset=0,
        include_meta=True,
    )

    assert isinstance(page, dict)
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["has_more"] is True


def test_save_can_be_reversed_and_merchant_can_be_unmuted(db):
    service = GmailPromotionIngestionService(db, config(promotions_min_score=0))
    service.process_message(message("controls", "20% off laundry", ""))
    offer = db.query(PromotionOffer).one()

    assert save(offer.id, db)["saved"] is True
    assert unsave(offer.id, db)["saved"] is False

    feedback(
        offer.id,
        PromotionFeedbackRequest(feedback_type="mute_merchant"),
        db,
    )
    assert offer.merchant_normalized in PromotionRankingService(db).settings().muted_merchants
    unmute_merchant(offer.merchant_normalized, db)
    assert offer.merchant_normalized not in PromotionRankingService(db).settings().muted_merchants


def test_replenishment_need_beats_irrelevant_large_discount(db):
    db.add(
        HouseholdItem(
            name="Laundry detergent",
            cadence_days=30,
            last_acquired_at=datetime.now(UTC) - timedelta(days=30),
            enabled=True,
        )
    )
    db.commit()
    service = GmailPromotionIngestionService(db, config())
    service.process_message(message("need", "20% off laundry products", "Today only"))
    service.process_message(
        message("tv", "50% off televisions", "Today only", sender="Best Buy <deals@bestbuy.com>")
    )
    PromotionRankingService(db).rescore_all()
    values = list(
        db.execute(select(PromotionOffer).order_by(PromotionOffer.score.desc())).scalars()
    )
    assert values[0].primary_category == "Household"
    assert "Laundry detergent may be due soon" in values[0].score_breakdown_json["reasons"]


def test_minimum_spend_penalizes_score(db):
    service = GmailPromotionIngestionService(db, config())
    service.process_message(message("a", "$10 off", "No minimum"))
    service.process_message(
        message("b", "$10 off $100", "Minimum spend $100", sender="Store Two <deals@storetwo.com>")
    )
    offers = list(db.execute(select(PromotionOffer)).scalars())
    PromotionRankingService(db).rescore_all()
    plain = next(v for v in offers if v.minimum_spend is None)
    burden = next(v for v in offers if v.minimum_spend)
    assert burden.score_breakdown_json["minimum_spend_penalty"] < 0
    assert plain.score > burden.score


def test_muted_merchant_and_feedback_affect_scoring(db):
    service = GmailPromotionIngestionService(db, config())
    service.process_message(message("m", "25% off laundry", ""))
    offer = db.query(PromotionOffer).one()
    db.add(PromotionFeedback(promotion_offer_id=offer.id, feedback_type="less_like_this"))
    prefs = PromotionRankingService(db).settings()
    prefs.muted_merchants = [offer.merchant_normalized]
    db.commit()
    assert PromotionRankingService(db).score(offer) == 0
    assert offer.status == "suppressed"


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, value):
        self.messages.append(value)
        return True


def test_digest_groups_caps_and_prevents_duplicates(db):
    service = GmailPromotionIngestionService(
        db, config(promotions_min_score=0, promotions_digest_max_deals=2)
    )
    service.process_message(message("a", "20% off laundry", ""))
    service.process_message(message("b", "30% off groceries", "", sender="Aldi <deals@aldi.com>"))
    service.process_message(message("c", "40% off styles", "", sender="Gap <deals@gap.com>"))
    telegram = FakeTelegram()
    digest = PromotionDigestService(
        db,
        config(
            promotions_min_score=0, promotions_digest_enabled=True, promotions_digest_max_deals=2
        ),
        telegram,
    )
    first = digest.send()
    second = digest.send()
    assert first.offers_included == 2
    assert first.delivery_status == "sent"
    assert second.delivery_status == "skipped_duplicate"
    assert len(telegram.messages) == 1
    assert "BEST DEALS FOR YOU" in telegram.messages[0]


def test_digest_does_not_send_when_nothing_qualifies(db):
    telegram = FakeTelegram()
    run = PromotionDigestService(db, config(promotions_digest_enabled=True), telegram).send()
    assert run.delivery_status == "skipped_no_qualifying_offers"
    assert telegram.messages == []
    assert db.query(PromotionDigestRun).count() == 1
