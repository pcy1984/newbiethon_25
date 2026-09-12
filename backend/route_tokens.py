"""Short-lived signed route snapshots for stateless serverless requests.

Tokens contain route data (not encrypted), never API keys. Only our server can
sign them. Expiry and size bounds are enforced before a snapshot is restored.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import zlib
from seoul_transit import SeoulError

MAX_RAW = 262144
MAX_TOKEN = 48000
TTL = 300


def secret():
    key = os.environ.get('JOURNEY_TOKEN_SECRET', '')
    if (key or os.environ.get('VERCEL')) and len(key) < 32:
        raise SeoulError('서버의 경로 검증 설정이 필요합니다. 관리자에게 문의해 주세요.', 503)
    return key


def encode(context, routes, key):
    raw = json.dumps({'expires': time.time() + TTL, 'context': context, 'routes': routes},
                     ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_RAW:
        raise SeoulError('경로 정보가 너무 큽니다. 더 가까운 역·정류장으로 검색해 주세요.', 422)
    body = base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip('=')
    signature = hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()
    token = 'v1.' + body + '.' + signature
    if len(token) > MAX_TOKEN:
        raise SeoulError('경로 정보가 너무 큽니다. 더 가까운 역·정류장으로 검색해 주세요.', 422)
    return token


def decode(token, key):
    try:
        if not key or not isinstance(token, str) or len(token) > MAX_TOKEN:
            raise ValueError()
        version, body, signature = token.split('.')
        expected = hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if version != 'v1' or not hmac.compare_digest(signature, expected):
            raise ValueError()
        compressed = base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_RAW + 1)
        if len(raw) > MAX_RAW or not decoder.eof or decoder.unused_data:
            raise ValueError()
        data = json.loads(raw)
        now = time.time()
        if type(data['expires']) not in (float, int) or not now <= data['expires'] <= now + TTL + 5:
            raise ValueError()
        if not isinstance(data['routes'], list) or not isinstance(data['context'], list):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, zlib.error):
        raise SeoulError('조회 결과가 만료됐거나 변경됐어요. 경로를 다시 검색해 주세요.', 409) from None
