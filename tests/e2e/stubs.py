"""Test doubles of Google and OpenAI for the end-to-end tests of the container.

One HTTP server, on the port the container is configured with (container.env):

- /google/token: any code but "bad-code" is accepted; the Google access token
  is "google-<code>", and the user's email "<code>@example.com"
- /google/userinfo, /google/revoke
- /openai/v1/responses: a fixed ingredients list; an error for the English
  prompts, to test the refund of a failed analysis
- /revenuecat/v1/subscribers/<id> (GET) and .../subscriptions/<transaction>/cancel
  (POST): the customers are what the tests put in `StubServer.subscribers`
"""
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

INGREDIENTS = ["  Pâte ", "Farine", "TOMATE"]
REVENUECAT = "/revenuecat/v1/subscribers/"


class StubServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), _Handler)
        # (path, headers, body) of each request received, for the assertions
        self.requests: list[tuple[str, dict, bytes]] = []
        # RevenueCat customers (API v1 `subscriber` objects) by app user id
        self.subscribers: dict[str, dict] = {}

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def received(self, path: str) -> list[tuple[dict, bytes]]:
        return [(headers, body) for p, headers, body in self.requests if p == path]


class _Handler(BaseHTTPRequestHandler):
    server: StubServer

    def log_message(self, format, *args):
        pass

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> bytes:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.requests.append((self.path.split("?")[0], dict(self.headers), body))
        return body

    def _revenuecat_authorized(self) -> bool:
        return self.headers.get("Authorization") == "Bearer sk_e2e"

    def _subscriber(self, app_user_id: str) -> dict:
        return self.server.subscribers.setdefault(app_user_id, {
            "original_app_user_id": app_user_id, "entitlements": {}, "subscriptions": {}})

    def do_GET(self):
        self._record()
        if self.path.startswith(REVENUECAT):
            if not self._revenuecat_authorized():
                return self._reply(401, {"message": "Invalid API key"})
            app_user_id = self.path.removeprefix(REVENUECAT)
            return self._reply(200, {"subscriber": self._subscriber(app_user_id)})
        if self.path == "/google/userinfo":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not token.startswith("google-"):
                return self._reply(401, {"error": "invalid_token"})
            code = token.removeprefix("google-")
            return self._reply(200, {"sub": f"sub-{code}", "email": f"{code}@example.com",
                                     "name": code.title(), "picture": "https://example.com/p.png"})
        self._reply(404, {"error": "not_found"})

    def do_POST(self):
        body = self._record()
        if self.path.startswith(REVENUECAT) and self.path.endswith("/cancel"):
            if not self._revenuecat_authorized():
                return self._reply(401, {"message": "Invalid API key"})
            app_user_id, _, transaction_id, _ = self.path.removeprefix(REVENUECAT).split("/")
            subscriber = self._subscriber(app_user_id)
            for subscription in subscriber["subscriptions"].values():
                if subscription.get("store_transaction_id") == transaction_id:
                    subscription["unsubscribe_detected_at"] = datetime.now(timezone.utc).isoformat()
                    return self._reply(200, {"subscriber": subscriber})
            return self._reply(404, {"message": "Subscription not found"})
        if self.path == "/google/token":
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            if form.get("code") == "bad-code" or form.get("client_secret") != "e2e-client-secret":
                return self._reply(400, {"error": "invalid_grant"})
            return self._reply(200, {"access_token": f"google-{form['code']}", "token_type": "Bearer",
                                     "expires_in": 3600, "scope": "email profile"})
        if self.path == "/google/revoke":
            return self._reply(200, {})
        if self.path == "/openai/v1/responses":
            if self.headers.get("Authorization") != "Bearer sk-e2e":
                return self._reply(401, {"error": {"message": "Invalid API key", "type": "invalid_request_error"}})
            request = json.loads(body)
            if "Language: English" in request["input"][0]["content"]:
                # 400: not retried by the SDK
                return self._reply(400, {"error": {"message": "Test failure", "type": "invalid_request_error"}})
            return self._reply(200, {
                "id": "resp_e2e",
                "object": "response",
                "created_at": 0,
                "model": request["model"],
                "status": "completed",
                "output": [{
                    "type": "message",
                    "id": "msg_e2e",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "annotations": [],
                                 "text": json.dumps({"ingredients": INGREDIENTS})}],
                }],
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
            })
        self._reply(404, {"error": "not_found"})
