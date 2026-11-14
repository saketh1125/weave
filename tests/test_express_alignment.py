import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")

from app.doc_alignment import SourceFile
from app.express_alignment import express_routes


def test_explicit_status_and_mounted_router():
    code = SourceFile("src/app.ts", '''import express from "express";
const app = express();
const router = express.Router();
app.use("/api", router);
router.post("/jobs", (req, res) => res.status(201).json({}));
''')
    routes = express_routes([code])
    assert routes[("POST", "/api/jobs")] == [(201, {"file": "src/app.ts", "start_line": 5, "end_line": 5})]


def test_dynamic_handler_and_multiple_routes_remain_unknown_or_ambiguous():
    code = SourceFile("app.js", '''import express from "express";
const app = express();
app.get("/x", (req, res) => res.status(code).json({}));
app.get("/x", handler);
''')
    assert [s for s, _ in express_routes([code])[("GET", "/x")]] == [None, None]


def test_unsupported_or_broken_sources_never_invent_routes():
    code = [SourceFile("app.js", 'const app = fake(); app.get("/x", (req, res) => res.status(201).json({}));'),
            SourceFile("bad.ts", 'import express from "express"; const app = express(;')]
    assert express_routes(code) == {}
