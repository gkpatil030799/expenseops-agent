from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.jobs import gmail_receipts, promotions


class _SessionContext:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *_args):
        return None


def _job_context(workspace_id: int, configured: bool) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_id=workspace_id,
        settings=SimpleNamespace(configured=configured),
    )


def _patch_job_plumbing(monkeypatch, module, db, contexts) -> None:
    monkeypatch.setattr(module, "SessionLocal", lambda: _SessionContext(db))
    monkeypatch.setattr(module, "gmail_job_contexts", lambda *_args: contexts)
    monkeypatch.setattr(module, "enter_job_workspace", lambda *_args: None)
    monkeypatch.setattr(module, "leave_job_workspace", lambda: None)
    monkeypatch.setattr(module, "acquire_job_lease", lambda *_args, **_kwargs: "lease")
    monkeypatch.setattr(module, "release_job_lease", lambda *_args, **_kwargs: True)


def test_receipt_job_skips_unconsented_accounts_without_hiding_real_failures(
    monkeypatch,
):
    db = SimpleNamespace(rollback=lambda: None)
    contexts = [
        _job_context(1, False),
        _job_context(2, False),
        _job_context(3, True),
    ]
    _patch_job_plumbing(monkeypatch, gmail_receipts, db, contexts)
    calls: list[int] = []

    class FakeService:
        def __init__(self, _db, settings):
            self.configured = settings.configured

        def sync(self, *, max_results):
            calls.append(max_results)
            return SimpleNamespace(scanned=4, ingested=1, skipped=3)

    monkeypatch.setattr(gmail_receipts, "GmailReceiptService", FakeService)

    assert gmail_receipts.run(7) == {"scanned": 4, "ingested": 1, "skipped": 3}
    assert calls == [7]

    class FailingService(FakeService):
        def sync(self, *, max_results):
            raise RuntimeError("provider_failure")

    monkeypatch.setattr(gmail_receipts, "GmailReceiptService", FailingService)
    with pytest.raises(RuntimeError, match="failed_for_1_workspace"):
        gmail_receipts.run(7)


def test_promotion_sync_skips_unconsented_accounts_without_hiding_real_failures(
    monkeypatch,
):
    db = SimpleNamespace(rollback=lambda: None)
    contexts = [
        _job_context(1, False),
        _job_context(2, False),
        _job_context(3, True),
    ]
    _patch_job_plumbing(monkeypatch, promotions, db, contexts)
    calls = 0

    class FakeService:
        def __init__(self, _db, settings):
            self.configured = settings.configured

        def sync(self):
            nonlocal calls
            calls += 1
            return SimpleNamespace(scanned=4, processed=2, offers_created=1, skipped=2)

    monkeypatch.setattr(promotions, "GmailPromotionIngestionService", FakeService)

    assert promotions.run("sync") == {
        "workspaces": [
            {
                "workspace_id": 3,
                "scanned": 4,
                "processed": 2,
                "offers_created": 1,
                "skipped": 2,
            }
        ]
    }
    assert calls == 1

    class FailingService(FakeService):
        def sync(self):
            raise RuntimeError("provider_failure")

    monkeypatch.setattr(promotions, "GmailPromotionIngestionService", FailingService)
    with pytest.raises(RuntimeError, match="failed_for_1_workspace"):
        promotions.run("sync")
