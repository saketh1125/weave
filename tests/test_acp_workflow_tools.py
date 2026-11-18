import json
from pathlib import Path

import pytest

from deployment.antigravity import workflow_tools as w


def workflow():
    return w.Workflow({'a.md': 'It works.\n', 'b.md': '# Heading\n', 'code.py': 'works = True\n'}, ['a.md', 'b.md'], 'hash')


def invoke(tools, name, args):
    value, failed = tools.call_tool(name, args)
    assert not failed, value
    return value


def test_planner_partition_and_reviewer_isolation():
    job = workflow()
    assert job.planner.call_tool('submit_plan', {'assignments': [['a.md'], ['b.md']]})[1]
    invoke(job.planner, 'read_assigned_markdown', {})
    assert job.planner.call_tool('submit_plan', {'assignments': [['a.md'], ['a.md']]})[1]
    invoke(job.planner, 'submit_plan', {'assignments': [['a.md'], ['b.md']]})
    first, second = job.reviewers['reviewer-1'], job.reviewers['reviewer-2']
    assert first.call_tool('read_markdown', {'path': 'b.md', 'start_line': 1, 'end_line': 1})[1]
    assert second.steps == 0 and second.result_payload()['files'][0]['status'] == 'pending'
    assert job.planner.call_tool('submit_plan', {'assignments': [['a.md', 'b.md']]})[1]


def test_assessment_requires_assigned_document_and_read_source(monkeypatch):
    monkeypatch.setenv('ASSESSMENT_HELPER_PATH', str(Path('app/documentation/assessment.py').resolve()))
    job = workflow()
    invoke(job.planner, 'read_assigned_markdown', {})
    invoke(job.planner, 'submit_plan', {'assignments': [['a.md'], ['b.md']]})
    reviewer = job.reviewers['reviewer-1']
    invoke(reviewer, 'read_markdown', {'path': 'a.md', 'start_line': 1, 'end_line': 1})
    invoke(reviewer, 'read_source', {'path': 'code.py', 'start_line': 1, 'end_line': 1})
    assessment = {'claim': 'It works.', 'documentation_citation': {'path': 'a.md', 'start_line': 1, 'end_line': 1}, 'verdict': 'supported', 'code_citations': [{'path': 'code.py', 'start_line': 1, 'end_line': 1}], 'suggested_update': '', 'rationale': 'Implemented.'}
    invoke(reviewer, 'record_assessment', assessment)
    assert reviewer.call_tool('finish_review', {'complete': True})[1]
    invoke(reviewer, 'finish_file', {'path': 'a.md'})
    invoke(reviewer, 'finish_review', {'complete': True})
    result = reviewer.result_payload()
    assert result['finished'] and result['files'][0]['assessments'] == [assessment]


def test_claimless_requires_read_and_explicit_reason():
    job = workflow()
    invoke(job.planner, 'read_assigned_markdown', {})
    invoke(job.planner, 'submit_plan', {'assignments': [['b.md', 'a.md']]})
    reviewer = job.reviewers['reviewer-1']
    invoke(reviewer, 'read_markdown', {'path': 'b.md', 'start_line': 1, 'end_line': 1})
    invoke(reviewer, 'finish_file', {'path': 'b.md', 'no_factual_claims': True, 'reason': 'Only a heading.'})
    assert reviewer.result_payload()['files'][0]['status'] == 'completed'
    assert not reviewer.result_payload()['finished']


def test_claimless_file_is_not_blocked_by_unrelated_tool_error():
    job = workflow()
    invoke(job.planner, 'read_assigned_markdown', {})
    invoke(job.planner, 'submit_plan', {'assignments': [['a.md', 'b.md']]})
    reviewer = job.reviewers['reviewer-1']
    invoke(reviewer, 'read_assigned_markdown', {})
    _, failed = reviewer.call_tool('unknown_tool', {})
    assert failed and reviewer.errors == 1
    invoke(reviewer, 'finish_file', {'path': 'b.md', 'no_factual_claims': True, 'reason': 'Only a heading.'})
    assert reviewer.files['b.md']['status'] == 'completed'


def test_failed_assessment_attempt_blocks_claimless_completion_for_same_file(monkeypatch):
    class RejectAssessment:
        def validate_assessment(self, *_args, **_kwargs):
            raise w.base.ToolError('assessment_validation_failed')

    job = workflow()
    invoke(job.planner, 'read_assigned_markdown', {})
    invoke(job.planner, 'submit_plan', {'assignments': [['a.md', 'b.md']]})
    reviewer = job.reviewers['reviewer-1']
    invoke(reviewer, 'read_assigned_markdown', {})
    monkeypatch.setattr(reviewer, '_helper', lambda: RejectAssessment())
    assessment = {'claim': 'Claim from this file.', 'documentation_citation': {'path': 'b.md', 'start_line': 1, 'end_line': 1}}
    _, failed = reviewer.call_tool('record_assessment', assessment)
    assert failed and 'b.md' in reviewer.assessment_attempts
    _, failed = reviewer.call_tool('finish_file', {'path': 'b.md', 'no_factual_claims': True, 'reason': 'No factual claims.'})
    assert failed and reviewer.files['b.md']['status'] == 'pending'


def test_load_workflow_validates_snapshot(tmp_path):
    path = tmp_path / 'job.json'
    path.write_text(json.dumps({'snapshot': {'a.md': 'x'}, 'documentation_files': ['a.md']}))
    job = w.load_workflow(path)
    assert job.paths == ['a.md'] and job.snapshot['a.md'] == 'x' and len(job.identity) == 64
    path.write_text(json.dumps({'snapshot': {'../secret.md': 'x'}, 'documentation_files': ['../secret.md']}))
    with pytest.raises(ValueError):
        w.load_workflow(path)


def test_resume_reuses_plan_and_completed_files(tmp_path):
    path = tmp_path / 'job.json'
    path.write_text(json.dumps({'snapshot': {'a.md': 'A', 'b.md': 'B'}, 'documentation_files': ['b.md', 'a.md'],
                                'plan': {'version': 2, 'assignments': [['a.md'], ['b.md']], 'static_triage': False}, 'completed_files': ['a.md']}))
    job = w.load_workflow(path)
    assert job.planner.result_payload()['plan']['assignments'] == [['a.md'], ['b.md']]
    assert job.reviewers['reviewer-1'].result_payload()['finished']
    assert job.reviewers['reviewer-1'].result_payload()['files'][0]['assessments'] == []
    assert not job.reviewers['reviewer-2'].result_payload()['finished']
    saved_hash = job.identity
    path.write_text(json.dumps({'snapshot': {'a.md': 'A', 'b.md': 'B'}, 'documentation_files': ['a.md', 'b.md']}))
    assert w.load_workflow(path).identity == saved_hash


def test_mcp_routes_isolate_initialization():
    import http.client
    import threading
    job = workflow()
    invoke(job.planner, 'read_assigned_markdown', {})
    invoke(job.planner, 'submit_plan', {'assignments': [['a.md'], ['b.md']]})
    server = w.WorkflowServer(('127.0.0.1', 0), job)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def post(path, body):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            connection.request('POST', path, json.dumps(body), {'Host': 'workflow-tools:8080', 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
            response = connection.getresponse()
            raw = response.read()
            result = json.loads(raw) if raw else None
            connection.close()
            return response.status, result
        init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}}
        assert post('/mcp/reviewer-1', init)[0] == 200
        assert post('/mcp/reviewer-1', {'jsonrpc': '2.0', 'method': 'notifications/initialized'})[0] == 202
        listing = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}
        assert post('/mcp/reviewer-2', listing)[1]['error']['code'] == -32002
        assert post('/mcp/reviewer-1', listing)[1]['result']['tools']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_bulk_read_is_bounded_and_does_not_mark_failed_read():
    text = 'x' * (512 * 1024)
    job = w.Workflow({'a.md': text}, ['a.md'], 'hash')
    result, failed = job.planner.call_tool('read_assigned_markdown', {})
    assert failed and result['error'] == 'bulk_documentation_too_large_use_read_markdown'
    assert not job.planner.doc_reads['a.md']
    assert job.planner.call_tool('submit_plan', {'assignments': [['a.md']]})[1]


def test_bulk_read_permits_code_after_all_assigned_docs():
    job = workflow()
    invoke(job.planner, 'read_assigned_markdown', {})
    invoke(job.planner, 'submit_plan', {'assignments': [['a.md', 'b.md']]})
    reviewer = job.reviewers['reviewer-1']
    assert reviewer.call_tool('read_source', {'path': 'code.py', 'start_line': 1, 'end_line': 1})[1]
    assert len(invoke(reviewer, 'read_assigned_markdown', {})['files']) == 2
    assert not reviewer.call_tool('read_source', {'path': 'code.py', 'start_line': 1, 'end_line': 1})[1]


def test_static_triage_capacity_and_instance_reuse(monkeypatch):
    monkeypatch.setenv('ASSESSMENT_HELPER_PATH', str(Path('app/documentation/assessment.py').resolve()))
    static = {'items': [{'id': str(i), 'status': 'unverified', 'doc': {'file': 'a.md', 'start_line': 1, 'end_line': 1}, 'claim': 'It works.'} for i in range(20)] + [{'id': 'resolved', 'status': 'aligned', 'doc': {'file': 'a.md', 'start_line': 1, 'end_line': 1}, 'claim': 'It works.'}], 'triage_threshold': 20}
    job = workflow()
    job.static = static
    invoke(job.planner, 'read_assigned_markdown', {})
    invoke(job.planner, 'submit_plan', {'assignments': [['a.md'], ['b.md']], 'static_triage': True})
    reviewer = job.reviewers['reviewer-1']
    invoke(reviewer, 'read_assigned_markdown', {})
    assert reviewer.call_tool('reuse_static_result', {'id': '0'})[1]
    invoke(reviewer, 'reuse_static_result', {'id': 'resolved'})
    invoke(reviewer, 'finish_file', {'path': 'a.md'})
    assert reviewer.result_payload()['reused_static_ids'] == ['resolved']
    lane = job.triage
    assert len(lane.result_payload()['finding_ids']) == 20
    invoke(lane, 'read_assigned_markdown', {})
    assessment = {'claim': 'It works.', 'documentation_citation': {'path': 'a.md', 'start_line': 1, 'end_line': 1}, 'verdict': 'insufficient', 'code_citations': [], 'suggested_update': '', 'rationale': 'No conclusive source.'}
    invoke(lane, 'record_triage_assessment', {'finding_id': '0', 'assessment': assessment})
    assert lane.call_tool('finish_triage', {'complete': True})[1]
    assert lane.result_payload()['processed_ids'] == ['0']


def test_static_triage_rejects_small_queue_and_sixth_lane():
    paths = [f'{i}.md' for i in range(5)]
    job = w.Workflow({p: 'x' for p in paths}, paths, 'hash')
    with pytest.raises(w.base.ToolError):
        job.submit({'assignments': [paths], 'static_triage': True})
    job.static = {'items': [{'id': str(i), 'status': 'unverified', 'doc': {'file': paths[0]}, 'claim': 'x'} for i in range(20)], 'triage_threshold': 20}
    with pytest.raises(w.base.ToolError):
        job.submit({'assignments': [[p] for p in paths], 'static_triage': True})


def test_resume_triage_preserves_processed_ids_without_fabricated_evidence(tmp_path):
    static = {'version': 1, 'fingerprint': 'f', 'items': [{'id': str(i), 'status': 'unverified', 'doc': {'file': 'a.md', 'start_line': 1, 'end_line': 1}, 'claim': 'A'} for i in range(20)], 'triage_threshold': 20}
    job = {'snapshot': {'a.md': 'A'}, 'documentation_files': ['a.md'], 'static_review': static, 'plan': {'version': 2, 'assignments': [['a.md']], 'static_triage': True}, 'completed_triage_ids': ['0', '1']}
    path = tmp_path / 'job.json'
    path.write_text(json.dumps(job))
    state = w.load_workflow(path).triage.result_payload()
    assert state['processed_ids'] == ['0', '1'] and state['assessments'] == [] and not state['finished']
    job['completed_triage_ids'] = ['unknown']
    path.write_text(json.dumps(job))
    with pytest.raises(ValueError):
        w.load_workflow(path)


def test_planner_diagnostics_are_bounded_fixed_codes():
    job = workflow()
    assert job.planner.call_tool('submit_plan', {'assignments': [['a.md'], ['b.md']]})[1]
    activity = job.planner.result_payload()['activity']
    assert activity == {'steps': 1, 'errors': 1, 'last_error_code': 'read_assigned_markdown_first'}
    invoke(job.planner, 'read_assigned_markdown', {})
    assert job.planner.result_payload()['activity']['last_error_code'] == 'read_assigned_markdown_first'


def test_triage_schema_and_evidence_mismatch_feedback(monkeypatch):
    monkeypatch.setenv('ASSESSMENT_HELPER_PATH', str(Path('app/documentation/assessment.py').resolve()))
    tool = next(t for t in w.Triage.tools_schema if t['name'] == 'record_triage_assessment')
    nested = tool['inputSchema']['properties']['assessment']
    assert nested == next(t['inputSchema'] for t in w.base.TOOLS if t['name'] == 'record_assessment')
    assert set(nested['required']) == {'claim', 'documentation_citation', 'verdict', 'code_citations', 'suggested_update', 'rationale'}
    job = workflow()
    job.static = {'items': [{'id': str(i), 'status': 'unverified', 'doc': {'file': 'a.md', 'start_line': 1, 'end_line': 1}, 'claim': 'It works.'} for i in range(20)], 'triage_threshold': 20}
    invoke(job.planner, 'read_assigned_markdown', {})
    invoke(job.planner, 'submit_plan', {'assignments': [['a.md'], ['b.md']], 'static_triage': True})
    lane = job.triage
    invoke(lane, 'read_assigned_markdown', {})
    assessment = {'claim': 'It works.', 'documentation_citation': {'path': 'a.md', 'start_line': 1, 'end_line': 1}, 'verdict': 'insufficient', 'code_citations': [], 'suggested_update': '', 'rationale': 'No conclusive source.'}
    invalid = {**assessment, 'claim': 'Wrong', 'documentation_citation': {'path': 'a.md', 'start_line': 2, 'end_line': 2}}
    value, failed = lane.call_tool('record_triage_assessment', {'finding_id': '0', 'assessment': invalid})
    assert failed and value == {'error': 'finding_evidence_mismatch', 'feedback': {'claim_matches': False, 'documentation_citation_matches': False}}
    serialized = w._tool_text_result(value, failed)
    assert serialized['isError'] is True and json.loads(serialized['content'][0]['text']) == value
    assert lane.result_payload()['activity']['last_error_code'] == 'finding_evidence_mismatch'
    invoke(lane, 'record_triage_assessment', {'finding_id': '0', 'assessment': assessment})
    assert lane.call_tool('record_triage_assessment', {'finding_id': '0', 'assessment': assessment})[1]
