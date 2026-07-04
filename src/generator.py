"""LLM 호출 + 수치 조작 방지 Fence + fallback.

핵심 방어:
  1) 프롬프트 분리 주입: [검증된 수치 데이터](lookup) 와 [법령 맥락](RAG) 를 분리.
     시스템 프롬프트에 "수치 블록에 없는 수치 언급 금지, 블록 수치 변형 금지" 명시.
  2) 생성 후 검증(post-hoc guard): 답변의 숫자 토큰을 정규식 추출 →
     허용 집합(주입 수치·단위 + 프롬프트 재료의 숫자: 조항번호·질의 숫자)에 없는
     숫자가 든 문장은 "(수치 확인 불가 — law.go.kr 확인 요망)" 로 교체 + notes 기록.
  3) fallback: provider=none / 키 없음 / SDK 미설치 / 호출 실패 → 예외 없이
     수치표 + RAG 인용 + 안내문 템플릿으로 강등. answer() 는 절대 예외로 죽지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .standards_lookup import LookupResult, format_matches_table


SYSTEM_PROMPT = """당신은 대한민국 물환경 규제 준수를 돕는 전문 안내 어시스턴트입니다.

절대 규칙(위반 시 실패):
1. 배출허용기준 등 '수치'는 오직 아래 [검증된 수치 데이터] 블록에 있는 값만 사용하십시오.
2. 이 블록에 없는 수치(기준값, 농도, 한도 등)를 절대 새로 만들거나 추정하지 마십시오.
3. 블록의 수치를 반올림하거나 변형하지 마십시오. 표에 있는 값을 그대로 인용하십시오.
4. [법령 맥락] 블록의 문서는 해석·절차·행정처분 등 '맥락 설명'에만 사용하고,
   그 안에 등장하는 숫자를 '배출허용기준 수치'로 제시하지 마십시오.
5. 수치 데이터가 비어 있으면 "해당 기준은 데이터에 없으니 law.go.kr에서 확인하세요"라고 안내하십시오.
6. 마지막에 항상 법률 자문이 아님을 짧게 덧붙이십시오.

한국어로, 영세 사업자가 이해하기 쉽게, 근거(법령명·조항)를 함께 설명하십시오."""


@dataclass
class GenerationResult:
    text: str
    used_llm: bool = False
    provider: str = "none"
    notes: list[str] = field(default_factory=list)


# 숫자 토큰: 1,234.5 / 30 / 2000 등. 콤마·소수점 허용.
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 문장 분리(한국어): 마침표/줄바꿈/느낌표/물음표 기준(간단).
_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")


def _norm_num(s: str) -> str:
    return s.replace(",", "").rstrip(".")


def _collect_allowed_numbers(
    lookup: LookupResult, rag_snippets: list[str], query: str
) -> set[str]:
    """답변에 등장해도 되는 숫자 집합.

    - lookup 결과의 limit / 단위 내부 숫자
    - 질의에 등장한 숫자 (예: '2,000㎥')
    - RAG 청크에 등장한 숫자 (조항 번호 등 맥락 숫자) — 단, 이는 '기준 수치'로 쓰이지 않도록
      시스템 프롬프트가 막고, 여기서는 존재 자체를 허용해 문장 삭제 과잉을 방지.
    - 상시 허용: 1~4 자리 조번호/별표 번호 등 흔한 작은 숫자 및 연도.
    """
    allowed: set[str] = set()
    for m in lookup.matches:
        limit = m.get("limit")
        if limit is not None:
            allowed.add(_norm_num(str(limit)))
        unit = str(m.get("unit", ""))
        for n in _NUM_RE.findall(unit):
            allowed.add(_norm_num(n))
    for n in _NUM_RE.findall(query or ""):
        allowed.add(_norm_num(n))
    for snip in rag_snippets:
        for n in _NUM_RE.findall(snip or ""):
            allowed.add(_norm_num(n))
    return allowed


def sanitize_numbers(
    answer: str, lookup: LookupResult, rag_snippets: list[str], query: str
) -> tuple[str, list[str]]:
    """허용 집합에 없는 숫자가 든 문장을 무력화한다. (post-hoc guard)"""
    allowed = _collect_allowed_numbers(lookup, rag_snippets, query)
    notes: list[str] = []
    sentences = _SENT_SPLIT.split(answer)
    out_sentences: list[str] = []
    for sent in sentences:
        nums = _NUM_RE.findall(sent)
        bad = [n for n in nums if _norm_num(n) not in allowed]
        if bad:
            notes.append(
                f"검증되지 않은 수치({', '.join(bad)}) 를 포함한 문장을 안전을 위해 교체했습니다."
            )
            out_sentences.append(
                "(위 항목의 수치는 검증된 데이터에서 확인되지 않았습니다 — "
                "law.go.kr에서 확인 요망)"
            )
        else:
            out_sentences.append(sent)
    return " ".join(s for s in out_sentences if s.strip()), notes


def build_prompt(
    query: str,
    lookup: LookupResult,
    rag_results: list[tuple[str, str, float]],
) -> str:
    """LLM 사용자 프롬프트 조립: 수치 블록과 맥락 블록을 물리적으로 분리."""
    table = format_matches_table(lookup)
    if table:
        num_block = table
    elif lookup.is_numeric_intent:
        num_block = "(질의한 조건에 해당하는 검증된 수치가 데이터에 없음. 수치를 만들지 말 것.)"
    else:
        num_block = "(이 질의는 수치형이 아님. 수치를 새로 언급하지 말 것.)"

    ctx_lines = []
    for i, (text, source, _score) in enumerate(rag_results, 1):
        ctx_lines.append(f"[문서{i}] (출처: {source})\n{text}")
    ctx_block = "\n\n".join(ctx_lines) if ctx_lines else "(관련 법령 문서 없음)"

    notes_block = "\n".join(f"- {n}" for n in lookup.notes) if lookup.notes else "(없음)"

    return f"""[사용자 질의]
{query}

[검증된 수치 데이터]  ← 이 블록의 값만 수치로 사용
{num_block}

[조회 참고 사항]
{notes_block}

[법령 맥락]  ← 해석/절차 설명용. 여기 숫자를 배출허용기준으로 제시 금지
{ctx_block}

위 자료만 근거로, 사용자 질의에 답하십시오."""


class Generator:
    def __init__(self, config):
        self.config = config
        self.provider = config.llm.provider
        self.model = config.llm.model
        self.temperature = config.llm.temperature

    # ------------------------------------------------------------------
    def generate(
        self,
        query: str,
        lookup: LookupResult,
        rag_results: list[tuple[str, str, float]],
    ) -> GenerationResult:
        rag_snippets = [t for (t, _s, _sc) in rag_results]
        prompt = build_prompt(query, lookup, rag_results)

        raw, used_llm, provider = self._call_llm(prompt)
        if not used_llm or not raw.strip():
            # fallback: 템플릿 조립
            text = self._fallback_answer(query, lookup, rag_results)
            return GenerationResult(
                text=text, used_llm=False, provider="fallback",
                notes=["LLM 미사용(fallback): 검증된 수치표 + 법령 원문 인용으로 답변을 조립했습니다."],
            )

        # post-hoc guard
        safe, guard_notes = sanitize_numbers(raw, lookup, rag_snippets, query)
        return GenerationResult(
            text=safe, used_llm=True, provider=provider, notes=guard_notes
        )

    # ------------------------------------------------------------------
    def _call_llm(self, prompt: str) -> tuple[str, bool, str]:
        """(text, used_llm, provider). 어떤 실패든 예외 없이 (\"\", False, ...) 반환."""
        provider = self.provider
        if provider == "none":
            return "", False, "none"

        api_key = self.config.llm.api_key()
        if not api_key:
            return "", False, provider  # 키 없음 → fallback

        try:
            if provider == "openai":
                return self._call_openai(prompt, api_key), True, "openai"
            if provider == "anthropic":
                return self._call_anthropic(prompt, api_key), True, "anthropic"
        except Exception:
            return "", False, provider  # SDK 미설치/네트워크/기타 실패 → fallback
        return "", False, provider

    def _call_openai(self, prompt: str, api_key: str) -> str:
        from openai import OpenAI  # 지연 import
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""

    def _call_anthropic(self, prompt: str, api_key: str) -> str:
        import anthropic  # 지연 import
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=1500,
            temperature=self.temperature,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts)

    # ------------------------------------------------------------------
    def _fallback_answer(
        self,
        query: str,
        lookup: LookupResult,
        rag_results: list[tuple[str, str, float]],
    ) -> str:
        """LLM 없이 안전하게 조립. 수치는 오직 lookup 표에서만 온다."""
        parts: list[str] = []
        parts.append("[LLM 미사용 · 검증된 데이터 기반 안내]")

        table = format_matches_table(lookup)
        if table:
            parts.append("■ 배출허용기준(조회 결과)")
            parts.append(table)
        elif lookup.is_numeric_intent:
            parts.append(
                "■ 배출허용기준: 질의하신 조건에 해당하는 값이 데이터에 없습니다.\n"
                "  국가법령정보센터(law.go.kr)에서 반드시 확인하세요. (수치를 추정하지 않습니다.)"
            )

        if lookup.notes:
            parts.append("■ 참고 사항")
            for n in lookup.notes:
                parts.append(f"  - {n}")

        if rag_results:
            parts.append("■ 관련 법령 맥락(원문 인용)")
            for text, source, _score in rag_results:
                snippet = text.strip().replace("\n", " ")
                if len(snippet) > 300:
                    snippet = snippet[:300] + "…"
                parts.append(f"  · ({source}) {snippet}")
        else:
            parts.append("■ 관련 법령 맥락: 색인된 문서에서 관련 조항을 찾지 못했습니다.")

        parts.append(
            "■ 안내: 위 수치는 조회된 구조화 데이터 그대로이며, 법령 해석이 필요한 경우 "
            "관할 지자체 또는 환경 전문가의 확인을 받으시기 바랍니다."
        )
        return "\n".join(parts)
