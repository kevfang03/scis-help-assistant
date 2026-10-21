import pytest

from scis_help.common.scrub import KINDS, scrub


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("my password is Hunter2", "my password is [PASSWORD]"),
        ("Password: Hunter2 thanks", "Password: [PASSWORD] thanks"),
        ("passwd=s3cret", "passwd=[PASSWORD]"),
        ("my PW was Tr0ub4dor", "my PW was [PASSWORD]"),
        ("passcode is 4242", "passcode is [PASSWORD]"),
        ("my pass is letmein", "my pass is [PASSWORD]"),
        ('my password is "correct horse battery"', "my password is [PASSWORD]"),
        ("my pw Hunter2! fails", "my pw [PASSWORD] fails"),
    ],
)
def test_password_phrases(text, expected):
    result = scrub(text)
    assert result.text == expected
    assert result.replaced["password"] == 1


@pytest.mark.parametrize(
    "text",
    ["password reset for canvas", "my password is not working", "my password is expired"],
)
def test_password_questions_keep_their_words(text):
    result = scrub(text)
    assert result.text == text
    assert not result.any


def test_email_phone_id_user():
    result = scrub("I'm abc123, ID 12345678, mail a.b@drexel.edu or call (215) 555-1234 x12")
    assert result.text == "I'm [USER], ID [ID], mail [EMAIL] or call [PHONE]"
    assert dict(result.replaced) == {"password": 0, "email": 1, "phone": 1, "id": 1, "user": 1}


def test_course_codes_with_space_or_dash_stay_but_solid_ones_are_over_scrubbed():
    assert scrub("CS 265 and CS-265").text == "CS 265 and CS-265"
    assert scrub("cs265 lab").text == "[USER] lab"  # accepted over-scrubbing


def test_order_password_before_id_and_email_before_user():
    result = scrub("password: 12345678 and abc123@drexel.edu")
    assert result.text == "password: [PASSWORD] and [EMAIL]"
    assert result.replaced["id"] == 0 and result.replaced["user"] == 0


def test_clean_text_unchanged_and_counts_zero():
    result = scrub("how do I connect to the VPN from home?")
    assert result.text == "how do I connect to the VPN from home?"
    assert not result.any
    assert tuple(result.replaced) == KINDS


def test_repr_has_counts_only():
    result = scrub("my password is Hunter2 and my id is 12345678")
    text = repr(result)
    assert "Hunter2" not in text and "12345678" not in text and "password is" not in text
    assert "password=1" in text and "id=1" in text
