"""Desktop OAuth and the YouTube Data API v3 transport."""
from __future__ import annotations

import hmac
import json
import re
import time
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.credentials import NativeVault

SCOPE = 'https://www.googleapis.com/auth/youtube.force-ssl'
API = 'https://www.googleapis.com/youtube/v3/'
UPLOAD = 'https://www.googleapis.com/upload/youtube/v3/videos'
REVOKE = 'https://oauth2.googleapis.com/revoke'
CHANNEL_ID = re.compile(r'UC[A-Za-z0-9_-]{22}')
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')


class YouTubeError(RuntimeError):
    pass


def callback_code(target, state):
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or parts.path != '/' or parts.fragment:
        raise ValueError('Invalid callback path')
    query = parse_qs(parts.query, keep_blank_values=True)
    states = query.get('state', [])
    if len(states) != 1 or not hmac.compare_digest(states[0].encode(), state.encode()):
        raise ValueError('Invalid callback state')
    if 'error' in query:
        raise YouTubeError('Google authorization was declined. Select Connect YouTube to retry.')
    codes = query.get('code', [])
    if len(codes) != 1 or not codes[0]:
        raise ValueError('Invalid authorization code')
    return codes[0]


def browser_credentials(config):
    flow = InstalledAppFlow.from_client_config(config, [SCOPE], autogenerate_code_verifier=True)
    result = []

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            try:
                result.append(callback_code(self.path, state))
            except ValueError:
                self.send_error(400, 'Invalid authorization callback')
                return
            except YouTubeError as exc:
                result.append(exc)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Return to ViceScope Clip Studio.')

    class CallbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

    with CallbackServer(('127.0.0.1', 0), Callback) as server:
        server.timeout = 0.5
        flow.redirect_uri = f'http://127.0.0.1:{server.server_port}/'
        url, state = flow.authorization_url(prompt='consent select_account')
        if not webbrowser.open(url):
            raise YouTubeError('The system browser could not be opened.')
        deadline = time.monotonic() + 180
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result:
        raise YouTubeError('Google authorization timed out. Select Connect YouTube to retry.')
    if isinstance(result[0], YouTubeError):
        raise result[0]
    flow.fetch_token(code=result[0], timeout=30)
    return flow.credentials


def response_error(response):
    try:
        reasons = {entry.get('reason') for entry in response.json()['error']['errors']}
    except (AttributeError, KeyError, TypeError, ValueError):
        reasons = set()
    if reasons & {'quotaExceeded', 'dailyLimitExceeded'}:
        return YouTubeError('YouTube API quota is exhausted. Retry after the Google Cloud quota resets.')
    if response.status_code == 401:
        return YouTubeError('Google credentials expired or were revoked. Reconnect YouTube.')
    if response.status_code == 429 or reasons & {'rateLimitExceeded', 'userRateLimitExceeded'}:
        return YouTubeError('YouTube is rate limiting requests. Wait before retrying.')
    return YouTubeError(f'YouTube API returned HTTP {response.status_code}. Check YouTube Studio and project access.')


class YouTubeAccount:
    def __init__(self, config_path, expected_channel, vault_factory=NativeVault):
        self.config_path = config_path
        self.expected_channel = expected_channel
        self.vault_factory = vault_factory

    def vault(self):
        try:
            return self.vault_factory()
        except Exception:
            raise YouTubeError('The native credential store is unavailable. Unlock your OS credential store and retry.') from None

    def require_channel(self):
        if not CHANNEL_ID.fullmatch(self.expected_channel):
            raise YouTubeError('Configure the intended ViceScope channel ID in settings.json or VICESCOPE_YOUTUBE_CHANNEL_ID.')

    def client_config(self):
        try:
            config = json.loads(self.config_path.read_text(encoding='utf-8'))
            client = config['installed']
            if (not client['client_id'].endswith('.apps.googleusercontent.com')
                    or not isinstance(client['client_secret'], str) or not client['client_secret']):
                raise ValueError()
            if client['auth_uri'] != 'https://accounts.google.com/o/oauth2/auth' or client['token_uri'] != 'https://oauth2.googleapis.com/token':
                raise ValueError()
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            raise YouTubeError('Place a Google Desktop app client JSON at youtube-client.json in the app data folder.') from None
        return config

    def connect(self):
        self.require_channel()
        config = self.client_config()
        vault = self.vault()
        try:
            credentials = browser_credentials(config)
        except YouTubeError:
            raise
        except Exception:
            raise YouTubeError('Google authorization failed. Check Google Cloud settings and reconnect.') from None
        if not credentials.refresh_token:
            raise YouTubeError('Google did not return a refresh token. Reconnect and grant consent.')
        with AuthorizedSession(credentials, max_refresh_attempts=0) as session:
            channel = self.verify(session)
        vault.put('refresh-token', credentials.refresh_token)
        return channel

    def disconnect(self):
        vault = self.vault()
        token = vault.get('refresh-token')
        if not token:
            return
        try:
            response = requests.post(REVOKE, data={'token': token}, timeout=30, allow_redirects=False)
        except requests.RequestException:
            raise YouTubeError('Google revocation could not be reached. Retry Disconnect YouTube.') from None
        if response.status_code != 200:
            try:
                already_revoked = response.status_code == 400 and response.json().get('error') == 'invalid_token'
            except (ValueError, AttributeError):
                already_revoked = False
            if not already_revoked:
                raise response_error(response)
        vault.delete('refresh-token')

    @contextmanager
    def session(self):
        self.require_channel()
        vault = self.vault()
        token = vault.get('refresh-token')
        if not token:
            raise YouTubeError('Connect YouTube before continuing.')
        client = self.client_config()['installed']
        credentials = Credentials(None, refresh_token=token, token_uri=client['token_uri'],
                                  client_id=client['client_id'], client_secret=client['client_secret'], scopes=[SCOPE])
        with AuthorizedSession(credentials, max_refresh_attempts=0) as session:
            try:
                yield session
            finally:
                if credentials.refresh_token != token:
                    vault.put('refresh-token', credentials.refresh_token)

    def verify(self, session):
        payload = api_json(session, 'GET', API + 'channels', params={'part': 'id,snippet', 'mine': 'true', 'maxResults': 2})
        items = payload.get('items', [])
        if len(items) != 1 or payload.get('nextPageToken') or items[0].get('id') != self.expected_channel:
            raise YouTubeError('Connected YouTube channel does not match the configured ViceScope channel.')
        return items[0]['snippet']['title'], self.expected_channel

    def channel(self):
        with self.session() as session:
            return self.verify(session)


def request(session, method, url, **kwargs):
    try:
        return session.request(method, url, timeout=45, allow_redirects=False, **kwargs)
    except GoogleAuthError:
        raise YouTubeError('Google credentials could not be refreshed. Reconnect YouTube.') from None
    except requests.RequestException:
        raise YouTubeError('YouTube connection interrupted. Check the connection and retry the same action.') from None


def api_json(session, method, url, **kwargs):
    response = request(session, method, url, **kwargs)
    if response.status_code not in (200, 201):
        raise response_error(response)
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    raise YouTubeError('YouTube returned an invalid response. Retry the same action.')
