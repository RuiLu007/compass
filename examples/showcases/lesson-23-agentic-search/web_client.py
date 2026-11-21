"""Same-origin Web client for the isolated classroom."""

import base64
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import httpx
from materials import TENANT, WORKSPACE


class ClassroomWeb:
    def __init__(self, login="teaching.analyst"):
        self.client = httpx.Client(
            base_url=os.environ["WEB_PUBLIC_ORIGIN"], timeout=90, trust_env=False
        )
        self.client.headers["origin"] = os.environ["WEB_PUBLIC_ORIGIN"]
        response = self.client.post(
            "/api/web/v1/auth/login",
            json={"loginName": login, "password": os.environ["WEB_TEST_PASSWORD"]},
        )
        response.raise_for_status()
        token = next(cookie.value for cookie in self.client.cookies.jar if len(cookie.value) == 43)
        self.client.headers["x-csrf-token"] = (
            base64.urlsafe_b64encode(
                hmac.new(token.encode(), b"compass-csrf-v1", hashlib.sha256).digest()
            )
            .decode()
            .rstrip("=")
        )
        self.scope = {"tenantId": TENANT, "workspaceId": WORKSPACE}

    def fetch(self, path, body=None, *, query=None):
        url = path + ("?" + urlencode(query) if query else "")
        if body is not None:
            response = self.client.post(url, json=body)
        else:
            for attempt in range(5):
                try:
                    response = self.client.get(url)
                except httpx.TransportError:
                    if attempt == 4:
                        raise
                else:
                    if response.status_code not in {429, 502, 503, 504} or attempt == 4:
                        break
                time.sleep(min(2**attempt, 8))
        if not response.is_success:
            raise ValueError(
                f"{response.request.method} {path}: HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )
        return response.json()

    def semantic(self, path, body=None):
        return self.fetch("/api/semantic/" + path, body, query=self.scope)
