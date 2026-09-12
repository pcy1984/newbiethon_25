"""Vercel entry point. Local start.cmd continues to use backend/app.py."""
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import app as backend


class handler(backend.Handler):
    def check_local_request(self):
        allowed = {os.environ.get(name, '').lower() for name in
                   ('VERCEL_URL', 'VERCEL_BRANCH_URL', 'VERCEL_PROJECT_PRODUCTION_URL')}
        allowed.update(h.strip().lower() for h in os.environ.get('APP_ALLOWED_HOSTS', '').split(','))
        allowed.discard('')
        host = self.headers.get('Host', '').lower()
        if host not in allowed:
            raise backend.AppError('허용되지 않은 서비스 주소입니다.', 403)
        origin = self.headers.get('Origin')
        if origin and origin != 'https://' + host:
            raise backend.AppError('허용되지 않은 요청입니다.', 403)
        self.server.config = backend.Config.load()
        # Rewrites pass only an endpoint name; private files are never served.
        parsed = urlsplit(self.path)
        params = parse_qsl(parsed.query, keep_blank_values=True)
        endpoint = next((value for key, value in params if key == 'endpoint'), None)
        if endpoint is not None:
            if endpoint not in ('config', 'places', 'routes', 'deadline', 'stress', 'recovery', 'walk', 'taxi'):
                raise backend.AppError('페이지를 찾을 수 없습니다.', 404)
            query = urlencode([(key, value) for key, value in params if key != 'endpoint'])
            self.path = '/api/' + endpoint + ('?' + query if query else '')
        if not self.path.startswith('/api/'):
            raise backend.AppError('페이지를 찾을 수 없습니다.', 404)
