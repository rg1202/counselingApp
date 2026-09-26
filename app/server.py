#!/usr/bin/env python3
"""Local transcript note workbench. Binds to loopback only."""
import base64
import io
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
MAX_BYTES = 12 * 1024 * 1024

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
                prompt = json.dumps({'template': template['name'], 'sections': instructions, 'transcript': transcript}, ensure_ascii=False)
                req = Request(endpoint, data=json.dumps({'model': model, 'stream': False, 'format': 'json', 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': prompt}]}).encode(), headers={'Content-Type': 'application/json'})
                with urlopen(req, timeout=240) as response:
                    raw = json.load(response)
                result = json.loads(raw['message']['content'])
                values = result.get('sections', {})
                return self.reply(200, {'sections': {s['id']: str(values.get(s['id'], '')) for s in sections}})
            return self.reply(404, {'error': 'Not found'})
        except Exception as exc:
            self.reply(400, {'error': str(exc)})

if __name__ == '__main__':
    print('Open http://127.0.0.1:8765')
    ThreadingHTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
