import pytest
from typesafe_sdk import Choice, Noul, Score

from jev.__main__ import build_questions


def test_builds_each_question_type():
    questions = build_questions({
        "pick": {"type": "choice", "instructions": "Which?", "criteria": {"a": "first", "b": "second"}},
        "holds": {"type": "noul", "instructions": "Is it true?"},
        "level": {"type": "score", "instructions": "How much?", "criteria": ["low", "high"]},
    })
    assert isinstance(questions["pick"], Choice)
    assert isinstance(questions["holds"], Noul)
    assert isinstance(questions["level"], Score)


def test_unknown_type_is_rejected():
    with pytest.raises(ValueError):
        build_questions({"q": {"type": "rank", "instructions": "?"}})
