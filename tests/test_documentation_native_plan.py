import pytest

from app.documentation.native_plan import validate_plan


def test_native_plan_accepts_exact_file_coverage():
    assert validate_plan({'assignments': [['a.md', 'b.md'], ['c.md']]}, ['a.md', 'b.md', 'c.md']) == {
        'version': 2, 'assignments': [['a.md', 'b.md'], ['c.md']], 'static_triage': False}


@pytest.mark.parametrize('value, paths', [
    ({'assignments': [['a.md'], ['a.md']]}, ['a.md', 'b.md']),
    ({'assignments': [['a.md']]}, ['a.md', 'b.md']),
    ({'assignments': [['a.md', 'x.md']]}, ['a.md', 'b.md']),
    ({'assignments': [['a.md'], []]}, ['a.md', 'b.md']),
    ({'assignments': [['a.md']], 'extra': True}, ['a.md']),
    ({'assignments': [['a.md']]}, ['a.md', 'a.md']),
    ({'assignments': []}, []),
    ({'assignments': [[str(i)] for i in range(6)]}, [str(i) for i in range(6)]),
    ({'assignments': [['a.md', 1]]}, ['a.md', 'b.md']),
])
def test_native_plan_rejects_invalid_coverage(value, paths):
    with pytest.raises(ValueError, match='invalid_plan'):
        validate_plan(value, paths)


def test_native_plan_triage_requires_queue_and_uses_a_lane():
    assert validate_plan({'assignments': [['a.md']], 'static_triage': True}, ['a.md'], 20)['static_triage']
    with pytest.raises(ValueError):
        validate_plan({'assignments': [['a.md']], 'static_triage': True}, ['a.md'], 19)
    with pytest.raises(ValueError):
        validate_plan({'assignments': [[str(i)] for i in range(5)], 'static_triage': True}, [str(i) for i in range(5)], 20)
