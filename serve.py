"""HydroLaw-AI 웹 서버 (표준 라이브러리 전용, 의존성 0 원칙).

역할: src/ 파이프라인의 "소비자"일 뿐이다. 여기서 수치를 가공/계산/보정하지 않는다.
pipeline.answer(query) 가 반환한 answer 텍스트를 그대로 렌더링만 한다.

사용 예:
    python3 serve.py
    python3 serve.py --port 8765
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.config import AppConfig
from src.pipeline import HydroLawPipeline

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# 전역 파이프라인 (기동 시 1회 생성)
_pipeline: HydroLawPipeline | None = None


def get_pipeline() -> HydroLawPipeline:
    global _pipeline
    if _pipeline is None:
        config = AppConfig.load(os.environ.get("HYDROLAW_CONFIG", "config.yaml"))
        _pipeline = HydroLawPipeline(config)
        # 인덱스가 없으면(최초 실행) 자동 구축. 이미 있으면 재구축하지 않는다.
        vs_path = config.vector_store_path
        has_index = os.path.isdir(vs_path) and len(os.listdir(vs_path)) > 0
        if not has_index:
            try:
                n = _pipeline.build_index(reset=True)
                print(f"[serve] 인덱스 자동 구축 완료: {n}개 청크")
            except Exception as e:  # noqa: BLE001 - 서버가 죽지 않도록 방어
                print(f"[serve] 인덱스 구축 경고: {e} (검색 없이 계속 진행)")
    return _pipeline


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "HydroLawAI/1.0"

    # 기본 로그를 조용히(원하면 access 로그 형태로 커스텀)
    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[serve] " + (fmt % args) + "\n")

    # ------------------------------------------------------------------
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rel_path: str) -> None:
        """web/ 하위 파일만 서빙. 경로 탈출(../ 등) 방지."""
        rel_path = rel_path.lstrip("/")
        if rel_path == "":
            rel_path = "index.html"

        target = os.path.normpath(os.path.join(WEB_DIR, rel_path))
        web_root = os.path.normpath(WEB_DIR)
        # 경로 탈출 방지: 반드시 web_root 하위여야 함
        if not (target == web_root or target.startswith(web_root + os.sep)):
            self._send_json(403, {"error": "forbidden"})
            return

        if not os.path.isfile(target):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"404 Not Found")
            return

        ext = os.path.splitext(target)[1].lower()
        content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(target, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_file("index.html")
        else:
            self._send_file(path)

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/api/query":
            self._send_json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "잘못된 JSON 요청입니다."})
                return

            query = str(body.get("query", "") or "").strip()
            if not query:
                self._send_json(400, {"error": "질문(query)을 입력해 주세요."})
                return

            pipeline = get_pipeline()
            result = pipeline.answer(query)

            self._send_json(
                200,
                {
                    "answer": result.answer,
                    "disclaimer": result.disclaimer,
                    "used_placeholder_data": bool(result.used_placeholder_data),
                },
            )
        except Exception as e:  # noqa: BLE001 - 500 대신 안내 메시지로 방어
            self._send_json(
                200,
                {
                    "answer": (
                        "죄송합니다. 답변을 생성하는 중 오류가 발생했습니다. "
                        "잠시 후 다시 시도해 주시거나, 국가법령정보센터(law.go.kr)에서 "
                        "직접 확인해 주세요.\n\n(오류 내용: "
                        f"{type(e).__name__}: {e})"
                    ),
                    "disclaimer": (
                        "본 결과는 법률 자문이 아니며 참고용 정보입니다. "
                        "실제 적용 전 반드시 관할 지자체 담당 부서 또는 환경 전문가의 확인을 거치시기 바랍니다."
                    ),
                    "used_placeholder_data": True,
                    "error": True,
                },
            )


def _lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="HydroLaw-AI 웹 서버 (표준 라이브러리 전용)")
    parser.add_argument("--port", type=int, default=8765, help="서버 포트 (기본 8765)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="바인드 호스트 (기본 0.0.0.0)")
    args = parser.parse_args()

    # 기동 시 파이프라인 1회 생성(인덱스 자동 구축 포함)
    print("[serve] HydroLaw-AI 파이프라인을 초기화합니다...")
    get_pipeline()
    print("[serve] 파이프라인 준비 완료.")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)

    lan_ip = _lan_ip()
    print("=" * 60)
    print("HydroLaw-AI 웹 서버가 시작되었습니다.")
    print(f"  로컬 접속:     http://localhost:{args.port}")
    print(f"  로컬 접속:     http://127.0.0.1:{args.port}")
    if lan_ip:
        print(f"  같은 네트워크: http://{lan_ip}:{args.port}")
    print("  (Ctrl+C 로 종료)")
    print("=" * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 서버를 종료합니다.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
