"""배출허용기준 수치의 정확 조회 (환각 0% 경로).

절대 원칙: 수치는 오직 emission_standards.json 의 정확 매칭 결과만 사용한다.
LLM 은 이 모듈의 결과 수치를 절대 생성·보정·반올림하지 못한다.

질의에서 다음 축을 키워드 매칭으로 추출:
  - 오염물질: BOD / COD / TOC / SS (+ 한글 별칭)
  - 지역: 청정 / 가 / 나 / 특례 지역
  - 규모: 2,000㎥ 이상(large) / 미만(small)
  - 업종: industry_aliases (매칭 시 notes 로 안내만; 업종→수치 매핑은 데이터에 없으므로)

부분 정보만 있으면(예: 지역 미상) 그 축은 필터하지 않아 전체를 반환 → 표로 노출 가능.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


# ---- 오염물질 별칭 (질의 텍스트 → 표준 pollutant 키) ----
# COD 는 현행 물환경보전법이 TOC 로 전환되어, COD 질의는 TOC 로 안내하고 그 사실을 명시한다.
POLLUTANT_ALIASES: dict[str, list[str]] = {
    "BOD": ["bod", "생물화학적산소요구량", "생물화학적 산소 요구량", "생화학적산소요구량"],
    "TOC": ["toc", "총유기탄소", "총 유기탄소", "총유기탄소량"],
    "COD": ["cod", "화학적산소요구량", "화학적 산소 요구량"],
    "SS": ["ss", "부유물질", "부유 물질", "suspended solids"],
    "T-N": ["t-n", "tn", "총질소", "총 질소"],
    "T-P": ["t-p", "tp", "총인", "총 인"],
    "n-헥산": ["헥산", "노말헥산", "n-헥산", "n헥산", "광유류", "동식물유지류"],
    "페놀": ["페놀", "phenol"],
    "색도": ["색도"],
    "총대장균군수": ["대장균", "총대장균", "총대장균군", "대장균군"],
}

# ---- 지역 별칭 ----
REGION_ALIASES: dict[str, list[str]] = {
    "청정지역": ["청정지역", "청정 지역", "청정"],
    "가지역": ["가지역", "가 지역", "'가'지역", "가 구역"],
    "나지역": ["나지역", "나 지역", "'나'지역", "나 구역"],
    "특례지역": ["특례지역", "특례 지역", "특례"],
}

# ---- 규모 별칭 ----
SIZE_ALIASES: dict[str, list[str]] = {
    "large": ["2000", "2,000", "이상", "대규모", "large", "2천"],
    "small": ["미만", "이하", "소규모", "small", "2000㎥ 미만"],
}


@dataclass
class LookupResult:
    matches: list[dict] = field(default_factory=list)
    is_dummy: bool = True
    source: str = ""
    notes: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    # 질의에서 어떤 축을 인식했는지 (프롬프트/표 헤더/디버깅용)
    detected_pollutants: list[str] = field(default_factory=list)
    detected_region: str | None = None
    detected_size: str | None = None
    detected_industry: str | None = None

    @property
    def is_numeric_intent(self) -> bool:
        """수치형(배출허용기준) 질문으로 볼 수 있는가."""
        return bool(self.detected_pollutants)


class StandardsLookup:
    def __init__(self, standards_path: str):
        self.path = standards_path
        self._data: dict | None = None
        self._load_error: str | None = None
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._load_error = f"배출허용기준 데이터 파일 없음: {self.path}"
            self._data = None
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self._load_error = f"배출허용기준 데이터 로드 실패: {e}"
            self._data = None

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._data is not None

    @property
    def meta(self) -> dict:
        if not self._data:
            return {}
        return self._data.get("meta", {}) or {}

    @property
    def is_dummy(self) -> bool:
        # 데이터 없거나 meta 불명이면 보수적으로 dummy 취급(경고 노출).
        return bool(self.meta.get("is_dummy", True))

    @property
    def industry_aliases(self) -> dict:
        if not self._data:
            return {}
        return self._data.get("industry_aliases", {}) or {}

    # ------------------------------------------------------------------
    def _detect_pollutants(self, q: str) -> list[str]:
        low = q.lower()
        found: list[str] = []
        for canon, aliases in POLLUTANT_ALIASES.items():
            for a in aliases:
                if a.lower() in low:
                    if canon not in found:
                        found.append(canon)
                    break
        return found

    def _detect_region(self, q: str) -> str | None:
        low = q.lower()
        for canon, aliases in REGION_ALIASES.items():
            for a in aliases:
                if a.lower() in low:
                    return canon
        return None

    def _detect_size(self, q: str) -> str | None:
        # "이상"과 "미만"이 둘 다 있으면 미만(small) 우선하지 않고 명시된 숫자문맥으로 판단하기 어려우므로
        # 단순히 "미만/이하"가 있으면 small, 없고 "이상/대규모"면 large.
        low = q.lower()
        for a in SIZE_ALIASES["small"]:
            if a in low:
                return "small"
        for a in SIZE_ALIASES["large"]:
            if a in low:
                return "large"
        return None

    def _detect_industry(self, q: str) -> str | None:
        for industry, aliases in self.industry_aliases.items():
            if industry in q:
                return industry
            for a in aliases:
                if a in q:
                    return industry
        return None

    # ------------------------------------------------------------------
    def lookup(self, query: str) -> LookupResult:
        q = (query or "").strip()
        source = self.meta.get("source", os.path.basename(self.path))
        res = LookupResult(
            matches=[],
            is_dummy=self.is_dummy,
            source=source,
            notes=[],
            meta=self.meta,
        )

        if not self.available:
            res.notes.append(
                self._load_error or "배출허용기준 데이터를 사용할 수 없습니다."
            )
            return res

        pollutants = self._detect_pollutants(q)
        region = self._detect_region(q)
        size = self._detect_size(q)
        industry = self._detect_industry(q)

        # COD → TOC 안내 (현행 기준 전환)
        query_pollutants = list(pollutants)
        if "COD" in query_pollutants:
            res.notes.append(
                "COD(화학적산소요구량) 항목은 현행 물환경보전법상 TOC(총유기탄소)로 "
                "관리 지표가 전환되었습니다. TOC 기준으로 안내합니다."
            )
            # COD 를 TOC 로 치환(중복 방지)
            query_pollutants = [
                "TOC" if p == "COD" else p for p in query_pollutants
            ]
            dedup: list[str] = []
            for p in query_pollutants:
                if p not in dedup:
                    dedup.append(p)
            query_pollutants = dedup

        res.detected_pollutants = query_pollutants
        res.detected_region = region
        res.detected_size = size
        res.detected_industry = industry

        if industry:
            res.notes.append(
                f"'{industry}' 업종으로 인식했습니다. 단, 배출허용기준은 업종이 아니라 "
                "지역·사업장 규모에 따라 정해집니다(폐수배출량 기준). 아래 표는 지역/규모별 기준입니다."
            )

        standards = self._data.get("standards", []) or []

        # 오염물질 축이 하나도 감지되지 않으면 수치형 질의로 보지 않는다.
        # (지역/규모만 언급됐다 해서 전체 기준표를 쏟아내지 않도록 방어)
        # → 이 경우 matches 는 비워 두고, 맥락 설명은 RAG 가 담당한다.
        if not query_pollutants:
            if region or size:
                res.notes.append(
                    "특정 오염물질(BOD/TOC/SS 등)이 지정되지 않아 수치표는 생략했습니다. "
                    "오염물질명을 함께 알려주시면 정확한 기준을 표로 안내합니다."
                )
            return res

        # ---- 축별 필터. 각 축은 정보가 있을 때만 필터한다(부분정보 대응). ----
        def _match(row: dict) -> bool:
            if query_pollutants and row.get("pollutant") not in query_pollutants:
                return False
            if region and row.get("region") != region:
                return False
            if size and row.get("size") != size:
                return False
            return True

        matches = [row for row in standards if _match(row)]

        # 오염물질은 감지했으나 지역/규모 미상이면 그 축 전체가 나온다 → 안내 note.
        if query_pollutants and not region:
            res.notes.append(
                "지역(청정/가/나/특례)이 명시되지 않아 전체 지역 기준을 함께 표시합니다."
            )
        if query_pollutants and not size:
            res.notes.append(
                "사업장 규모(1일 폐수배출량 2,000㎥ 이상/미만)가 명시되지 않아 "
                "전체 규모 기준을 함께 표시합니다."
            )

        res.matches = matches

        if query_pollutants and not matches:
            res.notes.append(
                "질의한 오염물질/조건에 해당하는 배출허용기준이 데이터에 없습니다. "
                "law.go.kr(국가법령정보센터)에서 반드시 확인하세요."
            )

        return res

    # ------------------------------------------------------------------
    def region_types(self) -> dict:
        if not self._data:
            return {}
        return self._data.get("region_types", {}) or {}

    def size_categories(self) -> dict:
        if not self._data:
            return {}
        return self._data.get("size_categories", {}) or {}


def format_matches_table(result: LookupResult) -> str:
    """조회 결과를 사람이 읽는 표(텍스트)로. 수치는 여기서만 문자열화된다."""
    if not result.matches:
        return ""
    rows = result.matches
    # 헤더
    header = "| 오염물질 | 지역 | 규모 | 기준값 | 단위 |"
    sep = "|---|---|---|---|---|"
    lines = [header, sep]
    size_labels = {"large": "2,000㎥ 이상", "small": "2,000㎥ 미만"}
    for r in rows:
        lines.append(
            "| {p} | {region} | {size} | {limit} | {unit} |".format(
                p=r.get("pollutant", "-"),
                region=r.get("region", "-"),
                size=size_labels.get(r.get("size", ""), r.get("size", "-")),
                limit=r.get("limit", "-"),
                unit=r.get("unit", "-"),
            )
        )
    return "\n".join(lines)
