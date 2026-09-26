#!/usr/bin/env python3
"""Local transcript note workbench. Binds to loopback only."""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).parent
MAX_BYTES = 12 * 1024 * 1024
KRISP_MCP = 'https://mcp.krisp.ai/mcp'
KRISP_REDIRECT = 'http://127.0.0.1:8765/krisp/callback'
krisp_auth = {'access_token': None, 'refresh_token': None, 'expires_at': 0, 'state': None}

def krisp_discovery():
    with urlopen(KRISP_MCP + '/.well-known/oauth-protected-resource', timeout=15) as response:
        resource = json.load(response)
    issuer = resource['authorization_servers'][0].rstrip('/')
    if not issuer.startswith('https://'): raise ValueError('Krisp authorization server must use HTTPS.')
    with urlopen(issuer + '/.well-known/oauth-authorization-server', timeout=15) as response:
        metadata = json.load(response)
    for key in ('authorization_endpoint', 'token_endpoint'):
        if not metadata.get(key, '').startswith('https://'): raise ValueError('Invalid Krisp OAuth metadata.')
    return metadata

def krisp_client(metadata):
    client_id = os.environ.get('KRISP_CLIENT_ID')
    if client_id: return client_id
    registration = metadata.get('registration_endpoint')
    if not registration or not registration.startswith('https://'):
        raise ValueError('Krisp requires a registered OAuth client. Set KRISP_CLIENT_ID for this local app.')
    data = json.dumps({'client_name': 'Session Notes Local', 'redirect_uris': [KRISP_REDIRECT],
                       'grant_types': ['authorization_code', 'refresh_token'],
                       'response_types': ['code'], 'token_endpoint_auth_method': 'none'}).encode()
    with urlopen(Request(registration, data=data, headers={'Content-Type': 'application/json'}), timeout=15) as response:
        return json.load(response)['client_id']

def krisp_tokens(fields):
    data = urlencode(fields).encode()
    with urlopen(Request(krisp_auth['metadata']['token_endpoint'], data=data,
                         headers={'Content-Type': 'application/x-www-form-urlencoded'}), timeout=20) as response:
        tokens = json.load(response)
    krisp_auth['access_token'] = tokens['access_token']
    krisp_auth['refresh_token'] = tokens.get('refresh_token', krisp_auth['refresh_token'])
    krisp_auth['expires_at'] = time.time() + int(tokens.get('expires_in', 3600)) - 60

def krisp_access_token():
    if not krisp_auth['access_token']:
        # An existing OAuth access token can also be supplied for short-lived local use.
        token = os.environ.get('KRISP_ACCESS_TOKEN')
        if not token: raise ValueError('Krisp is not connected. Click Connect Krisp first.')
        return token
    if krisp_auth['refresh_token'] and time.time() >= krisp_auth['expires_at']:
        krisp_tokens({'grant_type': 'refresh_token', 'refresh_token': krisp_auth['refresh_token'],
                      'client_id': krisp_auth['client_id']})
    return krisp_auth['access_token']

def krisp_call(method, params=None, session=None):
    token = krisp_access_token()
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    if session: headers['Mcp-Session-Id'] = session
    data = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode()
    with urlopen(Request(KRISP_MCP, data=data, headers=headers), timeout=45) as response:
        raw = response.read(8 * 1024 * 1024).decode()
        session = response.headers.get('Mcp-Session-Id', session)
        if 'text/event-stream' in response.headers.get('Content-Type', ''):
            events = [line[6:] for line in raw.splitlines() if line.startswith('data: ')]
            raw = events[-1] if events else '{}'
        result = json.loads(raw)
    if 'error' in result: raise ValueError(str(result['error'].get('message', 'Krisp request failed')))
    return result.get('result', {}), session

def krisp_tool(name, args):
    _, session = krisp_call('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'session-notes-local', 'version': '0.1'}})
    headers = {'Authorization': 'Bearer ' + krisp_access_token(), 'Content-Type': 'application/json'}
    if session: headers['Mcp-Session-Id'] = session
    notice = json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}).encode()
    with urlopen(Request(KRISP_MCP, data=notice, headers=headers), timeout=15): pass
    result, _ = krisp_call('tools/call', {'name': name, 'arguments': args}, session)
    if result.get('isError'): raise ValueError('Krisp tool returned an error: ' + str(result.get('content', ''))[:500])
    if result.get('structuredContent'): return result['structuredContent']
    parts = [c.get('text', '') for c in result.get('content', []) if c.get('type') == 'text']
    if len(parts) == 1:
        try: return json.loads(parts[0])
        except json.JSONDecodeError: pass
    return {'content': parts}

def find_items(value):
    if isinstance(value, list):
        if value and isinstance(value[0], dict) and any(k in value[0] for k in ('id', 'document_id', 'meeting_id')): return value
        for item in value:
            found = find_items(item)
            if found: return found
    elif isinstance(value, dict):
        for key in ('meetings', 'results', 'documents', 'items', 'data', 'content'):
            if key in value:
                found = find_items(value[key])
                if found: return found
    return []

def transcript_text(value):
    if isinstance(value, str):
        parts = re.split(r'(?m)^#{1,3} Transcript\s*$', value)
        return parts[-1].strip() if len(parts) > 1 else value
    if isinstance(value, list): return '\n'.join(filter(None, (transcript_text(x) for x in value)))
    if isinstance(value, dict):
        if 'text' in value and ('speaker' in value or 'speaker_name' in value):
            return str(value.get('speaker') or value.get('speaker_name')) + ': ' + str(value['text'])
        for key in ('results', 'document', 'documents', 'transcript', 'full_transcript', 'utterances', 'segments', 'text', 'content'):
            if key in value:
                return transcript_text(value[key])
        speaker = value.get('speaker') or value.get('speaker_name')
        return (str(speaker) + ': ' if speaker else '') + str(value.get('speech', ''))
    return ''

def map_sections(result, sections):
    values = result.get('sections', result) if isinstance(result, dict) else {}
    if isinstance(values, list):
        values = {str(v.get('id') or v.get('title') or ''): v.get('content', v.get('text', '')) for v in values if isinstance(v, dict)}
    if not isinstance(values, dict): values = {}
    mapped = {}
    for s in sections:
        value = values.get(s['id'], values.get(s['title'], ''))
        mapped[s['id']] = value.strip() if isinstance(value, str) else ''
    if not any(mapped.values()):
        raise ValueError('The model returned no note content. Check the selected model and try Generate draft again.')
    return mapped

def transcript_chunks(transcript, limit=9000):
    lines = transcript.splitlines(keepends=True)
    chunks, current = [], ''
    for line in lines:
        if len(current) + len(line) > limit and current:
            chunks.append(current)
            current = ''
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    if current: chunks.append(current)
    return chunks

def validate_note(mapped, transcript):
    populated = [v for v in mapped.values() if v]
    if not populated: raise ValueError('The model returned no note content.')
    if len(populated) > 1 and len(set(populated)) == 1:
        raise ValueError('The model repeated the same text in every section. No draft was accepted.')
    normalized = re.sub(r'\s+', ' ', transcript).casefold()
    for value in populated:
        flat = re.sub(r'\s+', ' ', value).casefold()
        if len(flat) > 300 and (flat[:250] in normalized or flat[-250:] in normalized):
            raise ValueError('The model copied a long passage from the transcript. No draft was accepted.')
    return mapped

def ollama_chat(endpoint, model, system, prompt, schema):
    req = Request(endpoint, data=json.dumps({'model': model, 'stream': False, 'format': schema,
        'options': {'temperature': 0, 'num_ctx': 8192},
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}]}).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=240) as response: raw = json.load(response)
    except HTTPError as exc:
        try: detail = json.load(exc).get('error', '')
        except Exception: detail = ''
        raise ValueError('Local model error: ' + (str(detail)[:200] or f'HTTP {exc.code}'))
    except URLError:
        raise ValueError('Cannot reach Ollama. Confirm Ollama is running on this computer.')
    content = raw.get('message', {}).get('content', '')
    if not content: raise ValueError('The local model returned an empty response.')
    try: return json.loads(content)
    except json.JSONDecodeError: raise ValueError('The local model did not return valid structured JSON.')

def extract(name, data):
    ext = Path(name).suffix.lower()
    if ext == '.txt':
        return data.decode('utf-8-sig', errors='replace')
    if ext == '.docx':
        from docx import Document
        doc = Document(io.BytesIO(data))
        return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
    if ext == '.pdf':
        from pypdf import PdfReader
        return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages)
    raise ValueError('Use a .txt, .docx, or text-based .pdf file.')

SYSTEM = """You are a clinical documentation assistant. Produce a draft for therapist review using ONLY transcript evidence. Do not invent diagnosis, symptoms, risk, assessment, interventions, mental status, or plans. Preserve ambiguity. Do not include names, phone numbers, email addresses, addresses, employer/school names, birth dates, account numbers, or identifying locations. Refer to provider only as Therapist; client as the client or Client. Replace other names with roles (Spouse, Partner, Parent, Child, Friend, etc.). Do not use pseudonyms. Preserve clinically meaningful quotes and exact risk statements without expanding or minimizing them. Return ONLY valid JSON object with a `sections` object mapping requested section IDs to strings. If unsupported, use an empty string. Brightside Initial diagnosis field: use only a diagnosis explicitly established in transcript; otherwise leave blank. Each transcript represents one session."""

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith('/krisp/callback?'):
            params = parse_qs(urlparse(self.path).query)
            if not krisp_auth.get('state') or params.get('state', [''])[0] != krisp_auth['state']:
                return self.reply(400, {'error': 'Invalid Krisp authorization state.'})
            krisp_auth['state'] = None
            if 'error' in params: return self.reply(400, {'error': 'Krisp authorization was declined.'})
            try:
                krisp_tokens({'grant_type': 'authorization_code', 'code': params['code'][0],
                              'redirect_uri': KRISP_REDIRECT, 'client_id': krisp_auth['client_id'],
                              'code_verifier': krisp_auth.pop('verifier')})
            except Exception:
                return self.reply(400, {'error': 'Krisp authorization failed. Try Connect Krisp again.'})
            page = b'<html><body><h2>Krisp connected</h2><p>Return to Session Notes and search meetings. You may close this tab.</p></body></html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            return self.wfile.write(page)
        path = '/index.html' if self.path == '/' else self.path
        if path not in ('/index.html', '/app.js', '/style.css'):
            return self.reply(404, {'error': 'Not found'})
        data = (ROOT / path.lstrip('/')).read_bytes()
        mime = 'text/html' if path.endswith('html') else 'text/javascript' if path.endswith('js') else 'text/css'
        self.send_response(200)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > MAX_BYTES * 2:
                raise ValueError('Request too large or empty.')
            body = json.loads(self.rfile.read(size))
            if self.path == '/krisp/status':
                return self.reply(200, {'connected': bool(krisp_auth['access_token'] or os.environ.get('KRISP_ACCESS_TOKEN'))})
            if self.path == '/krisp/connect':
                metadata = krisp_discovery()
                client_id = krisp_client(metadata)
                verifier = secrets.token_urlsafe(48)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                state = secrets.token_urlsafe(24)
                krisp_auth.update(metadata=metadata, client_id=client_id, verifier=verifier, state=state)
                url = metadata['authorization_endpoint'] + '?' + urlencode({
                    'response_type': 'code', 'client_id': client_id, 'redirect_uri': KRISP_REDIRECT,
                    'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
                    'scope': os.environ.get('KRISP_SCOPE', '')})
                return self.reply(200, {'url': url})
            if self.path == '/extract':
                data = base64.b64decode(body['data'], validate=True)
                if len(data) > MAX_BYTES: raise ValueError('File exceeds 12 MB.')
                content = extract(body['name'], data)
                if not content.strip(): raise ValueError('No text found. Scanned PDFs need OCR first.')
                return self.reply(200, {'text': content})
            if self.path == '/krisp/meetings':
                query = str(body.get('query', '')).strip()[:100]
                args = {'limit': 50, 'fields': ['name', 'date', 'transcript']}
                if query: args['search'] = query
                else: args['after'] = __import__('datetime').date.today().isoformat()[:4] + '-01-01'
                result = krisp_tool('search_meetings', args)
                items = find_items(result)
                meetings = []
                for item in items[:100]:
                    ident = str(item.get('meeting_id') or item.get('document_id') or item.get('id') or '').replace('-', '')
                    if re.fullmatch('[0-9a-f]{32}', ident):
                        meetings.append({'id': ident, 'title': str(item.get('title') or item.get('name') or 'Untitled meeting'), 'date': str(item.get('date') or item.get('started_at') or item.get('start_time') or '')})
                return self.reply(200, {'meetings': meetings})
            if self.path == '/krisp/transcript':
                ident = str(body.get('id', ''))
                if not re.fullmatch('[0-9a-f]{32}', ident): raise ValueError('Invalid Krisp document ID.')
                result = krisp_tool('get_multiple_documents', {'ids': [ident]})
                content = transcript_text(result)
                if not content.strip(): raise ValueError('Krisp returned no transcript for this meeting.')
                return self.reply(200, {'text': content})
            if self.path == '/generate':
                transcript = body['transcript']
                if len(transcript) > 150000:
                    raise ValueError('Transcript exceeds 150,000 characters. Split it into sessions or shorter parts.')
                template = body['template']
                sections = template['sections']
                if not transcript.strip() or not sections: raise ValueError('Transcript and template are required.')
                endpoint = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434/api/chat')
                if not endpoint.startswith(('http://127.0.0.1:', 'http://localhost:')):
                    raise ValueError('OLLAMA_URL must point to a local loopback service.')
                model = os.environ.get('OLLAMA_MODEL', 'llama3.1:8b')
                instructions = [{'id': s['id'], 'title': s['title'], 'instructions': s.get('instructions', '')} for s in sections]
                schema = {'type': 'object', 'properties': {'sections': {'type': 'object', 'properties': {s['id']: {'type': 'string'} for s in sections}, 'required': [s['id'] for s in sections]}}, 'required': ['sections']}
                chunks = transcript_chunks(transcript)
                if len(chunks) > 24: raise ValueError('Transcript is too long for a reliable local draft. Split it into sessions or shorter parts.')
                source = transcript
                if len(chunks) > 1:
                    evidence_schema = {'type': 'object', 'properties': {'evidence': {'type': 'string'}}, 'required': ['evidence']}
                    evidence = []
                    for index, chunk in enumerate(chunks, 1):
                        task = ('Summarize only clinically relevant facts in this excerpt in up to 140 words. '
                                'Use Client and Therapist roles; include interventions and responses only if present. '
                                'Preserve risk statements exactly and omit identifying information. '
                                'Do not copy the transcript or repeat dialogue. Return JSON with evidence string.')
                        data = ollama_chat(endpoint, model, SYSTEM, json.dumps({'task': task, 'excerpt': chunk}, ensure_ascii=False), evidence_schema)
                        summary = data.get('evidence', '')
                        if not isinstance(summary, str) or not summary.strip() or len(summary) > 2500:
                            raise ValueError(f'The model could not summarize transcript part {index}. No draft was accepted.')
                        if len(summary) > 300 and re.sub(r'\s+', ' ', summary).casefold()[:250] in re.sub(r'\s+', ' ', chunk).casefold():
                            raise ValueError(f'The model copied transcript part {index}. No draft was accepted.')
                        evidence.append(f'Part {index}: {summary.strip()}')
                    source = '\n'.join(evidence)
                prompt = json.dumps({'task': 'Write concise clinical narrative in each supported section. Do not copy raw dialogue. Do not repeat one response across sections. Use only the supplied source.',
                                     'template': template['name'], 'sections': instructions, 'source': source, 'output_schema': schema}, ensure_ascii=False)
                result = ollama_chat(endpoint, model, SYSTEM, prompt, schema)
                return self.reply(200, {'sections': validate_note(map_sections(result, sections), transcript)})
            return self.reply(404, {'error': 'Not found'})
        except Exception as exc:
            self.reply(400, {'error': str(exc)})

if __name__ == '__main__':
    print('Open http://127.0.0.1:8765')
    ThreadingHTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
