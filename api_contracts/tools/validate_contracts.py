"""Offline contract validation after installing tools/requirements.txt."""
import copy
import json
import re
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]
checker = FormatChecker()
fixtures = json.loads((ROOT / 'examples/schema-fixtures.json').read_text())
documents = {}
operations = set()
public_routes = set()
counts = dict(openapi=0, operations=0, fixtures=0, examples=0, events=0, negative_cases=0)

def walk(value):
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)

def resolve(doc, reference):
    assert reference.startswith('#/'), f'Unexpected external reference: {reference}'
    value = doc
    for key in reference[2:].split('/'):
        value = value[key.replace('~1', '/').replace('~0', '~')]
    return value

def schema_validator(doc, schema):
    return Draft202012Validator(dict(schema, components=doc['components']), format_checker=checker)

for path in sorted((ROOT / 'openapi').glob('*.json')):
    doc = json.loads(path.read_text())
    validate(doc)
    documents[path.stem] = doc
    counts['openapi'] += 1
    for node in walk(doc):
        if isinstance(node, dict) and '$ref' in node:
            resolve(doc, node['$ref'])
    for name, schema in doc['components']['schemas'].items():
        Draft202012Validator.check_schema(schema)
        schema_validator(doc, schema).validate(fixtures[name])
        counts['fixtures'] += 1
    for url, item in doc['paths'].items():
        for method, operation in item.items():
            if method not in ['get', 'post', 'patch', 'delete', 'put', 'options', 'head']:
                continue
            assert operation['operationId'] not in operations, operation['operationId']
            operations.add(operation['operationId'])
            counts['operations'] += 1
            if url.startswith('/api/'):
                key = (method, url)
                assert key not in public_routes, key
                public_routes.add(key)
            params = [resolve(doc, p['$ref']) if '$ref' in p else p for p in operation.get('parameters', [])]
            assert set(re.findall(r'{(\w+)}', url)) == {p['name'] for p in params if p['in'] == 'path'}
            if url.startswith('/internal/'):
                assert doc['security'] == [{'serviceKey': [], 'sessionBearer': []}]
            if 'requestBody' in operation:
                content = operation['requestBody']['content']['application/json']
                assert 'example' in content
                schema_validator(doc, content['schema']).validate(content['example'])
                counts['examples'] += 1
            for code, response in operation['responses'].items():
                if '$ref' in response:
                    response = resolve(doc, response['$ref'])
                if str(code) == '204':
                    assert 'content' not in response
                for content in response.get('content', {}).values():
                    if str(code).startswith('2'):
                        assert 'example' in content, (url, method, code)
                    if 'example' in content:
                        schema_validator(doc, content['schema']).validate(content['example'])
                        counts['examples'] += 1
                if 'ETag' in response.get('headers', {}) and 'content' in response:
                    body = response['content']['application/json']['example']
                    assert response['headers']['ETag']['example'] == f'"{body["version"]}"'

es = json.loads((ROOT / 'events/event.schema.json').read_text())
Draft202012Validator.check_schema(es)
ev = Draft202012Validator(es, format_checker=checker)
events = json.loads((ROOT / 'examples/events.json').read_text())
assert len(events) == 5
assert len({e['id'] for e in events}) == 5
assert {e['type'] for e in events} == {v['properties']['type']['const'] for v in es['$defs'].values()}
for event in events:
    ev.validate(event)
    counts['events'] += 1
    if 'recipient_id' in event['data']:
        assert event['data']['actor']['id'] != event['data']['recipient_id']

# Schema negative tests. Business-semantic checks are separate scenarios.
taskdoc = documents['task.openapi']
idoc = documents['identity-workspace.openapi']
def invalid(doc, name, value):
    assert list(schema_validator(doc, {'$ref': '#/components/schemas/' + name}).iter_errors(value)), (name, value)
    counts['negative_cases'] += 1
invalid(taskdoc, 'TaskCreate', dict(fixtures['TaskCreate'], priority='High'))
invalid(taskdoc, 'TaskCreate', dict(fixtures['TaskCreate'], status='Done'))
invalid(taskdoc, 'TaskCreate', dict(fixtures['TaskCreate'], title='x' * 201))
invalid(taskdoc, 'TaskCreate', dict(fixtures['TaskCreate'], assignee_id=[fixtures['UserSummary']['id']]))
invalid(taskdoc, 'TaskPatch', {})
invalid(taskdoc, 'TaskPatch', {'title': None})
invalid(taskdoc, 'TaskPatch', {'start_date': '2026-02-30'})
invalid(taskdoc, 'CommentCreate', {'body': 'x', 'mentions': [fixtures['MentionInput']] * 51})
invalid(idoc, 'NameWorkspace', {'name': 'x' * 101})
invalid(idoc, 'InviteCreate', {'email': 'not-an-email'})
for change in [{'producer': 'untrusted'}, {'schema_version': 2}, {'type': 'unknown.v1'}, {'unexpected': True}]:
    value = dict(events[0], **change)
    assert list(ev.iter_errors(value))
    counts['negative_cases'] += 1
for fixture in [fixtures['CommentCreate']]:
    text = fixture['body']
    last_end = 0
    for mention in sorted(fixture['mentions'], key=lambda m: m['start']):
        start, end = mention['start'], mention['start'] + mention['length']
        assert last_end <= start < end <= len(text)
        assert text[start:end].startswith('@') and end - start >= 2
        last_end = end

# Check all local Markdown artifact links resolve (external sources excluded).
for path in ROOT.rglob('*.md'):
    for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
        if target.startswith(('https://', 'http://', '#')):
            continue
        assert (path.parent / target.split('#')[0]).exists(), (path, target)
print(json.dumps({'status': 'PASS', **counts, 'public_operations': len(public_routes)}, indent=2))
