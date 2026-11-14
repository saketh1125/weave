# Request body claim checks

Sentra compares explicit documentation claims with statically indexed FastAPI
request model declarations. A matching field default verifies that default,
not the endpoint's full behavior or the documentation as a whole.

## Supported claims

Use an explicit HTTP method, literal route, and top-level request body field:

```text
POST /items includes request body field `name`
POST /items request body field `name` is required
POST /items request body field `note` is optional
POST /items request body field `count` defaults to `10`
POST /items request body field `label` defaults to `"guest"`
```

Defaults must be backticked JSON scalar values: strings, finite numbers,
booleans or null. Expressions and collection defaults remain unverified.
Request tables require an unambiguous route heading and explicit body scope:

```markdown
## POST /items
### Request body
| Field | Required | Default |
| --- | --- | --- |
| count | no | `10` |
```

A populated Required column and Default column produce separate claims.
Required accepts yes/no or true/false. Empty cells make no claim. Generic
field tables without request body scope are not treated as request schemas.

## Code evidence and limits

Supported evidence is one statically mounted FastAPI handler with one direct
Pydantic BaseModel body parameter. Models may be declared in the same file or
imported from an indexed module with an absolute import. Literal router
prefixes are resolved through the existing route graph. Simple primitive
fields, plain defaults and simple `Field()`/`Field(default=...)` declarations
are supported. Explicitly defaulted optional primitive fields are supported;
optional annotations without defaults remain unverified because requiredness
can depend on the repository's Pydantic version.

Aliases, factories, dynamic defaults, model inheritance/configuration,
validators, custom field types/decorators, embedded or multiple bodies,
dependency-based declarations and unresolved imports remain unverified.
Ambiguous or shadowed route/model declarations also prevent verification.
No repository code is imported or executed. Indirect runtime monkeypatching
and schema changes outside the indexed declarations are not established by
these checks. An unsafe route graph conservatively blocks body checks for the
run, including cases where an unrelated module makes the graph uncertain.

Claims in examples, historical/planned sections or qualified text remain
unverified. A documented field missing from an otherwise fully resolved model
is outdated: the message says the model does not declare the field, rather
than claiming arbitrary extra input is rejected at runtime.

## Results and evaluation

Each result carries `claim_type`, document context and original source-line
citations. Request results additionally identify method, path, field,
attribute and expected value in `claim`. HTTP status and environment default
checks retain their own claim types. Model assessments remain suggestions.

The drift response's `coverage.by_claim_type` separates checked, aligned,
outdated and unverified counts. `coverage.unverified_reasons` groups abstention
reasons. Existing saved runs without this optional field remain readable.

Run the synthetic corpus without model calls:

```powershell
python scripts/evaluate_request_alignment.py --output work/request-alignment.json
```

The fixture records revisions and corrections to its authored grammar and
citations. Its small development/evaluation split is regression evidence;
both splits were available during implementation and are not an independent
estimate of real-repository accuracy. Independent labeled repositories and
broader language/framework coverage remain release work.
