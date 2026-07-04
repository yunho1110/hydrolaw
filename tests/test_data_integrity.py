"""데이터 무결성 및 구조 검증.

- JSON 스키마 검증 (키 존재, 타입, 범위)
- 중복 조합 확인 (同 pollutant+region+size 2회 이상)
- laws 마크다운 frontmatter 검증
- 하이브리드 경계 회귀: laws 본문에 mg/L 기준값 표 없는지
"""
import json
import os
import re
from pathlib import Path

import pytest


def test_emission_standards_json_structure(emission_standards_data):
    """JSON 최상위 구조 검증."""
    assert "meta" in emission_standards_data, "meta 키 누락"
    assert "standards" in emission_standards_data, "standards 키 누락"
    assert "region_types" in emission_standards_data
    assert "size_categories" in emission_standards_data
    assert "industry_aliases" in emission_standards_data


def test_emission_standards_meta(emission_standards_data):
    """meta 블록 검증."""
    meta = emission_standards_data["meta"]
    assert "source" in meta, "meta.source 누락"
    assert "verified_date" in meta, "meta.verified_date 누락"
    assert "is_dummy" in meta, "meta.is_dummy 누락"
    assert isinstance(meta["is_dummy"], bool), "is_dummy는 bool이어야 함"
    # 실데이터는 is_dummy=false, 샘플은 true
    assert meta["is_dummy"] is False, "data/emission_standards.json은 is_dummy=false여야 함"


def test_emission_standards_count(emission_standards_data):
    """표준 데이터는 40건이어야 함."""
    standards = emission_standards_data["standards"]
    assert len(standards) == 40, f"표준이 정확히 40건이어야 함 (현재 {len(standards)}건)"


def test_emission_standards_schema(emission_standards_data):
    """각 표준 항목의 스키마 검증."""
    standards = emission_standards_data["standards"]
    required_keys = {"pollutant", "region", "size", "limit", "unit"}
    for i, s in enumerate(standards):
        assert isinstance(s, dict), f"[{i}] 항목이 dict가 아님"
        missing = required_keys - set(s.keys())
        assert not missing, f"[{i}] 키 누락: {missing}"

        # 타입 검증
        assert isinstance(s["pollutant"], str), f"[{i}] pollutant 타입 오류"
        assert isinstance(s["region"], str), f"[{i}] region 타입 오류"
        assert isinstance(s["size"], str), f"[{i}] size 타입 오류"
        assert isinstance(s["unit"], str), f"[{i}] unit 타입 오류"
        assert isinstance(s["limit"], (int, float)), f"[{i}] limit 타입 오류 (숫자 필요)"

        # limit 양수 검증
        assert s["limit"] > 0, f"[{i}] limit이 양수가 아님: {s['limit']}"

        # 단위는 mg/L
        assert s["unit"] == "mg/L", f"[{i}] unit이 mg/L이 아님: {s['unit']}"


def test_emission_standards_no_duplicates(emission_standards_data):
    """중복 조합(pollutant+region+size) 확인."""
    standards = emission_standards_data["standards"]
    seen = set()
    duplicates = []
    for s in standards:
        key = (s["pollutant"], s["region"], s["size"])
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    assert not duplicates, f"중복 조합 발견: {duplicates}"


def test_emission_standards_valid_regions(emission_standards_data):
    """region이 region_types에 정의된 것인지 확인."""
    standards = emission_standards_data["standards"]
    valid_regions = set(emission_standards_data["region_types"].keys())
    for s in standards:
        assert s["region"] in valid_regions, f"유효하지 않은 region: {s['region']}"


def test_emission_standards_valid_sizes(emission_standards_data):
    """size가 size_categories에 정의된 것인지 확인."""
    standards = emission_standards_data["standards"]
    valid_sizes = set(emission_standards_data["size_categories"].keys())
    for s in standards:
        assert s["size"] in valid_sizes, f"유효하지 않은 size: {s['size']}"


def test_emission_standards_valid_pollutants(emission_standards_data):
    """pollutant가 정의된 오염물질인지 확인."""
    standards = emission_standards_data["standards"]
    # 가능한 오염물질 목록
    valid_pollutants = {"BOD", "TOC", "COD", "SS", "T-N", "T-P", "n-헥산", "페놀", "색도", "총대장균군수"}
    for s in standards:
        assert s["pollutant"] in valid_pollutants, f"유효하지 않은 pollutant: {s['pollutant']}"


def test_laws_files_exist():
    """law 마크다운 파일 존재 확인."""
    laws_dir = Path("./data/laws")
    assert laws_dir.is_dir(), f"laws 디렉터리 없음: {laws_dir}"
    files = sorted(laws_dir.glob("*.md"))
    assert len(files) > 0, "laws/*.md 파일이 없음"


def test_laws_frontmatter_structure():
    """각 법령 마크다운 frontmatter 검증."""
    laws_dir = Path("./data/laws")
    files = sorted(laws_dir.glob("*.md"))
    fm_re = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

    required_fm_keys = {"source_law", "articles", "verified_date", "url"}

    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        m = fm_re.match(content)
        assert m, f"{fpath.name}: frontmatter 없음 (---...--- 형식 필요)"

        fm_block = m.group(1)
        # 간단한 체크: 각 필수 키가 frontmatter에 있는지
        for key in required_fm_keys:
            assert key in fm_block, f"{fpath.name}: frontmatter에 '{key}' 누락"


def test_laws_no_standards_table():
    """laws 본문에 mg/L 배출기준 수치 표가 없는지 검증 (하이브리드 경계 회귀).

    단, 조항번호나 ㎥ 단위 숫자는 허용.
    """
    laws_dir = Path("./data/laws")
    files = sorted(laws_dir.glob("*.md"))
    fm_re = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

    # mg/L 숫자: "30 mg/L", "40mg/L" 등 패턴. 이는 배출기준을 명시하는 것이므로 위반.
    # 단, "조항 제1조" 같은 단순 숫자는 괜찮음.
    standard_pattern = re.compile(
        r"\b(\d+(?:,\d+)?)\s*mg/L\b",  # "30 mg/L" 형식
        re.IGNORECASE
    )

    for fpath in files:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        m = fm_re.search(content)
        if m:
            body = content[m.end():]
        else:
            body = content

        matches = standard_pattern.findall(body)
        assert not matches, (
            f"{fpath.name}: 본문에 mg/L 기준값이 발견됨 (하이브리드 경계 침해): "
            f"{matches}. "
            "배출허용기준 수치는 JSON에서만 가져와야 함."
        )


def test_sample_data_is_dummy():
    """data_sample/emission_standards.json은 is_dummy=true여야 함."""
    sample_path = Path("./data_sample/emission_standards.json")
    if not sample_path.exists():
        pytest.skip("data_sample/emission_standards.json 없음")

    with open(sample_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["meta"]["is_dummy"] is True, "data_sample은 is_dummy=true여야 함"
