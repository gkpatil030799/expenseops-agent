from app.services.entity_resolution_service import (
    EntityResolutionService,
    normalize_mention,
)


def test_normalize_removes_noise_words():
    assert normalize_mention("janahvi and") == "janahvi"
    assert normalize_mention("split between Janhavi equally") == "janhavi"


def test_resolves_typo_to_friend():
    resolver = EntityResolutionService()
    result = resolver.resolve_person_mentions(
        ["janahvi and"],
        [{"id": 7, "first_name": "Janhavi", "last_name": "Ghuge"}],
    )

    assert result.ok
    assert result.resolved[0].entity_id == 7
    assert result.resolved[0].display_name == "Janhavi Ghuge"


def test_resolves_me_to_payer():
    resolver = EntityResolutionService()
    result = resolver.resolve_person_mentions(
        ["me"],
        [],
        payer={"id": 1, "first_name": "Gunjan", "last_name": "Patil"},
    )

    assert result.ok
    assert result.resolved[0].entity_id == 1
    assert result.resolved[0].source == "payer"


def test_multiple_yash_matches_are_ambiguous():
    resolver = EntityResolutionService()
    result = resolver.resolve_person_mentions(
        ["yash"],
        [
            {"id": 7, "first_name": "Yash", "last_name": "Bhatkhande"},
            {"id": 8, "first_name": "Yash", "last_name": "Patel"},
        ],
    )

    assert result.resolved == []
    assert result.unresolved == []
    assert len(result.ambiguous) == 1
    assert [candidate.entity_id for candidate in result.ambiguous[0].candidates] == [7, 8]


def test_group_members_are_prioritized_over_non_group_friends():
    resolver = EntityResolutionService()
    result = resolver.resolve_people_within_group(
        ["yash"],
        [{"id": 7, "first_name": "Yash", "last_name": "Bhatkhande"}],
        all_friends=[
            {"id": 7, "first_name": "Yash", "last_name": "Bhatkhande"},
            {"id": 8, "first_name": "Yash", "last_name": "Patel"},
        ],
    )

    assert result.ok
    assert result.resolved[0].entity_id == 7
    assert result.resolved[0].source == "group_member"


def test_unresolved_mentions_are_preserved():
    resolver = EntityResolutionService()
    result = resolver.resolve_person_mentions(
        ["unknown person"],
        [{"id": 7, "first_name": "Janhavi", "last_name": "Ghuge"}],
    )

    assert result.resolved == []
    assert result.ambiguous == []
    assert result.unresolved == ["unknown person"]


def test_group_resolution_returns_group_match():
    resolver = EntityResolutionService()
    result = resolver.resolve_group_mentions(
        ["sugar monkey"],
        [{"id": 44, "name": "Sugar Monkeys"}],
    )

    assert result.ok
    assert result.resolved[0].entity_id == 44
    assert result.resolved[0].display_name == "Sugar Monkeys"
