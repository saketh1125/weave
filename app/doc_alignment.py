"""Check explicit Markdown HTTP route and normal 2xx status claims.

Only statically mounted FastAPI routes and literal success statuses are checked.
Conditional, negative, ambiguous, dynamic, and error claims remain unverified.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from http import HTTPStatus
import re


@dataclass(frozen=True)
class SourceFile:
    path: str
    content: str


_METHOD = r"GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD"
_CLAIM = re.compile(rf"\b({_METHOD})\s+(/[\w/{{}}.:-]*)", re.I)
_STATUS = re.compile(r"\b(?:status(?:\s+code)?|returns?|responds?(?:\s+with)?|HTTP)\s*[:=]?\s*`?(\d{3})\b|\b(\d{3})\s+(?:status|response)\b", re.I)
_QUALIFIED = re.compile(r"\b(?:if|when|unless|except|otherwise|error|errors|failure|failures|not|never|no|without|may|might|could)\b|\bdoes(?:n't| not)\b", re.I)


@dataclass(frozen=True)
class HttpStatusClaim:
    """A narrow route reference with its document context and source range."""

    method: str
    path: str
    expected_status: int | None
    citation: dict
    headings: tuple[str, ...]
    role: str
    condition: str | None
    reason: str | None


_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HISTORICAL = re.compile(
    r"\b(?:historical|history|legacy|deprecated|previous|planned|plan|proposal|proposed|future)\b",
    re.I,
)
_EXAMPLE = re.compile(r"\b(?:example|examples|sample|samples)\b", re.I)
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def extract_claims(markdown: list[SourceFile]) -> list[HttpStatusClaim]:
    """Extract explicit HTTP references; uncertain context is never checked as fact."""
    extracted = []
    for source in markdown:
        lines = source.content.splitlines()
        headings = []
        fence = None
        table_header = None
        for index, line in enumerate(lines):
            fence_match = _FENCE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if fence is None:
                    fence = marker
                elif (
                    marker[0] == fence[0]
                    and len(marker) >= len(fence)
                    and not line[fence_match.end():].strip()
                ):
                    fence = None
                table_header = None
                continue
            heading = _HEADING.match(line) if fence is None else None
            if heading:
                level = len(heading.group(1))
                headings = headings[:level - 1]
                headings.extend([""] * (level - 1 - len(headings)))
                headings.append(heading.group(2).strip())
                table_header = None
                continue
            context = " ".join(headings)
            if fence or _EXAMPLE.search(context):
                role = "example"
            elif _HISTORICAL.search(context):
                role = "historical"
            else:
                role = "guidance"
            condition = None
            if _QUALIFIED.search(line):
                condition = line.strip()
            elif _QUALIFIED.search(context):
                condition = context
            claims = [(m.group(1).upper(), m.group(2)) for m in _CLAIM.finditer(line)]
            statuses = [int(m.group(1) or m.group(2)) for m in _STATUS.finditer(line)]
            cells = _cells(line) if fence is None and "|" in line else []
            if cells and index + 1 < len(lines):
                separators = _cells(lines[index + 1])
                if len(cells) == len(separators) and all(
                    _TABLE_SEPARATOR.fullmatch(c) for c in separators
                ):
                    table_header = [re.sub(r"[`*_]", "", c).strip().lower() for c in cells]
                    continue
            if cells and table_header and len(cells) == len(table_header):
                method_indices = [i for i, h in enumerate(table_header) if h == "method"]
                path_indices = [
                    i for i, h in enumerate(table_header)
                    if h in ("path", "route", "endpoint")
                ]
                status_headers = (
                    "status", "status code", "http status", "response status",
                    "success status", "success status code",
                )
                status_indices = [
                    i for i, h in enumerate(table_header) if h in status_headers
                ]
                if not claims and len(method_indices) == len(path_indices) == 1:
                    method = cells[method_indices[0]].strip(" `")
                    path = cells[path_indices[0]].strip(" `")
                    if re.fullmatch(_METHOD, method, re.I) and re.fullmatch(r"/[\w/{{}}.:-]*", path):
                        claims = [(method.upper(), path)]
                if not statuses and len(status_indices) == 1:
                    status_cell = cells[status_indices[0]].strip()
                    match = re.fullmatch(r"`?(\d{3})`?", status_cell)
                    if match:
                        statuses = [int(match.group(1))]
            elif not cells or (table_header and len(cells) != len(table_header)):
                table_header = None
            for method, path in claims:
                reason = None
                if role != "guidance":
                    reason = f"{role} context"
                elif condition:
                    reason = "conditional or qualified claim"
                elif len(claims) != 1 or len(statuses) > 1:
                    reason = "ambiguous route or status claim"
                elif not statuses:
                    reason = "no supported HTTP status claim"
                elif not 200 <= statuses[0] < 300:
                    reason = "only success status claims are supported"
                expected = statuses[0] if len(statuses) == 1 else None
                extracted.append(HttpStatusClaim(
                    method=method,
                    path=path,
                    expected_status=expected,
                    citation={"file": source.path, "start_line": index + 1, "end_line": index + 1},
                    headings=tuple(h for h in headings if h),
                    role=role,
                    condition=condition,
                    reason=reason,
                ))
    return extracted


def _literal(node):
    return node.value if isinstance(node, ast.Constant) else None


def _keyword(call, name, default=None):
    return next((kw.value for kw in call.keywords if kw.arg == name), default)


def _module(path):
    path = str(path).replace("\\", "/")
    if path.endswith(".py"):
        path = path[:-3]
    return path.replace("/", ".").strip(".")


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _routes(files, handlers=None):
    trees, modules, objects, imports, mounts = {}, {}, {}, {}, []
    for source in files:
        try:
            tree = ast.parse(source.content)
        except (SyntaxError, ValueError):
            continue
        trees[source.path] = tree
        modules[_module(source.path)] = source.path
        aliases = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    aliases[alias.asname or alias.name] = node.module + "." + alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split('.')[0]] = alias.name
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                constructor = _name(node.value.func)
                if constructor in ("FastAPI", "APIRouter"):
                    prefix_node = _keyword(node.value, "prefix")
                    prefix = "" if prefix_node is None else _literal(prefix_node)
                    custom_response = _keyword(node.value, "default_response_class") is not None
                    for target in targets:
                        if isinstance(target, ast.Name):
                            objects[(source.path, target.id)] = (constructor, prefix, custom_response)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                aliases.pop(node.name, None)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        aliases.pop(target.id, None)
        imports[source.path] = aliases

    def imported(file, expression):
        name = _name(expression)
        if not name:
            return None
        head, dot, tail = name.partition(".")
        target = imports[file].get(head)
        return target + ("." + tail if dot else "") if target else None

    def status_value(file, node):
        value = _literal(node)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        name = imported(file, node)
        match = re.fullmatch(r"(?:fastapi|starlette)\.status\.HTTP_(\d{3})_[A-Z0-9_]+", name or "")
        if match:
            value = int(match.group(1))
            try:
                return value if name.endswith("_" + HTTPStatus(value).name) else None
            except ValueError:
                pass
        return None

    def resolve(file, expression):
        name = _name(expression)
        if not name:
            return None
        if (file, name) in objects:
            return (file, name)
        head, dot, tail = name.partition(".")
        target = imports[file].get(head)
        if target and dot:
            target += "." + tail
        if target:
            module, _, obj = target.rpartition(".")
            path = modules.get(module)
            if path and (path, obj) in objects:
                return (path, obj)
        return None

    for file, tree in trees.items():
        for node in tree.body:
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router" or not call.args:
                continue
            parent, child = resolve(file, call.func.value), resolve(file, call.args[0])
            prefix_node = _keyword(call, "prefix")
            prefix = "" if prefix_node is None else _literal(prefix_node)
            if parent and child:
                custom_response = _keyword(call, "default_response_class") is not None
                mounts.append((parent, child, prefix, custom_response))

    def paths(obj, seen=frozenset()):
        if obj in seen:
            return []
        kind, own, own_response = objects[obj]
        if not isinstance(own, str):
            return []
        if kind == "FastAPI":
            return [(own, own_response)]
        result = []
        for parent, child, prefix, mount_response in mounts:
            if child == obj and isinstance(prefix, str):
                for path, parent_response in paths(parent, seen | {obj}):
                    result.append((path + prefix + own, parent_response or mount_response or own_response))
        return result

    routes = {}
    for file, tree in trees.items():
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in fn.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr.upper()
                obj = resolve(file, decorator.func.value)
                if method not in _METHOD.split('|') or not obj or not decorator.args:
                    continue
                route = _literal(decorator.args[0])
                if not isinstance(route, str):
                    continue
                status_node = _keyword(decorator, "status_code")
                status = 200 if status_node is None else status_value(file, status_node)
                if not isinstance(status, int) or isinstance(status, bool):
                    status = None
                # Nonliteral returns can hide a Response override.
                for node in ast.walk(fn):
                    if isinstance(node, ast.Return) and not isinstance(node.value, (ast.Dict, ast.List, ast.Tuple, ast.Constant)):
                        status = None
                    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        if any(isinstance(t, ast.Attribute) and t.attr == "status_code" for t in targets):
                            status = None
                location = decorator
                returns = [node for node in ast.walk(fn) if isinstance(node, ast.Return)]
                if len(returns) == 1 and returns[0] in fn.body and isinstance(returns[0].value, ast.Call):
                    call = returns[0].value
                    value = status_value(file, _keyword(call, "status_code"))
                    head = (_name(call.func) or "").split(".")[0]
                    shadowed = any(isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)) and n.id == head or isinstance(n, ast.arg) and n.arg == head for n in ast.walk(fn))
                    if not shadowed and imported(file, call.func) in tuple(f"{module}.{cls}" for module in ("fastapi.responses", "starlette.responses") for cls in ("Response", "JSONResponse", "PlainTextResponse", "HTMLResponse")) and value is not None:
                        status = value
                        location = returns[0]
                for prefix, inherited_response in paths(obj):
                    custom_response = inherited_response or _keyword(decorator, "response_class") is not None
                    citation = {"file": file, "start_line": location.lineno, "end_line": location.end_lineno}
                    routes.setdefault((method, prefix + route), []).append((None if custom_response else status, citation))
                    if handlers is not None:
                        handlers.setdefault((method, prefix + route), []).append((file, fn))
    return routes


def _request_route_handlers(files):
    handlers = {}
    _routes(files, handlers)
    return handlers


def check_alignment(markdown: list[SourceFile], code: list[SourceFile]) -> list[dict]:
    """Check explicit success status claims; uncertain evidence stays unverified."""
    routes = _routes(code)
    from app.express_alignment import express_routes
    express = express_routes(code)
    for route, matches in express.items():
        routes.setdefault(route, []).extend(matches)
    from app.request_alignment import check_request_alignment
    request_results = check_request_alignment(markdown, code, conflicting_routes=set(express))
    body_locations = {(r['doc']['file'], r['doc']['start_line']) for r in request_results}
    source_lines = {source.path: source.content.splitlines() for source in markdown}
    results = []
    for claim in extract_claims(markdown):
        location = (claim.citation['file'], claim.citation['start_line'])
        # A body-field claim is not also an implicit HTTP status claim. Keep a
        # separate result only when that same line explicitly states a status.
        if location in body_locations and not _STATUS.search(source_lines[location[0]][location[1] - 1]):
            continue
        method, path = claim.method, claim.path
        matches = routes.get((method, path), [])
        reason = claim.reason
        if reason is None and len(matches) != 1:
            reason = "route or status is ambiguous"
        if reason is None and (matches[0][0] is None or not 200 <= matches[0][0] < 300):
            reason = "route or status is ambiguous"

        if reason == "no supported HTTP status claim":
            message = f"Cannot verify {method} {path}: no supported HTTP status claim was found."
        else:
            message = f"Cannot verify {method} {path}: {reason}."
        result = {
            "status": "unverified",
            "claim_type": "http_status",
            "message": message,
            "doc": claim.citation,
            "code": matches[0][1] if len(matches) == 1 else None,
            "context": {
                "headings": list(claim.headings),
                "role": claim.role,
                "condition": claim.condition,
                "reason": reason,
            },
        }
        if reason is None:
            actual = matches[0][0]
            expected = claim.expected_status
            result["status"] = "aligned" if expected == actual else "outdated"
            result["message"] = (
                f"{method} {path} documents HTTP {expected}; code specifies HTTP {actual}."
            )
        results.append(result)
    results.extend(_check_config_defaults(markdown, code))
    results.extend(request_results)
    return results


# Environment variables are strings; only explicit backticked values are compared.
_CONFIG_CLAIM = re.compile(
    r"`([A-Z][A-Z0-9_]*_[A-Z0-9_]+)`\s+(?:defaults?\s+to|default\s*(?:is|:|=))\s+`([^`]+)`",
    0,
)


def _config_sources(files):
    found = {}
    for source in files:
        try:
            tree = ast.parse(source.content)
        except (SyntaxError, ValueError):
            continue
        # Only the unshadowed standard import is recognized.
        imports = [n for n in tree.body if isinstance(n, ast.Import)
                   for a in n.names if a.name == "os" and not a.asname]
        shadowed = any(
            isinstance(n, ast.Name) and n.id == "os" and isinstance(n.ctx, (ast.Store, ast.Del))
            or isinstance(n, ast.arg) and n.arg == "os"
            for n in ast.walk(tree)
        )
        if len(imports) != 1 or shadowed:
            continue
        direct_values = [n.value for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _name(node.func) not in ("os.getenv", "os.environ.get"):
                continue
            if not node.args or not isinstance(_literal(node.args[0]), str):
                found.setdefault(None, []).append((None, None))
                continue
            name = _literal(node.args[0])
            default = node.args[1] if len(node.args) == 2 and not node.keywords else None
            if len(node.args) == 1 and len(node.keywords) == 1 and node.keywords[0].arg == "default":
                default = node.keywords[0].value
            value = _literal(default) if node in direct_values else None
            citation = {"file": source.path, "start_line": node.lineno, "end_line": node.end_lineno}
            found.setdefault(name, []).append((value if isinstance(value, str) else None, citation))
    return found


def _check_config_defaults(markdown, code):
    sources = _config_sources(code)
    results = []
    for source in markdown:
        headings, fence = [], None
        for index, line in enumerate(source.content.splitlines()):
            marker = _FENCE.match(line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                    fence = None
                continue
            heading = _HEADING.match(line) if fence is None else None
            if heading:
                level = len(heading.group(1))
                headings = headings[:level - 1]
                headings.extend([""] * (level - 1 - len(headings)))
                headings.append(heading.group(2).strip())
                continue
            context = " ".join(headings)
            role = "example" if fence or _EXAMPLE.search(context) else "historical" if _HISTORICAL.search(context) else "guidance"
            condition = line.strip() if _QUALIFIED.search(line) else context if _QUALIFIED.search(context) else None
            claims = list(_CONFIG_CLAIM.finditer(line))
            for claim in claims:
                name, expected = claim.groups()
                matches = sources.get(name, [])
                reason = (f"{role} context" if role != "guidance" else
                          "conditional or qualified claim" if condition else
                          "ambiguous configuration claim" if len(claims) != 1 or re.search(r"\b(?:or|and)\b", line[claim.end():], re.I) else
                          "configuration default is ambiguous" if sources.get(None) or len(matches) != 1 or matches[0][0] is None else None)
                result = {
                    "status": "unverified",
                    "claim_type": "environment_default",
                    "message": f"Cannot verify {name} default: {reason}.",
                    "doc": {"file": source.path, "start_line": index + 1, "end_line": index + 1},
                    "code": matches[0][1] if len(matches) == 1 else None,
                    "context": {"headings": [h for h in headings if h], "role": role, "condition": condition, "reason": reason},
                }
                if reason is None:
                    actual = matches[0][0]
                    result["status"] = "aligned" if expected == actual else "outdated"
                    result["message"] = f"{name} documents default {expected!r}; code specifies {actual!r}."
                results.append(result)
    return results
