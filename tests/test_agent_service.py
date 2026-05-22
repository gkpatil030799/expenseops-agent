from app.services.agent_service import match_friends, parse_friend_names_from_text


def test_parse_friend_names_from_text():
    assert parse_friend_names_from_text("split equally with Rahul and Akash") == ["rahul", "akash"]


def test_match_friends_by_first_name_and_email():
    friends = [
        {"id": 1, "first_name": "Rahul", "last_name": "Shah", "email": "rahul@example.com"},
        {"id": 2, "first_name": "Neha", "last_name": "Patel", "email": "neha@example.com"},
    ]
    matches = match_friends("shared with neha", friends)
    assert len(matches) == 1
    assert matches[0].id == 2
    assert matches[0].display_name == "Neha Patel"
