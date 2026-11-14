"""Conservative extraction of explicit request body field claims from Markdown."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re


@dataclass(frozen=True)
class RequestFieldClaim:
    method: str
    path: str
    field: str
    attribute: str
    expected: object
    citation: dict
    headings: tuple[str, ...]
    role: str
    condition: str | None
    reason: str | None


_METHOD = r"GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD"
_ROUTE = re.compile(rf"\b({_METHOD})\s+(`?)(/[\w/{{}}.:-]*)(?:\2)", re.I)
_ROUTE_HEADING = re.compile(rf"^({_METHOD})\s+(/[\w/{{}}.:-]*)$", re.I)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_FIELD = r"`?([A-Za-z_][\w.-]*)`?"
_QUALIFIED = re.compile(r"\b(?:if|when|unless|except|otherwise|may|might|could|typically|usually|sometimes|not|never|no|without|errors?)\b", re.I)
_HISTORICAL = re.compile(r"\b(?:historical|history|legacy|deprecated|previous|planned|plan|proposal|proposed|future)\b", re.I)
_EXAMPLE = re.compile(r"\b(?:example|examples|sample|samples)\b", re.I)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _scalar(value: str) -> tuple[object, str | None]:
    value = value.strip()
    if not (len(value) >= 2 and value[0] == value[-1] == "`"):
        return None, "unsupported default value"
    try:
        parsed = json.loads(value[1:-1])
    except (ValueError, TypeError):
        return None, "unsupported default value"
    if parsed is None or isinstance(parsed, (str, int, bool)):
        return parsed, None
    if isinstance(parsed, float) and math.isfinite(parsed):
        return parsed, None
    return None, "unsupported default value"


def _role(headings: list[str], fenced: bool) -> str:
    context = " ".join(headings)
    if fenced or _EXAMPLE.search(context):
        return "example"
    if _HISTORICAL.search(context):
        return "historical"
    return "guidance"


def extract_request_claims(markdown) -> list[RequestFieldClaim]:
    """Extract explicit claims from a markdown string or duck-typed source file."""
    if isinstance(markdown, str):
        sources = [("<markdown>", markdown)]
    else:
        sources = [(getattr(source, "path", "<markdown>"), source.content) for source in markdown]
    results: list[RequestFieldClaim] = []
    for filename, content in sources:
        headings: list[str] = []
        fence = None
        table_header = None
        table_route = None
        lines = content.splitlines()
        for number, line in enumerate(lines, 1):
            marker = _FENCE.match(line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
                table_header = None
                continue
            heading = _HEADING.match(line) if fence is None else None
            if heading:
                level = len(heading.group(1))
                headings = headings[:level - 1]
                headings.extend([""] * max(0, level - len(headings)))
                headings[level - 1] = heading.group(2).strip()
                table_header = None
                table_route = None
                continue
            cells = _cells(line) if "|" in line else []
            if not fence and cells and number < len(lines) + 1:
                next_line = lines[number] if number < len(lines) else ""
                sep = _cells(next_line) if "|" in next_line else []
                if len(cells) == len(sep) and all(re.fullmatch(r":?-{3,}:?", c) for c in sep):
                    table_header = [re.sub(r"[`*_]", "", c).strip().lower() for c in cells]
                    route_matches = [_ROUTE_HEADING.fullmatch(h) for h in headings if h]
                    route_matches = [m for m in route_matches if m]
                    context = " ".join(headings)
                    body_scope = bool(re.search(r"\brequest\s+body\b", context, re.I)) or "body field" in table_header
                    table_route = ((route_matches[-1].group(1).upper(), route_matches[-1].group(2))
                                   if len(route_matches) == 1 and body_scope else None)
                    continue

            role = _role([h for h in headings if h], fence is not None)
            context = " ".join(h for h in headings if h)
            condition = line.strip() if _QUALIFIED.search(line) else context if _QUALIFIED.search(context) else None
            found: list[tuple[str, str, str, str, object, str | None]] = []
            route_matches = list(_ROUTE.finditer(line))
            if route_matches:
                for route_match in route_matches:
                    method, path = route_match.group(1).upper(), route_match.group(3)
                    tail = line[route_match.end():]
                    if re.search(r"\brequest\s+body\b", tail, re.I):
                        patterns = (
                            ("required", re.compile(rf"request\s+body\s+field\s+{_FIELD}\s+is\s+(required|optional)", re.I)),
                            ("default", re.compile(rf"request\s+body\s+field\s+{_FIELD}\s+defaults\s+to\s+(`[^`]*`|\S+)", re.I)),
                            ("presence", re.compile(rf"includes\s+request\s+body\s+field\s+{_FIELD}", re.I)),
                        )
                        for attribute, pattern in patterns:
                            for match in pattern.finditer(line):
                                field = match.group(1).strip("`")
                                if attribute == "required":
                                    expected, problem = match.group(2).lower() == "required", None
                                    if re.search(r"\b(?:or|and)\b", line[match.end():], re.I):
                                        problem = "ambiguous required value"
                                elif attribute == "default":
                                    expected, problem = _scalar(match.group(2))
                                    if re.search(r"\b(?:or|and)\b", line[match.end():], re.I):
                                        problem = "ambiguous default value"
                                else:
                                    expected, problem = True, None
                                found.append((method, path, field, attribute, expected, problem))

            if not fence and cells and table_header and len(cells) == len(table_header) and table_route:
                indices = {key: [i for i, h in enumerate(table_header) if h == key]
                           for key in ("field", "required", "default")}
                if len(indices["field"]) == len(indices["required"]) == len(indices["default"]) == 1:
                    data_columns = set(indices["field"] + indices["required"] + indices["default"])
                    if "body field" in table_header:
                        data_columns.update(i for i, h in enumerate(table_header) if h == "body field")
                    commentary = " ".join(cell for i, cell in enumerate(cells) if i not in data_columns)
                    condition = commentary if _QUALIFIED.search(commentary) else (
                        context if _QUALIFIED.search(context) else None
                    )
                    field = cells[indices["field"][0]].strip(" `")
                    if re.fullmatch(r"[A-Za-z_][\w.-]*", field):
                        req = cells[indices["required"][0]].strip().lower()
                        if req:
                            expected = req in ("true", "yes")
                            issue = None if req in ("true", "false", "yes", "no") else "unsupported required value"
                            found.append((*table_route, field, "required", expected if issue is None else None, issue))
                        default_cell = cells[indices["default"][0]].strip()
                        if default_cell:
                            expected, issue = _scalar(default_cell)
                            found.append((*table_route, field, "default", expected, issue))

            for method, path, field, attribute, expected, issue in found:
                reason = (f"{role} context" if role != "guidance" else
                          "conditional or qualified claim" if condition else
                          "ambiguous route or field claim" if len(route_matches) > 1 or any(
                              f[2] != field or (f[2] == field and f[3] == attribute)
                              for f in found if f != (method, path, field, attribute, expected, issue)
                          ) else issue)
                results.append(RequestFieldClaim(
                    method=method, path=path, field=field, attribute=attribute,
                    expected=expected, citation={"file": filename, "start_line": number, "end_line": number},
                    headings=tuple(h for h in headings if h), role=role,
                    condition=condition, reason=reason,
                ))
            if not cells or (table_header and len(cells) != len(table_header)):
                table_header = None
                table_route = None
    return results
