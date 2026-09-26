"""Desktop entry point for the local Session Notes server."""
import os
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from server import Handler

URL = 'http://127.0.0.1:8765'


def main():
    try:
        server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    except OSError as exc:
        print(f'Cannot start Session Notes on {URL}: {exc}', flush=True)
        print('Close another running copy of Session Notes, then try again.', flush=True)
        input('Press Enter to exit...')
        return
    server.daemon_threads = True
    print(f'Session Notes is running at {URL}', flush=True)
    print('Keep this window open while using the app. Close it to stop the app.', flush=True)
    if os.environ.get('SESSION_NOTES_NO_BROWSER') != '1':
        threading.Timer(0.6, lambda: webbrowser.open(URL)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
