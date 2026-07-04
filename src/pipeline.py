"""하이브리드 오케스트레이션.

흐름:
  ① standards_lookup  — 질의 → 구조화 JSON 수치 정확 조회 (환각 0%)
  ② vector_store.search — RAG 로 법령 맥락 검색
  ③ generator.generate — 수치 블록/맥락 블록 분리 주입 + post-hoc guard, 실패 시 fallback
  ④ 답변 말미에 근거(법령명·데이터 기준일·개정번호) + 출처 파일 목록 표기

run.py 계약:
  pipeline.build_index(reset) -> int
  result = pipeline.answer(query)
    result.answer / result.used_placeholder_data / result.disclaimer
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import AppConfig
from .standards_lookup import StandardsLookup
from .vector_store import VectorStore
from .generator import Generator


@dataclass
class AnswerResult:
    answer: str
    used_placeholder_data: bool
    disclaimer: str
    # 부가 정보(테스트/디버깅용, run.py 는 사용 안 함)
    notes: list[str] = field(default_factory=list)
    backend: str = ""
    used_llm: bool = False
    sources: list[str] = field(default_factory=list)


class HydroLawPipeline:
    def __init__(self, config: AppConfig):
        self.config = config
        self.lookup = StandardsLookup(config.emission_standards_file)
        self.store = VectorStore(config)
        self.generator = Generator(config)

    # ------------------------------------------------------------------
    def build_index(self, reset: bool = True) -> int:
        return self.store.build_index(reset=reset)

    # ------------------------------------------------------------------
    def answer(self, query: str) -> AnswerResult:
        # ① 수치 정확 조회
        lookup_res = self.lookup.lookup(query)

        # ② RAG 검색 (실패해도 죽지 않도록 VectorStore 가 방어)
        try:
            rag = self.store.search(query, self.config.retrieval.top_k)
        except Exception:
            rag = []

        # ③ 생성 (fence + fallback)
        gen = self.generator.generate(query, lookup_res, rag)

        # ④ 근거/출처 표기 조립
        answer_text = self._compose(query, gen, lookup_res, rag)

        sources = self._collect_sources(lookup_res, rag)
        notes = list(lookup_res.notes) + list(gen.notes)

        return AnswerResult(
            answer=answer_text,
            used_placeholder_data=bool(lookup_res.is_dummy),
            disclaimer=self.config.disclaimer,
            notes=notes,
            backend=self.store.backend_name,
            used_llm=gen.used_llm,
            sources=sources,
        )

    # ------------------------------------------------------------------
    def _compose(self, query, gen, lookup_res, rag) -> str:
        parts = [gen.text.rstrip()]

        # 수치형 질의인데 조회 0건이면 명시적으로 경고(추정 방지 재강조)
        if lookup_res.is_numeric_intent and not lookup_res.matches:
            parts.append(
                "\n※ 질의하신 배출허용기준 수치는 현재 데이터에서 확인되지 않았습니다. "
                "국가법령정보센터(law.go.kr)에서 반드시 확인하세요. (본 도구는 수치를 추정하지 않습니다.)"
            )

        # 근거 표기
        meta = lookup_res.meta or {}
        basis_bits = []
        if meta.get("source"):
            basis_bits.append(f"기준 법령: {meta['source']}")
        if meta.get("revision"):
            basis_bits.append(f"개정: {meta['revision']}")
        if meta.get("verified_date"):
            basis_bits.append(f"데이터 기준일: {meta['verified_date']}")
        if meta.get("law_url"):
            basis_bits.append(f"출처 URL: {meta['law_url']}")

        sources = self._collect_sources(lookup_res, rag)

        footer_lines = ["\n─────────────────────────────", "[근거 및 출처]"]
        if basis_bits:
            for b in basis_bits:
                footer_lines.append(f"  · {b}")
        if sources:
            footer_lines.append("  · 참조 문서: " + ", ".join(sources))
        if lookup_res.is_dummy:
            footer_lines.append(
                "  · ⚠️ 현재 배출허용기준 데이터는 예시(더미)입니다. 실제 값은 law.go.kr에서 확인하세요."
            )
        parts.append("\n".join(footer_lines))

        return "\n".join(parts).strip()

    def _collect_sources(self, lookup_res, rag) -> list[str]:
        srcs: list[str] = []
        for (_t, source, _sc) in rag:
            if source and source not in srcs:
                srcs.append(source)
        return srcs
