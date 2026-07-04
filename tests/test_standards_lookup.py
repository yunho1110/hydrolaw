"""배출허용기준 수치 정확 조회 테스트 (하이브리드 핵심).

- 40건 전수: 각 JSON 행이 조회되는지, 반환값==원본인지
- 조합별: 오염물질×지역×규모 모든 순열에서 정확도 검증
- 별칭 인식: 한글/영문/약자 동작
- COD→TOC 전환: 안내 메시지 생성
- 음성 케이스: 페놀(데이터 없음)
- 부분정보: 지역 미상→전체 행 반환
"""
import json
from itertools import product

import pytest

from src.standards_lookup import StandardsLookup, format_matches_table


@pytest.fixture
def lookup():
    """테스트 lookup 인스턴스."""
    return StandardsLookup("./data/emission_standards.json")


class TestLookupBasic:
    """기본 조회 기능."""

    def test_lookup_loads_data(self, lookup):
        """데이터 로드 성공."""
        assert lookup.available, "표준 데이터 로드 실패"
        assert lookup._data is not None

    def test_lookup_meta(self, lookup):
        """메타데이터 접근."""
        meta = lookup.meta
        assert meta.get("source")
        assert meta.get("verified_date")

    def test_lookup_is_not_dummy(self, lookup):
        """실데이터는 is_dummy=false."""
        assert lookup.is_dummy is False, "data/ 데이터는 is_dummy=false여야 함"


class TestStandardsLookupAccuracy:
    """40건 표준에 대한 전수 정확도 검증."""

    def test_all_40_standards_queryable(self, lookup, emission_standards_data):
        """각 표준이 적절한 질의로 조회되는지."""
        standards = emission_standards_data["standards"]
        assert len(standards) == 40

        for i, std in enumerate(standards):
            poll = std["pollutant"]
            region = std["region"]
            size = std["size"]

            # 명시적 질의: "BOD 청정지역 2000㎥ 이상"
            q = f"{poll} {region} {size}"
            result = lookup.lookup(q)

            assert result.matches, (
                f"표준[{i}] 조회 실패: {std}. "
                f"질의='{q}'에서 결과 없음"
            )

            # 반환된 matches에서 이 항목을 찾기
            found = False
            for m in result.matches:
                if (
                    m["pollutant"] == poll
                    and m["region"] == region
                    and m["size"] == size
                ):
                    found = True
                    # 수치도 정확히 일치하는지
                    assert m["limit"] == std["limit"], (
                        f"표준[{i}] 수치 불일치: "
                        f"JSON={std['limit']}, 조회={m['limit']}"
                    )
                    assert m["unit"] == "mg/L"
                    break

            assert found, (
                f"표준[{i}] {std}가 조회 결과에 없음. "
                f"matches={result.matches}"
            )

    def test_all_standards_match_json_exactly(self, lookup, emission_standards_data):
        """전체 조회 결과가 JSON 그대로인지 (모든 오염물질, 모든 지역 필터 없이)."""
        standards = emission_standards_data["standards"]
        # 지역 명시 없이 질의하면 지역 필터가 활성화되지 않음
        q = "BOD TOC SS T-N T-P"
        result = lookup.lookup(q)

        # matches 개수 확인 - 지역/규모 미명시면 전체 40행 반환
        assert len(result.matches) == len(standards), (
            f"조회 결과 개수 불일치: JSON={len(standards)}, 결과={len(result.matches)}"
        )

        # 각 JSON 행이 정확히 포함되는지
        json_records = [
            (s["pollutant"], s["region"], s["size"], s["limit"])
            for s in standards
        ]
        result_records = [
            (m["pollutant"], m["region"], m["size"], m["limit"])
            for m in result.matches
        ]

        for rec in json_records:
            assert rec in result_records, f"JSON 항목이 결과에 없음: {rec}"


class TestAliasRecognition:
    """별칭 인식 테스트."""

    def test_korean_pollutant_aliases(self, lookup):
        """한글 오염물질 별칭."""
        queries = [
            ("생물화학적산소요구량", "BOD"),
            ("총유기탄소", "TOC"),
            ("부유물질", "SS"),
            ("총질소", "T-N"),
            ("총인", "T-P"),
        ]
        for q, expected_poll in queries:
            result = lookup.lookup(q + " 청정지역")
            assert result.detected_pollutants, f"'{q}' 미인식"
            assert expected_poll in result.detected_pollutants, (
                f"'{q}'가 '{expected_poll}'로 변환되지 않음: "
                f"detected={result.detected_pollutants}"
            )

    def test_english_aliases(self, lookup):
        """영문 오염물질 별칭."""
        result = lookup.lookup("ss 청정지역")
        assert "SS" in result.detected_pollutants

    def test_region_korean_aliases(self, lookup):
        """지역 별칭."""
        q1 = lookup.lookup("BOD 청정")
        assert q1.detected_region == "청정지역"

        q2 = lookup.lookup("BOD 가 지역")
        assert q2.detected_region == "가지역"

    def test_size_aliases(self, lookup):
        """규모 별칭."""
        q1 = lookup.lookup("BOD 2000")
        assert q1.detected_size == "large"

        q2 = lookup.lookup("BOD 미만")
        assert q2.detected_size == "small"

        q3 = lookup.lookup("BOD 이상")
        assert q3.detected_size == "large"


class TestCODtoTOCConversion:
    """COD→TOC 안내 및 변환."""

    def test_cod_query_converts_to_toc(self, lookup):
        """COD 질의는 TOC로 변환되고, 안내 note 생성."""
        result = lookup.lookup("COD 청정지역")
        assert "TOC" in result.detected_pollutants, "COD가 TOC로 변환되지 않음"
        assert any("COD" in n and "TOC" in n for n in result.notes), (
            "COD→TOC 변환 안내 note 없음"
        )

    def test_cod_to_toc_returns_toc_values(self, lookup):
        """COD 질의 결과가 TOC 수치인지."""
        result = lookup.lookup("COD 청정지역 large")
        assert result.matches
        for m in result.matches:
            assert m["pollutant"] == "TOC", f"기대 TOC, 얻음 {m['pollutant']}"


class TestPartialInformation:
    """부분 정보 질의 (지역/규모 미상)."""

    def test_pollutant_only(self, lookup):
        """오염물질만 지정 → 모든 지역/규모."""
        result = lookup.lookup("BOD")
        assert result.matches, "오염물질 단독 조회 실패"
        # BOD는 4지역 × 2규모 = 8개 행
        bod_rows = [m for m in result.matches if m["pollutant"] == "BOD"]
        assert len(bod_rows) == 8, f"BOD는 8행이어야 함, 얻음 {len(bod_rows)}"

    def test_pollutant_and_region(self, lookup):
        """오염물질+지역, 규모 미상."""
        result = lookup.lookup("BOD 청정지역")
        assert result.matches
        # BOD + 청정지역 = 2개 행 (large, small)
        assert len(result.matches) == 2, f"BOD+청정지역은 2행, 얻음 {len(result.matches)}"
        for m in result.matches:
            assert m["region"] == "청정지역"

    def test_region_only_no_numeric_intent(self, lookup):
        """지역만 지정 (오염물질 없음) → 수치형 아님, matches 비움."""
        result = lookup.lookup("청정지역에서는?")
        assert not result.is_numeric_intent, "오염물질 없으면 numeric_intent=False"
        assert not result.matches, "오염물질 없으면 matches 비워야 함"


class TestNegativeCases:
    """음성 케이스: 데이터에 없는 항목."""

    def test_phenol_not_in_data(self, lookup):
        """페놀은 JSON에 없음."""
        result = lookup.lookup("페놀 기준")
        assert result.is_numeric_intent, "페놀 질의는 numeric_intent=true"
        assert not result.matches, "페놀은 데이터에 없어야 함"
        assert any("없습니다" in n or "확인" in n or "미등록" in n for n in result.notes), (
            f"데이터 없음 안내 note 없음. notes={result.notes}"
        )

    def test_unknown_region_skipped(self, lookup):
        """유효하지 않은 지역은 필터링되지 않음 (무시)."""
        result = lookup.lookup("BOD 우주지역")
        # "우주지역"은 인식되지 않음 → region=None → 모든 지역 반환
        assert len(result.matches) == 8  # BOD 모든 지역/규모


class TestFormatMatchesTable:
    """조회 결과를 텍스트 표로 포맷."""

    def test_table_format(self, lookup):
        """표 포맷이 마크다운 파이프 구분인지."""
        result = lookup.lookup("BOD 청정지역")
        table = format_matches_table(result)
        assert "|" in table, "표에 파이프 구분자 없음"
        assert "오염물질" in table, "표 헤더 없음"
        assert "기준값" in table, "표 헤더에 기준값 없음"

    def test_table_no_entries_returns_empty(self, lookup):
        """매치 없음 → 빈 문자열."""
        result = lookup.lookup("페놀")
        result.matches = []
        table = format_matches_table(result)
        assert table == "", "빈 matches는 빈 문자열 반환"


class TestIndustryAliases:
    """업종 별칭 인식 (수치로는 영향 없음, notes만 생성)."""

    def test_industry_detection(self, lookup):
        """업종이 인식되는지."""
        result = lookup.lookup("세차장 BOD 기준")
        assert result.detected_industry == "세차장", "업종 미인식"
        assert any("세차장" in n for n in result.notes), "업종 인식 note 없음"

    def test_industry_aliases(self, lookup):
        """업종 별칭도 인식."""
        result = lookup.lookup("자동차 세차 BOD")
        assert result.detected_industry == "세차장"


class TestLookupWithMissingFile:
    """파일 없음/로드 실패 케이스."""

    def test_missing_file_graceful(self):
        """존재하지 않는 파일 → graceful 실패."""
        lookup = StandardsLookup("/nonexistent/path.json")
        assert not lookup.available
        result = lookup.lookup("BOD")
        assert not result.matches
        assert result.notes  # 에러 메시지는 있음
