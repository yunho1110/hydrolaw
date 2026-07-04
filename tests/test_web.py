"""serve.py 웹 서버 통합 테스트.

서버를 백그라운드 스레드로 띄우고 실제 HTTP 요청으로 검증한다.
API 키 환경변수 없이(provider=none/fallback) 동작해야 한다.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

import serve


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module", autouse=True)
def _no_api_keys():
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
        if key in os.environ:
            del os.environ[key]


@pytest.fixture(scope="module")
def server():
    # 테스트 동안 별도 파이프라인 인스턴스를 쓰도록 모듈 상태 초기화
    serve._pipeline = None
    port = _free_port()
    httpd = serve.ThreadingHTTPServer(("127.0.0.1", port), serve.Handler)

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"

    # 서버가 응답 가능해질 때까지 대기
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base_url + "/", timeout=2)
            break
        except Exception:
            time.sleep(0.2)

    yield base_url

    httpd.shutdown()
    httpd.server_close()


def _post_json(url: str, payload: dict, timeout: float = 30.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class TestServeIndex:
    def test_get_root_returns_200(self, server):
        with urllib.request.urlopen(server + "/", timeout=10) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "<html" in body.lower()
            assert "HydroLaw" in body

    def test_path_traversal_blocked(self, server):
        req = urllib.request.Request(server + "/../serve.py")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                body = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read().decode("utf-8", errors="ignore")
        # 서빙되지 않거나(404) 금지(403)되어야 하고, 서버 소스코드가 노출되면 안 된다.
        assert status in (403, 404)
        assert "HydroLawPipeline" not in body


class TestApiQuery:
    def test_query_carwash_bod_returns_mgl_table(self, server):
        status, data = _post_json(
            server + "/api/query",
            {"query": "세차장을 운영 중인데 BOD 기준이 궁금합니다"},
        )
        assert status == 200
        assert "answer" in data and isinstance(data["answer"], str)
        assert data["answer"].strip()
        assert "disclaimer" in data
        assert "used_placeholder_data" in data
        assert "mg/L" in data["answer"]

    def test_empty_query_handled_gracefully(self, server):
        status, data = _post_json(server + "/api/query", {"query": ""})
        assert status == 400
        assert "error" in data

    def test_unknown_route_404(self, server):
        req = urllib.request.Request(server + "/api/does-not-exist", method="POST", data=b"{}")
        try:
            urllib.request.urlopen(req, timeout=10)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 404
