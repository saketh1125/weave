"""Shared evidence validation for API and isolated agent documentation reviews."""
from pathlib import PurePosixPath

class AssessmentValidationError(ValueError):
    def __init__(self, reason, **details):
        self.feedback = {"error": "invalid_tool_request_or_result", "reason": reason, **details}

def valid_citation(c, ranges):
    if not isinstance(c, dict) or set(c) != {"path", "start_line", "end_line"}:
        return False
    p, a, b = c.get("path"), c.get("start_line"), c.get("end_line")
    return isinstance(p, str) and type(a) is int and type(b) is int and 1 <= a <= b and any(r["path"] == p and r["start_line"] <= a and b <= r["end_line"] for r in ranges)


def validate_assessment(args, section, read_ranges, assessment_count=0):
    if not isinstance(args, dict):
        raise AssessmentValidationError("invalid_assessment_schema")
    required = {"claim", "documentation_citation", "verdict", "code_citations", "suggested_update", "rationale"}
    if set(args) != required or any(not isinstance(args[k], str) or not args[k].strip() for k in ("claim", "rationale")) or not isinstance(args["suggested_update"], str) or args["verdict"] not in ("supported", "conflicting", "insufficient") or not isinstance(args["code_citations"], list): raise AssessmentValidationError("invalid_assessment_schema")
    if any(len(args[k]) > 2000 for k in ("claim", "rationale", "suggested_update")) or len(args["code_citations"]) > 10 or assessment_count >= 30: raise AssessmentValidationError("invalid_assessment_schema")
    doc_range = [{"path": section["path"], "start_line": section["start_line"], "end_line": section["end_line"]}]
    if not valid_citation(args["documentation_citation"], doc_range):
        raise AssessmentValidationError("documentation_citation_outside_assignment")
    if any(not valid_citation(c, read_ranges) for c in args["code_citations"]) or (args["verdict"] != "insufficient" and not args["code_citations"]):
        ranges = [{**r, "path": r["path"][:1000]} for r in read_ranges[-20:]]
        raise AssessmentValidationError("unread_source_citation", hint="Call read_source for the exact needed path and range before citing it; or use insufficient with no code citations if evidence is unavailable.", read_ranges=ranges)
    citation = args["documentation_citation"]
    lines = section["content"].splitlines()
    cited = " ".join(lines[citation["start_line"] - section["start_line"]:citation["end_line"] - section["start_line"] + 1])
    if " ".join(args["claim"].split()) not in " ".join(cited.split()):
        snippet = cited.encode("utf-8")[:4000].decode("utf-8", errors="ignore")
        raise AssessmentValidationError("claim_not_verbatim", hint="Copy an exact substring from cited documentation, retaining Markdown formatting; do not paraphrase", cited_documentation=snippet)
    if any((c["path"].lower().endswith((".md", ".markdown", ".mdx", ".rst")) or (c["path"].lower().endswith(".txt") and PurePosixPath(c["path"]).name != "requirements.txt")) or c["path"] == section["path"] for c in args["code_citations"]): raise ValueError
