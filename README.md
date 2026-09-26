# Session Notes — local prototype

A local drafting interface for psychotherapy transcripts. Four editable note types are included: Brightside Follow-Up, Brightside Initial Session, Grow Follow-Up, and Summarize. Each uploaded file has its own note type and editable section-level output with copy controls.

## Run

1. Install Python 3 and the file readers: `python3 -m pip install -r requirements.txt`
2. Install [Ollama](https://ollama.com/) locally and pull a model, for example `ollama pull llama3.1:8b`.
3. Run `python3 app/server.py` and open `http://127.0.0.1:8765`.

Set `OLLAMA_MODEL` to another installed local model if desired. The server only accepts an `OLLAMA_URL` on `localhost` or `127.0.0.1` and binds the web interface to `127.0.0.1`. It does not require an API key or send transcripts to a hosted model. The browser keeps template edits in localStorage. Transcripts and drafts stay in memory until the page is closed or reloaded; they are not saved by the app.

## Workflow

Drop `.txt`, `.docx`, or text-based `.pdf` files. Select a note type for each file, review/edit the extracted transcript, then click **Generate draft**. Edit each output section and use its Copy button or **Copy all**. A scanned PDF needs OCR before upload. Each file is treated as one session.

Use the checkboxes in the Sessions list (or **Select all**) and choose **Generate All** to draft notes sequentially with each session's selected template. The date menu sorts Krisp meetings by meeting date and uploaded files by the file's modified date; items without dates appear last. Existing drafts require one confirmation before a batch replaces them. Batch generation may take several minutes per long transcript.

## Krisp transcript picker

Click **Connect Krisp**, complete Krisp's OAuth sign-in in the new tab, then return to the app and click **Search**. Search by meeting title or participant (for example, a name); an empty query starts with the newest available meetings. Krisp returns up to 50 per request; use **Load more meetings** to page through older results. Select all applies to the meetings loaded so far. Check meetings, choose a note type beside each, and select **Add selected transcripts**. This uses Krisp's official MCP service at `https://mcp.krisp.ai/mcp`. Access and refresh tokens are held only in the running server's memory, so restarting the server requires reconnecting. If Krisp does not allow dynamic client registration for your account, configure a registered public OAuth client ID with `KRISP_CLIENT_ID` and the callback `http://127.0.0.1:8765/krisp/callback`.

Do not put a Krisp token in a source file, browser storage, or Git commit. A user key is not necessarily an MCP OAuth access token. If the key you shared in chat is live, revoke it and create a replacement before using it elsewhere.

The model may still omit details, mistake speakers, invent content, or fail to anonymize. Review every draft against its source, particularly names, risk statements, medication, diagnosis, and undocumented interventions. Do not use an unreviewed draft as a clinical record. The Brightside diagnosis section is instructed to remain blank unless the source explicitly establishes a diagnosis.

This is a prototype for local evaluation. Before using identifiable clinical material, verify the computer's security, your practice's policies, and whether the chosen local model and workflow meet your obligations. Only source code and synthetic examples belong in a Git repository. There are no real transcripts in this package.

## Project files

- `app/server.py`: loopback web server, text extraction, and local model call.
- `app/app.js`: queue, four templates, template editing, draft review, copy controls.
- `app/style.css`: responsive interface.

Templates can be changed in the interface. The shared accuracy and confidentiality instructions are held in the server's `SYSTEM` constant. The app does not perform a separate deterministic PHI scan; anonymization is requested of the model and must be checked by the therapist.
