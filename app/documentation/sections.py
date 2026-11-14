"""Bounded non-overlapping Markdown sections with document heading context."""
import re

MAX_SECTION_BYTES = 6000
MAX_SECTION_LINES = 80


def sections(content):
    lines = content.splitlines(keepends=True)
    if any(len(line.encode('utf-8')) > MAX_SECTION_BYTES for line in lines):
        raise ValueError('markdown_line_exceeds_review_budget')
    contexts, heading_starts, headings, fence = [], [], [], None
    for number, line in enumerate(lines, 1):
        stripped = line.lstrip()
        marker = re.match(r'(`{3,}|~{3,})', stripped)
        is_heading = False
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            heading = re.match(r'^(#{1,6})\s+(.+?)\s*#*\s*$', stripped)
            if heading:
                is_heading = True
                level = len(heading.group(1))
                headings = [h for h in headings if h['level'] < level]
                headings.append({'level': level, 'title': heading.group(2)[:300], 'line': number})
        heading_starts.append(is_heading)
        contexts.append([dict(h) for h in headings])
    start = 0
    while start < len(lines):
        end, size = start, 0
        while end < len(lines) and end - start < MAX_SECTION_LINES:
            raw = len(lines[end].encode('utf-8'))
            if end > start and (heading_starts[end] or size + raw > MAX_SECTION_BYTES):
                break
            size += raw
            end += 1
        yield {'start_line': start+1, 'end_line': end, 'content': ''.join(lines[start:end]), 'heading_context': contexts[start]}
        start = end
