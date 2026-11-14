"""Conservative static evidence for top-level FastAPI request models."""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from app.doc_alignment import _module, _name, _request_route_handlers


@dataclass(frozen=True)
class BodyField:
    required: bool | None
    default: object
    default_known: bool
    citation: dict
    reason: str | None


@dataclass(frozen=True)
class BodySchema:
    fields: dict[str, BodyField]
    reason: str | None
    citation: dict


def _citation(file, node):
    return {"file": file, "start_line": node.lineno, "end_line": node.end_lineno}


def _scalar(node):
    if isinstance(node, ast.Constant) and (node.value is None or type(node.value) in (str, int, float, bool)) and not (type(node.value) is float and not math.isfinite(node.value)):
        return True, node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        known, value = _scalar(node.operand)
        if known and type(value) in (int, float):
            return True, -value if isinstance(node.op, ast.USub) else value
    return False, None


def resolve_request_schemas(files):
    """Return mounted route schemas without importing or executing source code."""
    files = [source for source in files if source.path.lower().endswith('.py')]
    trees, modules, bindings = {}, {}, {}
    ambiguous_modules = set()
    for source in files:
        try:
            tree = ast.parse(source.content)
        except (SyntaxError, ValueError):
            continue
        trees[source.path] = tree
        module = _module(source.path)
        normalized = module.removesuffix('.__init__')
        if normalized in modules or module in modules:
            ambiguous_modules.add(normalized)
        modules[normalized] = source.path
        modules[module] = source.path
        names = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    names.setdefault(alias.asname or alias.name, []).append(node.module + '.' + alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.setdefault(alias.asname or alias.name.split('.')[0], []).append(alias.name)
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.setdefault(node.name, []).append(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.setdefault(target.id, []).append(None)
        bindings[source.path] = names

    def lookup(file, node):
        name = _name(node)
        if not name:
            return None
        head, dot, tail = name.partition('.')
        values = bindings[file].get(head, [])
        if len(values) != 1:
            return None
        value = values[0]
        if isinstance(value, str):
            return value + ('.' + tail if dot else '')
        return value if not dot else None

    def model(file, annotation):
        value = lookup(file, annotation)
        if isinstance(value, ast.ClassDef):
            return file, value
        if isinstance(value, str):
            module, _, name = value.rpartition('.')
            target = modules.get(module)
            if target and module not in ambiguous_modules:
                values = bindings[target].get(name, [])
                if len(values) == 1 and isinstance(values[0], ast.ClassDef):
                    return target, values[0]
        return None

    def schema(file, fn):
        cite = _citation(file, fn)
        unknown = lambda why, location=cite: BodySchema({}, why, location)
        if any(not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and d.func.attr.upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'))
               for d in fn.decorator_list):
            return unknown('unsupported handler decorator')
        if fn.args.vararg or fn.args.kwarg or fn.args.posonlyargs:
            return unknown('unsupported handler parameters')
        args = fn.args.args + fn.args.kwonlyargs
        defaults = [None] * (len(fn.args.args) - len(fn.args.defaults)) + list(fn.args.defaults) + list(fn.args.kw_defaults)
        candidates = []
        for arg, default in zip(args, defaults):
            if arg.arg in ('self', 'cls'):
                return unknown('unsupported handler parameters')
            resolved = model(file, arg.annotation) if arg.annotation else None
            if resolved:
                candidates.append((resolved, default))
            elif default is not None and isinstance(default, ast.Call):
                return unknown('unsupported parameter declaration')
            elif arg.annotation is None or _name(arg.annotation) not in ('str', 'int', 'float', 'bool'):
                return unknown('ambiguous handler parameter')
        if len(candidates) != 1:
            return unknown('body model is ambiguous')
        (model_file, cls), default = candidates[0]
        model_cite = _citation(model_file, cls)
        # Local bindings can replace an imported model or its constructor at runtime.
        annotation_names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
        annotation_names.update(a.arg for a in ast.walk(fn) if isinstance(a, ast.arg))
        model_head = (_name(next(arg.annotation for arg in args if model(file, arg.annotation))) or '').split('.')[0]
        if model_head in annotation_names:
            return unknown('shadowed model reference')
        if default is not None:
            return unknown('unsupported body parameter default')
        if len(cls.bases) != 1 or lookup(model_file, cls.bases[0]) != 'pydantic.BaseModel' or cls.keywords or cls.decorator_list:
            return unknown('unsupported model inheritance or decoration', model_cite)
        # Mutations after class definition can change the runtime schema.
        for node in ast.walk(trees[model_file]):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and _name(node.value) == cls.name:
                return unknown('model mutation', model_cite)
        fields = {}
        for node in cls.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if isinstance(node, ast.Pass):
                continue
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name) or node.target.id.startswith('_'):
                return unknown('unsupported model declaration', _citation(model_file, node))
            def primitive(annotation):
                return isinstance(annotation, ast.Name) and annotation.id in ('str', 'int', 'float', 'bool') and not bindings[model_file].get(annotation.id)

            annotation = node.annotation
            optional = (isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr)
                        and ((primitive(annotation.left) and isinstance(annotation.right, ast.Constant) and annotation.right.value is None)
                             or (primitive(annotation.right) and isinstance(annotation.left, ast.Constant) and annotation.left.value is None)))
            optional = optional or (isinstance(annotation, ast.Subscript)
                                    and lookup(model_file, annotation.value) == 'typing.Optional'
                                    and primitive(annotation.slice))
            if not primitive(annotation) and not (optional and node.value is not None):
                return unknown('unsupported field annotation', _citation(model_file, node))
            field_name = node.target.id
            value = node.value
            required, known, default_value = value is None, False, None
            if value is not None:
                if isinstance(value, ast.Call) and lookup(model_file, value.func) == 'pydantic.Field':
                    if len(value.args) > 1 or any(kw.arg not in ('default', 'alias') for kw in value.keywords):
                        return unknown('unsupported Field declaration', _citation(model_file, node))
                    kw = {item.arg: item.value for item in value.keywords}
                    if len(kw) != len(value.keywords) or (value.args and 'default' in kw):
                        return unknown('ambiguous Field declaration', _citation(model_file, node))
                    if 'alias' in kw:
                        return unknown('unsupported field alias', _citation(model_file, node))
                    value = value.args[0] if value.args else kw.get('default')
                    required = value is None or isinstance(value, ast.Constant) and value.value is Ellipsis
                else:
                    required = isinstance(value, ast.Constant) and value.value is Ellipsis
                if not required:
                    known, default_value = _scalar(value)
                    if not known:
                        return unknown('dynamic field default', _citation(model_file, node))
            if optional and required:
                return unknown('optional field without default', _citation(model_file, node))
            if field_name in fields:
                return unknown('duplicate input field', _citation(model_file, node))
            fields[field_name] = BodyField(required, default_value, known, _citation(model_file, node), None)
        return BodySchema(fields, None, model_cite)

    # The shared route graph recognizes bare constructors.  Require each such
    # constructor and its assigned route object to have a unique real binding.
    unsafe_graph = bool(ambiguous_modules)
    for file, tree in trees.items():
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call) and _name(node.value.func) in ('FastAPI', 'APIRouter'):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if lookup(file, node.value.func) != 'fastapi.' + _name(node.value.func) or any(not isinstance(t, ast.Name) or len(bindings[file].get(t.id, [])) != 1 for t in targets):
                    unsafe_graph = True
                protected = {_name(node.value.func)} | {t.id for t in targets if isinstance(t, ast.Name)}
                for name in protected:
                    stores = sum(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(tree))
                    expected = 0 if name == _name(node.value.func) else 1
                    if stores != expected:
                        unsafe_graph = True
    result = {}
    for route, handlers in _request_route_handlers(files).items():
        result[route] = [BodySchema({}, 'ambiguous route graph', _citation(file, fn)) if unsafe_graph else schema(file, fn) for file, fn in handlers]
    return result
