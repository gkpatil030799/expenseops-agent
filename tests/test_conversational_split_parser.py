from app.services.conversational_split_parser import parse_conversational_split


def test_parser_handles_personal():
    parsed = parse_conversational_split("mark personal")

    assert parsed.action == "personal"


def test_parser_handles_split_with_and():
    parsed = parse_conversational_split("split with Rahul and Akash")

    assert parsed.action == "split_people"
    assert parsed.participant_names == ["Rahul", "Akash"]


def test_parser_handles_split_with_comma():
    parsed = parse_conversational_split("split with Rahul, Akash")

    assert parsed.action == "split_people"
    assert parsed.participant_names == ["Rahul", "Akash"]


def test_parser_handles_group_split():
    parsed = parse_conversational_split("split in Apartment group with Rahul and Akash")

    assert parsed.action == "split_group"
    assert parsed.group_name == "Apartment group"
    assert parsed.participant_names == ["Rahul", "Akash"]
