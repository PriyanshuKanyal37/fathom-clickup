from app.services import _match_attendees_to_members, _match_content_to_members, _match_owner_to_member_id

MEMBERS = [
    {"id": 95349668, "username": "Priyanshu Kanyal", "email": "priyanshu@theladder.ai"},
    {"id": 192213754, "username": "Shivam Chandhok", "email": "shivam@theladder.ai"},
    {"id": 95352213, "username": "Kartikey Sangal", "email": "kartikey@theladder.ai"},
    {"id": 95352214, "username": "Madhvendra Singh", "email": "madhvendra@theladder.ai"},
    {"id": 95349667, "username": "Vansh Raj", "email": "vansh@theladder.ai"},
]


def test_owner_does_not_match_by_email_only():
    assert _match_owner_to_member_id("someone else", "shivam@theladder.ai", MEMBERS) is None


def test_owner_does_not_match_by_email_without_full_name():
    assert _match_owner_to_member_id(None, "SHIVAM@THELADDER.AI", MEMBERS) is None


def test_full_name_exact():
    assert _match_owner_to_member_id("Shivam Chandhok", None, MEMBERS) == 192213754


def test_full_name_case_insensitive_and_whitespace():
    assert _match_owner_to_member_id("  shivam   chandhok  ", None, MEMBERS) == 192213754


def test_owner_does_not_match_first_name_only():
    assert _match_owner_to_member_id("Shivam", None, MEMBERS) is None
    assert _match_owner_to_member_id("Priyanshu", None, MEMBERS) is None
    assert _match_owner_to_member_id("Priyanshu Rijhwani", None, MEMBERS) is None


def test_no_match_when_not_in_clickup():
    assert _match_owner_to_member_id("Harish Ghasolia", None, MEMBERS) is None
    assert _match_owner_to_member_id("Harish", None, MEMBERS) is None


def test_no_match_empty_inputs():
    assert _match_owner_to_member_id(None, None, MEMBERS) is None
    assert _match_owner_to_member_id("", "", MEMBERS) is None
    assert _match_owner_to_member_id("   ", None, MEMBERS) is None


def test_ambiguous_first_name_returns_none():
    members_with_duplicate_first = MEMBERS + [
        {"id": 99999, "username": "Shivam Other", "email": "other@example.com"},
    ]
    assert _match_owner_to_member_id("Shivam", None, members_with_duplicate_first) is None
    assert (
        _match_owner_to_member_id("Shivam Chandhok", None, members_with_duplicate_first)
        == 192213754
    )


def test_full_name_preferred_over_email():
    assert (
        _match_owner_to_member_id("Shivam Chandhok", "priyanshu@theladder.ai", MEMBERS)
        == 192213754
    )


def test_empty_member_list():
    assert _match_owner_to_member_id("Shivam", None, []) is None


def test_attendees_match_exact_full_name_only():
    assert _match_attendees_to_members([{"name": "Vansh Raj", "email": None}], MEMBERS) == [95349667]
    assert _match_attendees_to_members([{"name": "Priyanshu Rijhwani", "email": None}], MEMBERS) == []


def test_attendees_do_not_match_by_email_or_first_name():
    assert _match_attendees_to_members([{"name": "Shivam", "email": "shivam@theladder.ai"}], MEMBERS) == []
    assert _match_attendees_to_members([{"name": None, "email": "vansh@theladder.ai"}], MEMBERS) == []


def test_content_matches_exact_full_name_only():
    assert _match_content_to_members("Vansh Raj will review this with Shivam Chandhok.", MEMBERS) == [
        192213754,
        95349667,
    ]
    assert _match_content_to_members("Priyanshu Rijhwani will review this with Shivam.", MEMBERS) == []
