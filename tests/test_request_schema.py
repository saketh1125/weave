from app.doc_alignment import SourceFile
from app.request_schema import resolve_request_schemas


def test_simple_mounted_imported_model():
    files = [SourceFile('main.py', 'from fastapi import FastAPI\nfrom api import router\napp = FastAPI()\napp.include_router(router, prefix="/v1")\n'), SourceFile('api.py', 'from fastapi import APIRouter\nfrom models import Payload\nrouter = APIRouter()\n@router.post("/items")\ndef create(body: Payload):\n    return {}\n'), SourceFile('models.py', 'from pydantic import BaseModel, Field\nclass Payload(BaseModel):\n    name: str\n    size: int = 3\n    token: str = Field()\n')]
    schema = resolve_request_schemas(files)[('POST', '/v1/items')][0]
    assert schema.reason is None
    assert schema.fields['name'].required is True
    assert schema.fields['size'].default == 3
    assert schema.fields['token'].required is True
    assert schema.fields['size'].citation['start_line'] == 4


def test_unsupported_model_and_duplicate_routes_are_conservative():
    source = SourceFile('app.py', 'from fastapi import FastAPI\nfrom pydantic import BaseModel, Field\napp = FastAPI()\nclass Payload(BaseModel):\n    value: str = Field(default_factory=str)\n@app.post("/x")\ndef one(body: Payload): return {}\n@app.post("/x")\ndef two(body: Payload): return {}\n')
    matches = resolve_request_schemas([source])[('POST', '/x')]
    assert len(matches) == 2
    assert all(item.reason for item in matches)


def test_runtime_openapi_matches_supported_fields():
    from fastapi import FastAPI
    from pydantic import BaseModel, Field
    class Payload(BaseModel):
        name: str
        size: int = 3
        enabled: bool = Field(default=True)
        note: str | None = None
    app = FastAPI()
    @app.post('/x')
    def create(body: Payload):
        return {}
    generated = app.openapi()['components']['schemas']['Payload']
    source = SourceFile('app.py', 'from fastapi import FastAPI\nfrom pydantic import BaseModel, Field\napp = FastAPI()\nclass Payload(BaseModel):\n    name: str\n    size: int = 3\n    enabled: bool = Field(default=True)\n    note: str | None = None\n@app.post("/x")\ndef create(body: Payload): return {}\n')
    schema = resolve_request_schemas([source])[('POST', '/x')][0]
    assert schema.reason is None
    assert set(schema.fields) == set(generated['properties'])
    assert {name for name, field in schema.fields.items() if field.required} == set(generated['required'])
    assert schema.fields['size'].default == generated['properties']['size']['default']
    assert schema.fields['enabled'].default == generated['properties']['enabled']['default']
    assert schema.fields['note'].default_known and schema.fields['note'].default is None
    assert Payload.model_fields['note'].default is None
    assert not Payload.model_fields['note'].is_required()


def test_shadowed_routes_and_unsupported_fields_abstain():
    template = 'from fastapi import FastAPI\nfrom pydantic import BaseModel, Field\n{prefix}\napp = FastAPI()\nclass Payload(BaseModel):\n    {field}\n@app.post("/x")\ndef create(body: Payload): return {{}}\n'
    for prefix, field in [('FastAPI = lambda: None', 'name: str'), ('', 'name: str = Field(alias="other")'), ('', 'name: Custom'), ('', 'name: float = 1e999'), ('app = None', 'name: str')]:
        source = SourceFile('app.py', template.format(prefix=prefix, field=field))
        assert resolve_request_schemas([source])[('POST', '/x')][0].reason
