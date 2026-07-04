#!/usr/bin/env python3
"""
법령 자동 감지 도구 (HydroLaw-AI 법령 개정 감시)

물환경보전법 시행규칙의 최신 공포일자를 국가법령정보센터 DRF API로 조회해
기준선(tools/law_watch_state.json)과 비교함으로써 개정을 자동 감지한다.

신규 개정이 발견되면 경고를 출력하고 exit code 1을 반환해 CI 파이프라인을 중단시킨다.
재검증 후 --update-state 플래그로 기준선을 갱신한다.

사용법:
  # API 조회 (OC 필수)
  python3 tools/law_watch.py --oc <OC코드>
  LAW_OC=<OC코드> python3 tools/law_watch.py

  # 로컬 XML로 테스트 (네트워크 불필요)
  python3 tools/law_watch.py --mock tools/fixtures/sample_response.xml

  # 재검증 완료 후 기준선 갱신
  python3 tools/law_watch.py --oc <OC코드> --update-state

OC 발급:
  1. https://open.law.go.kr 접속
  2. 회원가입/로그인 (이메일 기반)
  3. API 키 발급 페이지에서 OC 코드(OpenAPI Certification) 복사
  4. 환경변수 또는 --oc 인자로 제공

법령 정보:
  - 조회 대상: 물환경보전법 시행규칙
  - 기준선: 환경부령 제1184호, 2025-08-07 (현재 확인된 최신 개정)
  - 별표 13 (수질오염물질 배출허용기준)은 현재 2021. 12. 10.부터 유지 중

참고: 이 스크립트는 감지·경고만 수행하며, data/emission_standards.json을 자동 수정하지 않는다.
"""

import sys
import json
import os
import argparse
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

# law.go.kr는 Referer/User-Agent 헤더가 없는 요청을
# "필수입력요소 검증 실패"로 거부하므로 반드시 포함해야 한다.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (HydroLaw-AI law-watch)",
    "Referer": "https://open.law.go.kr/",
}


# 기준선 파일 경로
STATE_FILE = Path(__file__).parent / "law_watch_state.json"

# 기본 기준선 (환경부령 제1184호, 2025-08-07)
DEFAULT_STATE = {
    "last_checked": "2026-07-04T00:00:00",
    "last_enforcement_number": "환경부령 제1184호",
    "last_enforcement_date": "2025-08-07",
    "law_name": "물환경보전법 시행규칙"
}


def load_state():
    """기준선 파일 로드, 없으면 기본값으로 초기화"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"경고: {STATE_FILE} 파일이 손상됨. 기본값으로 복구합니다.", file=sys.stderr)
            return DEFAULT_STATE.copy()
    return DEFAULT_STATE.copy()


def save_state(state):
    """기준선 파일 저장"""
    state["last_checked"] = datetime.now().isoformat()
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_law_info_from_api(oc):
    """
    국가법령정보센터 DRF API로 물환경보전법 시행규칙 조회

    Returns:
        (enforcement_number, enforcement_date) 튜플 또는 (None, None)
    """
    url = "https://www.law.go.kr/DRF/lawSearch.do"
    params = {
        "OC": oc,
        "target": "law",
        "type": "XML",
        "query": "물환경보전법 시행규칙"
    }

    query_string = urlencode(params)
    full_url = f"{url}?{query_string}"

    try:
        req = Request(full_url, headers=REQUEST_HEADERS)
        with urlopen(req, timeout=10) as response:
            xml_data = response.read()
            return parse_law_response(xml_data)
    except HTTPError as e:
        print(f"API 오류 (HTTP {e.code}): {e.reason}", file=sys.stderr)
        if e.code == 401:
            print("인증 실패: OC 코드가 유효하지 않습니다.", file=sys.stderr)
        return None, None
    except URLError as e:
        print(f"네트워크 오류: {e.reason}", file=sys.stderr)
        return None, None
    except Exception as e:
        print(f"예기치 않은 오류: {e}", file=sys.stderr)
        return None, None


def parse_law_response(xml_data):
    """
    법령정보센터 DRF 응답 XML 파싱

    실제 DRF 응답 구조 (2026-07 확인, 한글 태그):
    <LawSearch>
        <law id="1">
            <법령명한글><![CDATA[물환경보전법 시행규칙]]></법령명한글>
            <공포일자>20260622</공포일자>
            <공포번호>00042</공포번호>
            <법령구분명>기후에너지환경부령</법령구분명>
            <제개정구분명>타법개정</제개정구분명>
            ...
        </law>
    </LawSearch>

    Returns:
        (enforcement_number, enforcement_date) 또는 (None, None)
    """
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        print(f"XML 파싱 오류: {e}", file=sys.stderr)
        return None, None

    best = None  # (law_no, date)
    for law in root.iter('law'):
        name = (law.findtext('법령명한글') or '').strip()
        date_raw = (law.findtext('공포일자') or '').strip()
        no_raw = (law.findtext('공포번호') or '').strip()
        kind = (law.findtext('법령구분명') or '').strip() or '부령'
        rev_type = (law.findtext('제개정구분명') or '').strip()

        if '물환경보전법 시행규칙' not in name or not date_raw:
            continue

        # 공포일자 YYYYMMDD → YYYY-MM-DD (state 파일 형식과 통일)
        if len(date_raw) == 8 and date_raw.isdigit():
            date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        else:
            date = date_raw

        try:
            law_no = f"{kind} 제{int(no_raw)}호"
        except ValueError:
            law_no = f"{kind} {no_raw}".strip()
        if rev_type:
            law_no += f" ({rev_type})"

        if best is None or date > best[1]:
            best = (law_no, date)

    if best:
        return best
    return None, None


def check_update(current_state, new_law_no, new_date):
    """
    기준선과 비교해 개정 유무 판단

    Returns:
        (has_update: bool, message: str)
    """
    if not new_law_no or not new_date:
        return False, "API에서 법령 정보를 가져올 수 없습니다."

    last_no = current_state.get("last_enforcement_number", "")
    last_date = current_state.get("last_enforcement_date", "")

    # 공포일자 비교 (날짜 형식: YYYY-MM-DD)
    if new_date > last_date:
        return True, f"신규 개정 감지: {new_law_no} ({new_date})"

    if new_date == last_date and new_law_no != last_no:
        return True, f"공포번호 변경: {new_law_no} (기존: {last_no})"

    return False, f"최신 상태: {new_law_no} ({new_date})"


def print_update_warning(law_no, new_date, old_date):
    """개정 감지 시 경고 메시지 출력"""
    print("\n" + "="*70, file=sys.stderr)
    print("⚠️  물환경보전법 시행규칙 개정 감지!", file=sys.stderr)
    print("="*70, file=sys.stderr)
    print(f"\n법령명: 물환경보전법 시행규칙", file=sys.stderr)
    print(f"공포번호: {law_no}", file=sys.stderr)
    print(f"공포일자: {new_date} (기준: {old_date})", file=sys.stderr)
    print(f"\n조치사항:", file=sys.stderr)
    print(f"  1. data/emission_standards.json 및 data/laws/ 재검증", file=sys.stderr)
    print(f"     - 별표 13 (수질오염물질 배출허용기준) 수치 확인", file=sys.stderr)
    print(f"     - 최신 법령 다운로드: https://www.law.go.kr/LSW/lsInfoP.do?lsId=007575", file=sys.stderr)
    print(f"  2. 변경사항 확인 후 JSON 수치 갱신", file=sys.stderr)
    print(f"  3. 재검증 완료 후 다음 실행:", file=sys.stderr)
    print(f"     python3 tools/law_watch.py --update-state", file=sys.stderr)
    print("\n" + "="*70 + "\n", file=sys.stderr)


def print_oc_guide():
    """OC 발급 안내"""
    print("\n" + "="*70, file=sys.stderr)
    print("API 인증 코드(OC) 필요", file=sys.stderr)
    print("="*70, file=sys.stderr)
    print("\n1. https://open.law.go.kr 접속", file=sys.stderr)
    print("2. 회원가입/로그인 (이메일 기반)", file=sys.stderr)
    print("3. 'API 인증 키' 또는 '발급 현황' 페이지에서 OC 코드 복사", file=sys.stderr)
    print("4. 다음 중 하나로 제공:", file=sys.stderr)
    print("   - 환경변수: LAW_OC=<OC코드> python3 tools/law_watch.py", file=sys.stderr)
    print("   - 명령행 인자: python3 tools/law_watch.py --oc <OC코드>", file=sys.stderr)
    print("\n수동 확인 링크:", file=sys.stderr)
    print("https://www.law.go.kr/LSW/lsInfoP.do?lsId=007575", file=sys.stderr)
    print("\n" + "="*70 + "\n", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="물환경보전법 시행규칙 개정 자동 감지",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예제:
  python3 tools/law_watch.py --oc <OC코드>
  LAW_OC=<OC코드> python3 tools/law_watch.py
  python3 tools/law_watch.py --mock tools/fixtures/sample_response.xml
  python3 tools/law_watch.py --oc <OC코드> --update-state
        """
    )
    parser.add_argument('--oc', help='API 인증 코드(OpenAPI Certification)')
    parser.add_argument('--mock', help='로컬 XML 파일로 테스트 (네트워크 미사용)')
    parser.add_argument('--update-state', action='store_true',
                       help='재검증 완료 후 기준선 갱신')

    args = parser.parse_args()

    # 기준선 로드
    state = load_state()

    # API 또는 Mock으로 최신 정보 조회
    if args.mock:
        # Mock 모드: 로컬 파일 파싱
        try:
            with open(args.mock, 'rb') as f:
                new_law_no, new_date = parse_law_response(f.read())
        except FileNotFoundError:
            print(f"오류: Mock 파일 없음 {args.mock}", file=sys.stderr)
            sys.exit(2)
    else:
        # API 모드: OC 필요
        oc = args.oc or os.environ.get('LAW_OC')
        if not oc:
            print_oc_guide()
            sys.exit(2)

        new_law_no, new_date = fetch_law_info_from_api(oc)
        if not new_law_no:
            # API 실패 시에도 안내 제공
            print("\nAPI 조회 실패. 수동 확인:", file=sys.stderr)
            print("https://www.law.go.kr/LSW/lsInfoP.do?lsId=007575", file=sys.stderr)
            sys.exit(2)

    # 개정 여부 확인
    has_update, message = check_update(state, new_law_no, new_date)

    if has_update:
        print_update_warning(new_law_no, new_date, state.get("last_enforcement_date", ""))

        # --update-state 플래그가 있으면 기준선 갱신
        if args.update_state:
            state["last_enforcement_number"] = new_law_no
            state["last_enforcement_date"] = new_date
            save_state(state)
            print(f"기준선 갱신 완료: {new_law_no} ({new_date})", file=sys.stderr)
            sys.exit(0)
        else:
            # 업데이트가 있는데 --update-state 없으면 CI 실패
            sys.exit(1)
    else:
        print(message)

        # --update-state 플래그가 있으면 마지막 확인 시간 갱신
        if args.update_state:
            save_state(state)
            print(f"마지막 확인 시간 갱신: {state['last_checked']}")

        sys.exit(0)


if __name__ == '__main__':
    main()
