"""Narrow static Express route/status evidence, using syntax trees when available.

Recognizes same-file default Express imports, const app/router bindings, literal
mounts, and single-expression arrow handlers. Other handlers remain unknown.
"""
from __future__ import annotations


def express_routes(files):
    """Return route candidates in the same shape as doc_alignment._routes."""
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
        import tree_sitter_typescript
    except ImportError:
        return {}
    results = {}
    for source in files:
        path = str(source.path).lower()
        if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
            continue
        if path.endswith(".tsx"):
            language = tree_sitter_typescript.language_tsx()
        elif path.endswith(".ts"):
            language = tree_sitter_typescript.language_typescript()
        else:
            language = tree_sitter_javascript.language()
        data = source.content.encode("utf-8")
        tree = Parser(Language(language)).parse(data)
        if tree.root_node.has_error:
            continue

        def text(node):
            return data[node.start_byte:node.end_byte].decode("utf-8") if node else ""

        def field(node, name):
            return node.child_by_field_name(name) if node else None

        def literal(node):
            if not node or node.type != "string":
                return None
            raw = text(node)
            # Escapes can change runtime strings; do not interpret them here.
            return raw[1:-1] if len(raw) >= 2 and "\\" not in raw else None

        def call(node):
            if not node or node.type != "call_expression":
                return None, []
            args = field(node, "arguments")
            return field(node, "function"), list(args.named_children) if args else []

        def member(node):
            if not node or node.type != "member_expression":
                return None, None
            return text(field(node, "object")), text(field(node, "property"))

        body = tree.root_node.named_children
        imports = []
        for node in body:
            if node.type == "import_statement" and literal(field(node, "source")) == "express":
                clause = next((c for c in node.named_children if c.type == "import_clause"), None)
                if clause and len(clause.named_children) == 1 and clause.named_children[0].type == "identifier":
                    imports.append(text(clause.named_children[0]))
        if len(imports) != 1:
            continue
        express = imports[0]
        objects = {}
        for node in body:
            if node.type != "lexical_declaration" or not text(node).lstrip().startswith("const "):
                continue
            for declaration in node.named_children:
                if declaration.type != "variable_declarator":
                    continue
                name, value = field(declaration, "name"), field(declaration, "value")
                function, args = call(value)
                if not name or name.type != "identifier" or args:
                    continue
                if text(function) == express:
                    objects[text(name)] = "app"
                elif member(function) == (express, "Router"):
                    objects[text(name)] = "router"
        mounts, routes = [], []
        uncertain = False
        for node in body:
            if node.type != "expression_statement" or len(node.named_children) != 1:
                continue
            function, args = call(node.named_children[0])
            obj, method = member(function)
            if obj not in objects:
                continue
            if method == "use" and len(args) == 2:
                prefix = literal(args[0])
                child = text(args[1])
                if prefix is not None and objects.get(child) == "router" and objects[obj] == "app":
                    mounts.append((child, prefix))
                else:
                    uncertain = True
                continue
            if method not in ("get", "post", "put", "patch", "delete", "options", "head") or len(args) < 2:
                uncertain = True
                continue
            route = literal(args[0])
            if route is None or not route.startswith("/"):
                continue
            status = None
            location = node
            if len(args) == 2 and args[1].type == "arrow_function":
                handler = args[1]
                params = field(handler, "parameters")
                handler_body = field(handler, "body")
                names = list(params.named_children) if params else []
                # TypeScript wraps even untyped parameters in required_parameter.
                # Accept only a bare identifier, never defaults or destructuring.
                names = [n.named_children[0] if n.type == "required_parameter"
                         and len(n.named_children) == 1
                         and n.named_children[0].type == "identifier" else n for n in names]
                if len(names) == 2 and all(n.type == "identifier" for n in names):
                    # A single expression body or single return only, with no branches.
                    if handler_body and handler_body.type == "statement_block":
                        statements = handler_body.named_children
                        handler_body = statements[0].named_children[0] if len(statements) == 1 and statements[0].type == "return_statement" and len(statements[0].named_children) == 1 else None
                    outer_fn, outer_args = call(handler_body)
                    outer_obj, outer_method = member(outer_fn)
                    if outer_method in ("json", "send", "end") and len(outer_args) <= 1:
                        # member() returns text for the inner status call.
                        inner = field(outer_fn, "object")
                        inner_fn, inner_args = call(inner)
                        if member(inner_fn) == (text(names[1]), "status") and len(inner_args) == 1 and inner_args[0].type == "number":
                            raw = text(inner_args[0])
                            if len(raw) == 3 and raw.isascii() and raw.isdecimal():
                                status = int(raw)
                                location = inner_args[0]
            citation = {"file": source.path, "start_line": location.start_point.row + 1, "end_line": location.end_point.row + 1}
            routes.append((obj, method.upper(), route, status, citation))
        # Any nested registrations, reassignment, or other use of these objects
        # may change routing or intercept responses; do not assert a status.
        def walk(node):
            yield node
            for child in node.named_children:
                yield from walk(child)

        top_calls = {n.named_children[0].start_byte for n in body
                     if n.type == "expression_statement" and len(n.named_children) == 1}
        for node in walk(tree.root_node):
            if node.type in ("assignment_expression", "augmented_assignment_expression"):
                if text(field(node, "left")) in objects or text(field(node, "left")) == express:
                    uncertain = True
            if node.type == "call_expression":
                fn, _ = call(node)
                obj, _ = member(fn)
                if obj in objects and node.start_byte not in top_calls:
                    uncertain = True
        for obj, method, route, status, citation in routes:
            if uncertain:
                status = None
            prefixes = [""] if objects[obj] == "app" else [p for child, p in mounts if child == obj]
            for prefix in prefixes:
                results.setdefault((method, prefix + route), []).append((status, citation))
    return results
