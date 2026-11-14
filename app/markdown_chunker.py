from __future__ import annotations

import hashlib
import re
from bisect import bisect_right

from app.chunker import Chunk

# Bytes are a conservative proxy for tokens, including non-ASCII text.
_MAX_BYTES = 1400
_OVERLAP_BYTES = 240
_HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$')
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')


def chunk_markdown(source: bytes, file_path: str) -> list[Chunk]:
    """Split UTF-8 Markdown into source-exact, section-aware bounded chunks."""
    text = source.decode('utf-8')
    lines = text.splitlines(keepends=True)
    sections: list[tuple[int, int, tuple[str, ...]]] = []
    line_offsets = [0]
    paragraph_ends: list[int] = []
    block_ends: list[int] = []
    headings: list[str] = []
    start = 0
    offset = 0
    fence: str | None = None
    for line in lines:
        stripped = line.rstrip('\r\n')
        marker = _FENCE.match(stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not stripped[marker.end():].strip():
                fence = None
        match = _HEADING.match(stripped) if fence is None and not marker else None
        if match:
            if offset > start:
                sections.append((start, offset, tuple(headings)))
            level = len(match.group(1))
            headings = headings[:level - 1]
            headings.append(match.group(2).strip())
            start = offset
        offset += len(line.encode('utf-8'))
        line_offsets.append(offset)
        if fence is None:
            block_ends.append(offset)
            if not stripped.strip():
                paragraph_ends.append(offset)
    if offset > start:
        sections.append((start, offset, tuple(headings)))

    chunks: list[Chunk] = []
    for section_start, section_end, hierarchy in sections:
        pos = section_start
        while pos < section_end:
            end = min(pos + _MAX_BYTES, section_end)
            # Never cut a UTF-8 code point. Prefer a paragraph or line break.
            while end < section_end and source[end] & 0xC0 == 0x80:
                end -= 1
            if end < section_end:
                floor = pos + _MAX_BYTES // 2
                for boundaries, minimum in (
                    (paragraph_ends, floor),
                    (block_ends, pos + _OVERLAP_BYTES + 1),
                    (line_offsets, floor),
                ):
                    index = bisect_right(boundaries, end)
                    if index and boundaries[index - 1] >= minimum:
                        end = boundaries[index - 1]
                        break
            content_bytes = source[pos:end]
            content = content_bytes.decode('utf-8')
            if content.strip():
                name = hierarchy[-1] if hierarchy else '<document>'
                chunks.append(Chunk(
                    file_path=file_path, language='markdown', kind='markdown',
                    name=name, qualified_name=' > '.join(hierarchy) or name,
                    start_line=bisect_right(line_offsets, pos),
                    end_line=bisect_right(line_offsets, end - 1),
                    byte_start=pos, byte_end=end, content=content,
                    content_hash=hashlib.sha256(content_bytes).hexdigest(),
                ))
            if end == section_end:
                break
            if end - pos <= _OVERLAP_BYTES:
                pos = end
                continue
            next_pos = end - _OVERLAP_BYTES
            while next_pos < end and source[next_pos] & 0xC0 == 0x80:
                next_pos += 1
            # Start overlap at a line boundary when practical.
            next_line = line_offsets[bisect_right(line_offsets, next_pos)]
            if next_line < end:
                next_pos = next_line
            pos = next_pos
    return chunks
