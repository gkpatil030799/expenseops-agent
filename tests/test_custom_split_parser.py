from app.services.custom_split_parser import parse_custom_split_text


def test_parse_exact_amount_custom_split():
    parsed = parse_custom_split_text("split 20 with Rahul and 35 with Akash")

    assert parsed.action == "custom_split"
    assert parsed.split_mode == "exact_amounts"
    assert parsed.participant_names == ["Rahul", "Akash"]
    assert parsed.values_by_name["Rahul"] == 20
    assert parsed.values_by_name["Akash"] == 35


def test_parse_percentage_custom_split():
    parsed = parse_custom_split_text("split 60% Rahul 40% Akash")

    assert parsed.split_mode == "percentages"
    assert parsed.values_by_name["Rahul"] == 60
    assert parsed.values_by_name["Akash"] == 40


def test_parse_share_custom_split():
    parsed = parse_custom_split_text("split shares Rahul 2 Akash 1 me 1")

    assert parsed.split_mode == "shares"
    assert parsed.participant_names == ["Rahul", "Akash", "me"]
    assert parsed.values_by_name["me"] == 1


def test_parse_equal_custom_split_with_payer_excluded():
    parsed = parse_custom_split_text("split with Rahul and Akash, exclude me")

    assert parsed.split_mode == "equal"
    assert parsed.payer_included is False
    assert parsed.participant_names == ["Rahul", "Akash"]
