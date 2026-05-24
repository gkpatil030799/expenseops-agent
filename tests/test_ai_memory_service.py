from datetime import UTC, datetime

from app.models import AIInterpretationMemory, ExpenseTransaction
from app.services.ai_memory_service import AIInterpretationMemoryService
from app.services.telegram_state_service import PendingTelegramSplit


class FakeScalars:
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        return iter(self.values)


class FakeExecute:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return FakeScalars(self.values)


class FakeDb:
    def __init__(self, memories=None):
        self.memories = memories or []
        self.added = []
        self.commits = 0

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commits += 1

    def refresh(self, item):
        item.id = getattr(item, "id", None) or 1

    def execute(self, _stmt):
        return FakeExecute(self.memories)

    def get(self, _model, memory_id):
        return next((memory for memory in self.memories if memory.id == memory_id), None)

    def delete(self, item):
        self.memories.remove(item)


def test_records_button_fallback_memory_and_caps_original_message():
    tx = ExpenseTransaction(
        id=1,
        plaid_transaction_id="tx-1",
        plaid_item_id=1,
        name="Costco",
        amount_cents=5000,
    )
    pending = PendingTelegramSplit(transaction_id=1)
    pending.remember_failed_ai_attempt("x" * 700, "low_confidence")
    pending.button_fallback_active = True

    memory = AIInterpretationMemoryService(FakeDb()).record_button_fallback_memory(
        tx=tx,
        pending=pending,
        final_action="split_equal",
        final_participants=[{"user_id": 2, "display_name": "Rahul"}],
        final_split_mode="equal",
    )

    assert memory is not None
    assert len(memory.original_message) == 500
    assert memory.final_action == "split_equal"
    assert memory.final_participants[0]["display_name"] == "Rahul"


def test_records_ai_confirmed_memory():
    tx = ExpenseTransaction(
        id=1,
        plaid_transaction_id="tx-1",
        plaid_item_id=1,
        name="Costco",
        amount_cents=5000,
    )
    pending = PendingTelegramSplit(transaction_id=1)
    pending.ai_slots = {"original_user_message": "split with Rahul"}

    memory = AIInterpretationMemoryService(FakeDb()).record_ai_interpretation_memory(
        tx=tx,
        pending=pending,
        final_action="split_equal",
        final_participants=[{"user_id": 2, "display_name": "Rahul"}],
        final_split_mode="equal",
        correction_type="ai_confirmed",
    )

    assert memory is not None
    assert memory.original_message == "split with Rahul"
    assert memory.correction_type == "ai_confirmed"
    assert pending.ai_memory_recorded is True


def test_ai_memory_does_not_record_for_button_fallback():
    tx = ExpenseTransaction(
        id=1,
        plaid_transaction_id="tx-1",
        plaid_item_id=1,
        name="Costco",
        amount_cents=5000,
    )
    pending = PendingTelegramSplit(transaction_id=1)
    pending.ai_slots = {"original_user_message": "split with Rahul"}
    pending.button_fallback_active = True

    memory = AIInterpretationMemoryService(FakeDb()).record_ai_interpretation_memory(
        tx=tx,
        pending=pending,
        final_action="split_equal",
    )

    assert memory is None


def test_public_memory_does_not_expose_user_ids():
    memory = AIInterpretationMemory(
        id=1,
        original_message="split like last time",
        failure_reason="low_confidence",
        final_action="split_equal",
        merchant="Costco",
        final_participants=[{"user_id": 222, "display_name": "Janhavi"}],
        custom_values=[{"user_id": 222, "display_name": "Janhavi", "percentage": "50"}],
        created_at=datetime.now(UTC),
    )

    public = AIInterpretationMemoryService(FakeDb()).to_public_memory(memory)

    assert public["final_participants"] == ["Janhavi"]
    assert "user_id" not in public["custom_values"][0]


def test_memory_retrieval_prioritizes_same_merchant_and_similar_message():
    same = AIInterpretationMemory(
        id=1,
        original_message="split with Janhavi",
        failure_reason="low_confidence",
        final_action="split_equal",
        merchant="Costco",
        final_participants=[{"display_name": "Janhavi"}],
        created_at=datetime.now(UTC),
    )
    other = AIInterpretationMemory(
        id=2,
        original_message="mark personal",
        failure_reason="parse_failed",
        final_action="personal",
        merchant="Uber",
        final_participants=[],
        created_at=datetime.now(UTC),
    )
    db = FakeDb([other, same])

    memories = AIInterpretationMemoryService(db).relevant_memories(
        merchant="Costco",
        message="split with Janhavi again",
    )

    assert memories[0] is same
    assert same.usage_count == 1
    assert same.last_used_at is not None


def test_memory_retrieval_scores_group_and_participant_overlap():
    memory = AIInterpretationMemory(
        id=1,
        original_message="split in apartment",
        failure_reason="none",
        final_action="split_equal",
        merchant="Other",
        final_group_name="Apartment group",
        final_participants=[{"display_name": "Rahul Shah"}],
        created_at=datetime.now(UTC),
    )
    db = FakeDb([memory])

    memories = AIInterpretationMemoryService(db).relevant_memories(
        merchant="Costco",
        message="split apartment with Rahul",
    )

    assert memories == [memory]
