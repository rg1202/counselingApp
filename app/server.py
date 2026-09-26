#!/usr/bin/env python3
"""Local transcript note workbench. Binds to loopback only."""
import base64
import io
import json
import os
import re
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).parent
MAX_BYTES = 12 * 1024 * 1024
KRISP_MCP = 'https://mcp.krisp.ai/mcp'

def krisp_call(method, params=None, session=None):
    token = os.environ.get('KRISP_ACCESS_TOKEN')
    if not token:
        raise ValueError('Krisp is not connected. Set a Krisp OAuth access token in KRISP_ACCESS_TOKEN on the local server.')
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
    if isinstance(value, str): return value
    if isinstance(value, list): return '\n'.join(filter(None, (transcript_text(x) for x in value)))
    if isinstance(value, dict):
        if 'text' in value and ('speaker' in value or 'speaker_name' in value):
            return str(value.get('speaker') or value.get('speaker_name')) + ': ' + str(value['text'])
        for key in ('documents', 'transcript', 'full_transcript', 'utterances', 'segments', 'text', 'content'):
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
            if self.path == '/extract':
                data = base64.b64decode(body['data'], validate=True)
                if len(data) > MAX_BYTES: raise ValueError('File exceeds 12 MB.')
                content = extract(body['name'], data)
                if not content.strip(): raise ValueError('No text found. Scanned PDFs need OCR first.')
                return self.reply(200, {'text': content})
            if self.path == '/krisp/meetings':
                result = krisp_tool('search_meetings', {'query': body.get('query', '')[:100]})
                items = find_items(result)
                meetings = []
                for item in items[:100]:
                    ident = str(item.get('document_id') or item.get('id') or item.get('meeting_id') or '').replace('-', '')
                    if re.fullmatch('[0-9a-f]{32}', ident):
                        meetings.append({'id': ident, 'title': str(item.get('title') or item.get('name') or 'Untitled meeting'), 'date': str(item.get('date') or item.get('started_at') or item.get('start_time') or '')})
                return self.reply(200, {'meetings': meetings})
            if self.path == '/krisp/transcript':
                ident = str(body.get('id', ''))
                if not re.fullmatch('[0-9a-f]{32}', ident): raise ValueError('Invalid Krisp document ID.')
                result = krisp_tool('get_multiple_documents', {'document_ids': [ident], 'include_transcript': True})
                content = transcript_text(result)
                if not content.strip(): raise ValueError('Krisp returned no transcript for this meeting.')
                return self.reply(200, {'text': content})
            if self.path == '/generate':
                transcript = body['transcript'][:150000]
                template = body['template']
                sections = template['sections']
                if not transcript.strip() or not sections: raise ValueError('Transcript and template are required.')
                endpoint = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434/api/chat')
                if not endpoint.startswith(('http://127.0.0.1:', 'http://localhost:')):
                    raise ValueError('OLLAMA_URL must point to a local loopback service.')
                model = os.environ.get('OLLAMA_MODEL', 'llama3.1:8b')
                instructions = [{'id': s['id'], 'title': s['title'], 'instructions': s.get('instructions', '')} for s in sections]
                schema = {'type': 'object', 'properties': {'sections': {'type': 'object', 'properties': {s['id']: {'type': 'string'} for s in sections}, 'required': [s['id'] for s in sections]}}, 'required': ['sections']}
                prompt = json.dumps({'template': template['name'], 'sections': instructions, 'transcript': transcript, 'output_schema': schema}, ensure_ascii=False)
                req = Request(endpoint, data=json.dumps({'model': model, 'stream': False, 'format': schema, 'options': {'temperature': 0}, 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': prompt}]}).encode(), headers={'Content-Type': 'application/json'})
                try:
                    with urlopen(req, timeout=240) as response:
                        raw = json.load(response)
                except HTTPError as exc:
                    try: detail = json.load(exc).get('error', '')
                    except Exception: detail = ''
                    raise ValueError('Local model error: ' + (str(detail)[:200] or f'HTTP {exc.code}'))
                except URLError:
                    raise ValueError('Cannot reach Ollama. Confirm Ollama is running on this computer.')
                content = raw.get('message', {}).get('content', '')
                if not content: raise ValueError('The local model returned an empty response.')
                try: result = json.loads(content)
                except json.JSONDecodeError: raise ValueError('The local model did not return valid structured JSON.')
                return self.reply(200, {'sections': map_sections(result, sections)})
            return self.reply(404, {'error': 'Not found'})
        except Exception as exc:
            self.reply(400, {'error': str(exc)})

if __name__ == '__main__':
    print('Open http://127.0.0.1:8765')
    ThreadingHTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
