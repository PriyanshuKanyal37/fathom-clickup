from app.services import _match_owner_to_member_id

MEMBERS = [
    {"id": 95349668, "username": "Priyanshu Kanyal", "email": "priyanshu@theladder.ai"},
    {"id": 192213754, "username": "Shivam Chandhok", "email": "shivam@theladder.ai"},
    {"id": 95352213, "username": "Kartikey Sangal", "email": "kartikey@theladder.ai"},
    {"id": 95352214, "username": "Madhvendra Singh", "email": "madhvendra@theladder.ai"},
    {"id": 95349667, "username": "Vansh Raj", "email": "vansh@theladder.ai"},
]


def test_email_match_exact_wins():
    assert _match_owner_to_member_id("someone else", "shivam@theladder.ai", MEMBERS) == 192213754


def test_email_match_case_insensitive():
    assert _match_owner_to_member_id(None, "SHIVAM@THELADDER.AI", MEMBERS) == 192213754


def test_full_name_exact():
    assert _match_owner_to_member_id("Shivam Chandhok", None, MEMBERS) == 192213754


def test_full_name_case_insensitive_and_whitespace():
    assert _match_owner_to_member_id("  shivam   chandhok  ", None, MEMBERS) == 192213754


def test_first_name_when_unique():
    assert _match_owner_to_member_id("Shivam", None, MEMBERS) == 192213754
    assert _match_owner_to_member_id("Priyanshu", None, MEMBERS) == 95349668


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
    # "Shivam" alone is now ambiguous — could be Chandhok or Other
    assert _match_owner_to_member_id("Shivam", None, members_with_duplicate_first) is None
    # But full name still works
    assert (
        _match_owner_to_member_id("Shivam Chandhok", None, members_with_duplicate_first)
        == 192213754
    )


def test_email_preferred_over_name():
    # Name points at one person, email at another — email wins
    assert (
        _match_owner_to_member_id("Shivam Chandhok", "priyanshu@theladder.ai", MEMBERS)
        == 95349668
    )


def test_empty_member_list():
    assert _match_owner_to_member_id("Shivam", None, []) is None
