"""PROPOSED: loopback HTTP adapter; domain work belongs to isolated workers."""
import argparse
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import os
import tempfile
import threading
from urllib.parse import urlsplit, parse_qs

from . import core
from .export import parse
from .service import Service, Error, CONFIG_SHA, failure, exact


STATIC = Path(__file__).resolve().parents[1] / 'static'


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service=None, *, max_handlers=16, idle_timeout=10,
                 legacy_quiescent=False, bootstrap_directory=None):
        if address[0] != '127.0.0.1':
            raise ValueError('Loopback only')
        if type(max_handlers) is not int or not 1 <= max_handlers <= 64:
            raise ValueError('Invalid handler limit')
        if isinstance(idle_timeout, bool) or not isinstance(idle_timeout, (int, float)) or not 0 < idle_timeout <= 180:
            raise ValueError('Invalid socket timeout')
        self._closing = False
        self.service = None
        self.slots = threading.BoundedSemaphore(max_handlers)
        self.idle_timeout = idle_timeout
        super().__init__(address, Handler)
        try:
            self.service = service if service is not None else Service(legacy_quiescent=legacy_quiescent)
            self.session = secrets.token_urlsafe(32)
            self.csrf = secrets.token_urlsafe(32)
            self.bootstrap = secrets.token_urlsafe(32)
            self.bootstrap_lock = threading.Lock()
            if bootstrap_directory is None:
                self.auth_directory = tempfile.TemporaryDirectory(prefix='folderundo-auth-')
                auth_path = Path(self.auth_directory.name)
            else:
                auth_path=Path(bootstrap_directory)
                fd=core.open_directory_chain(auth_path,create=True)
                try:
                    info=os.fstat(fd)
                    if info.st_uid!=os.getuid() or info.st_mode & 0o077:
                        raise ValueError('PRIVATE_BOOTSTRAP_DIRECTORY_REQUIRED')
                finally: os.close(fd)
            self.bootstrap_file = auth_path / 'open-url.txt'
            fd = os.open(self.bootstrap_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(f'http://127.0.0.1:{self.server_address[1]}/#bootstrap={self.bootstrap}\n')
        except BaseException:
            self.server_close()
            raise

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.idle_timeout)
        return request, address

    def process_request(self, request, address):
        if self._closing or not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def server_close(self):
        # Stop admission before draining the coordinator. Preserve bootstrap files
        # if quiescence fails; never clean up a live worker's ownership boundary.
        self._closing = True
        try:
            if self.service is not None:
                self.service.close()
        except BaseException:
            super().server_close()
            raise
        super().server_close()
        if hasattr(self, 'auth_directory'):
            self.auth_directory.cleanup()


class Handler(BaseHTTPRequestHandler):
    server_version = 'FolderUndo'

    def log_message(self, *args):
        pass

    def reply(self, status, value, content_type='application/json', cookie=False):
        raw = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if cookie:
            self.send_header('Set-Cookie', 'folderundo=' + self.server.session + '; HttpOnly; SameSite=Strict; Path=/')
        if content_type == 'application/zip':
            self.send_header('Content-Disposition', 'attachment; filename="folderundo-evidence.zip"')
        self.end_headers()
        self.wfile.write(raw)

    def guard(self, mutate=False, session=True):
        if self.server._closing:
            raise Error('COORDINATOR_CLOSED', 503)
        port = self.server.server_address[1]
        unique = ('Host', 'Origin', 'Cookie', 'X-CSRF-Token', 'Idempotency-Key', 'Content-Type', 'X-Bootstrap-Token')
        if any(len(self.headers.get_all(name, [])) > 1 for name in unique):
            raise Error('DUPLICATE_HEADER', 400)
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            raise Error('LOCAL_HOST_REQUIRED', 403)
        origin = self.headers.get('Origin')
        if origin is not None and origin != 'http://' + self.headers.get('Host'):
            raise Error('LOCAL_ORIGIN_REQUIRED', 403)
        if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            raise Error('LOCAL_ORIGIN_REQUIRED', 403)
        if session:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
            except Exception:
                raise Error('SESSION_REQUIRED', 403) from None
            token = cookie['folderundo'].value if 'folderundo' in cookie else ''
            if not hmac.compare_digest(token, self.server.session):
                raise Error('SESSION_REQUIRED', 403)
        if mutate and (origin != 'http://' + self.headers.get('Host')
                       or not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), self.server.csrf)):
            raise Error('CSRF_REQUIRED', 403)

    def do_GET(self):
        try:
            url = urlsplit(self.path)
            if url.path in ('/', '/app.js', '/style.css'):
                self.guard(session=False)
                name, mime = {'/': ('index.html', 'text/html; charset=utf-8'),
                              '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                              '/style.css': ('style.css', 'text/css; charset=utf-8')}[url.path]
                return self.reply(200, (STATIC / name).read_bytes(), mime)
            self.guard()
            if url.path == '/session':
                if url.query:
                    raise Error('INVALID_QUERY')
                return self.reply(200, {'csrf': self.server.csrf, 'config_sha': CONFIG_SHA,
                                        'provider': 'CLOSED', **self.server.service.session_view()})
            parts = url.path.strip('/').split('/')
            if len(parts) not in (2, 3) or parts[0] != 'runs':
                raise Error('NOT_FOUND', 404)
            run = parts[1]
            if len(parts) == 2:
                query = parse_qs(url.query, strict_parsing=True, keep_blank_values=True)
                if set(query) - {'after'} or any(len(value) != 1 for value in query.values()):
                    raise Error('INVALID_QUERY')
                after = int(query.get('after', ['0'])[0])
                if not 0 <= after <= 1000000000:
                    raise Error('INVALID_QUERY')
                return self.reply(200, self.server.service.status(run, after))
            if url.query:
                raise Error('INVALID_QUERY')
            if parts[2] == 'scan':
                return self.reply(200, self.server.service.scan(run))
            if parts[2] == 'export':
                return self.reply(200, self.server.service.export(run), 'application/zip')
            raise Error('NOT_FOUND', 404)
        except Error as exc:
            self.reply(exc.status, failure(exc.code))
        except core.Refusal:
            self.reply(409, failure('ERROR_EVIDENCE'))
        except (ValueError, TypeError, RecursionError):
            self.reply(400, failure('INVALID_REQUEST'))
        except Exception:
            self.reply(500, failure('INTERNAL_ERROR'))

    def do_POST(self):
        try:
            bootstrap = self.path == '/bootstrap'
            self.guard(mutate=not bootstrap, session=not bootstrap)
            if bootstrap and self.headers.get('Origin') != 'http://' + self.headers.get('Host'):
                raise Error('LOCAL_ORIGIN_REQUIRED', 403)
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                raise Error('INVALID_LENGTH')
            length = int(self.headers['Content-Length'])
            if not 0 <= length <= 16384:
                raise Error('BODY_LIMIT', 413)
            if self.headers.get('Content-Type') != 'application/json':
                raise Error('JSON_REQUIRED', 415)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise Error('INVALID_LENGTH')
            body = parse(raw)
            if not isinstance(body, dict):
                raise Error('INVALID_SCHEMA')
            if bootstrap:
                if body != {}:
                    raise Error('INVALID_SCHEMA')
                token = self.headers.get('X-Bootstrap-Token', '')
                with self.server.bootstrap_lock:
                    if not self.server.bootstrap or not hmac.compare_digest(token, self.server.bootstrap):
                        raise Error('BOOTSTRAP_REQUIRED', 403)
                    self.server.bootstrap = None
                return self.reply(200, {'authenticated': True}, cookie=True)
            url = urlsplit(self.path)
            parts = url.path.strip('/').split('/')
            if url.query:
                raise Error('INVALID_QUERY')
            if parts in (['runs'], ['reference-runs']):
                action, run = 'create', None
            elif parts in (['runs', 'preserve-and-create'], ['reference-runs', 'preserve-and-create']):
                action, run = 'create-preserving', None
            elif len(parts) == 3 and parts[0] == 'runs':
                run, action = parts[1:]
            else:
                raise Error('NOT_FOUND', 404)
            key = self.headers.get('Idempotency-Key')
            if action in ('create', 'create-preserving'):
                exact(body, ('fixture_id', 'mode', 'idempotency_key', 'config_sha'))
                if ((parts[0] == 'runs' and body['mode'] not in ('B0', 'B1', 'B2'))
                        or (parts[0] == 'reference-runs' and body['mode'] != 'reference')):
                    raise Error('INVALID_MODE')
                if key is not None and key != body['idempotency_key']:
                    raise Error('IDEMPOTENCY_CONFLICT', 409)
                key = body.pop('idempotency_key')
            status, result = self.server.service.mutate(action, run, body, key)
            self.reply(status, result)
        except Error as exc:
            self.reply(exc.status, failure(exc.code))
        except core.Refusal:
            self.reply(409, failure('ERROR_EVIDENCE'))
        except (ValueError, TypeError, RecursionError):
            self.reply(400, failure('INVALID_REQUEST'))
        except Exception:
            self.reply(500, failure('INTERNAL_ERROR'))
        finally:
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', choices=['127.0.0.1'], default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--legacy-quiescent', action='store_true',
                        help='Attest that the old service and every old database writer have stopped before first legacy import')
    args = parser.parse_args()
    with Server((args.host, args.port), legacy_quiescent=args.legacy_quiescent) as server:
        print(f'FolderUndo owner bootstrap URL is in private file: {server.bootstrap_file}. Open that URL in your local browser. Linux local synthetic lab only.', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
