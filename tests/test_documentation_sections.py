import pytest
from app.documentation.sections import sections


def test_heading_context_carries_across_nonoverlapping_sections():
    text = '# API\n## Deprecated endpoints\n' + 'A historical claim.\n' * 160
    parts = list(sections(text))
    assert ''.join(p['content'] for p in parts) == text
    assert parts[1]['heading_context'][-1]['title'] == 'Deprecated endpoints'
    assert parts[1]['start_line'] == parts[0]['end_line'] + 1
    assert parts[-1]['end_line'] == len(text.splitlines())


def test_atx_headings_start_separate_sections_and_keep_parent_context():
    text = ('# Guide\nOverview claim.\n'
            '## Purpose\nPurpose claim.\n'
            '## Operation\nOperation claim.\n'
            '### Exceptions\nException claim.\n')
    parts = list(sections(text))

    assert [part['content'] for part in parts] == [
        '# Guide\nOverview claim.\n',
        '## Purpose\nPurpose claim.\n',
        '## Operation\nOperation claim.\n',
        '### Exceptions\nException claim.\n',
    ]
    assert parts[1]['heading_context'] == [
        {'level': 1, 'title': 'Guide', 'line': 1},
        {'level': 2, 'title': 'Purpose', 'line': 3},
    ]
    assert parts[3]['heading_context'] == [
        {'level': 1, 'title': 'Guide', 'line': 1},
        {'level': 2, 'title': 'Operation', 'line': 5},
        {'level': 3, 'title': 'Exceptions', 'line': 7},
    ]
    assert ''.join(part['content'] for part in parts) == text
    assert all(parts[i]['end_line'] + 1 == parts[i + 1]['start_line'] for i in range(len(parts) - 1))


def test_code_fences_do_not_change_heading_context_and_long_lines_are_explicit():
    parts = list(sections('# Actual\n```md\n## Not a heading\n```\n' + 'line\n' * 100))
    assert parts[1]['heading_context'] == [{'level': 1, 'title': 'Actual', 'line': 1}]
    with pytest.raises(ValueError, match='markdown_line_exceeds_review_budget'):
        list(sections('x' * 6001))


def test_heading_inside_code_fence_does_not_start_a_new_section():
    text = '# Actual\n```md\n## Not a heading\n````\n## Real heading\nclaim\n'
    parts = list(sections(text))
    assert len(parts) == 2
    assert parts[0]['content'] == '# Actual\n```md\n## Not a heading\n````\n'
    assert parts[1]['content'] == '## Real heading\nclaim\n'
    assert parts[1]['heading_context'] == [
        {'level': 1, 'title': 'Actual', 'line': 1},
        {'level': 2, 'title': 'Real heading', 'line': 5},
    ]
