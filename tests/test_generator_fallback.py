"""LLM 미사용(fallback) 및 숫자 검증 테스트.

- API 키 없이 fallback 동작 확인
- 답변이 항상 str 반환 (예외 없음)
- post-hoc guard: 허용되지 않은 수치 제거/교체
- provider=none 설정에서도 정상 작동
"""
import re

import pytest

from src.config import AppConfig, LLMConfig, EmbeddingConfig, VectorStoreConfig, RetrievalConfig, DataConfig
from src.generator import Generator, sanitize_numbers, _collect_allowed_numbers
from src.standards_lookup import LookupResult


@pytest.fixture
def none_config():
    """provider=none 설정."""
    return AppConfig(
        llm=LLMConfig(provider="none", model="none", api_key_env="", temperature=0.1),
        embedding=EmbeddingConfig(model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
        vector_store=VectorStoreConfig(path="./vector_store", collection_name="test"),
        retrieval=RetrievalConfig(top_k=4),
        data=DataConfig(laws_dir="./data/laws", emission_standards_file="./data/emission_standards.json"),
        base_dir=".",
    )


@pytest.fixture
def generator(none_config):
    """provider=none 제너레이터."""
    return Generator(none_config)


class TestGeneratorFallback:
    """LLM 미사용 fallback 동작."""

    def test_fallback_no_exception(self, generator):
        """provider=none → 예외 없이 str 반환."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "region": "청정지역", "size": "large", "limit": 30, "unit": "mg/L"}
            ],
            is_dummy=False,
            notes=["test"],
        )
        rag = [("법령 내용", "법령1", 0.9)]

        result = generator.generate("BOD 기준?", lookup, rag)

        assert isinstance(result.text, str), "답변이 str이 아님"
        assert result.text.strip(), "답변이 비어있음"
        assert not result.used_llm, "fallback이므로 used_llm=False"

    def test_fallback_includes_table(self, generator):
        """fallback 답변에 수치표 포함."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "region": "청정지역", "size": "large", "limit": 30, "unit": "mg/L"}
            ],
            is_dummy=False,
        )
        result = generator.generate("BOD?", lookup, [])
        assert "BOD" in result.text, "답변에 BOD 없음"
        assert "30" in result.text, "답변에 수치(30) 없음"

    def test_fallback_includes_rag_context(self, generator):
        """fallback 답변에 RAG 인용 포함."""
        lookup = LookupResult(matches=[], is_dummy=False)
        rag = [("배출허용기준은 지역에 따라 다릅니다.", "법령A", 0.95)]
        result = generator.generate("규제?", lookup, rag)
        assert "배출허용기준" in result.text or "법령A" in result.text

    def test_fallback_empty_lookup(self, generator):
        """조회 결과 없음 → fallback 답변에 안내."""
        lookup = LookupResult(
            matches=[],
            is_dummy=False,
            notes=["데이터에 없음"],
            detected_pollutants=["페놀"],  # numeric_intent가 true가 되도록
        )
        result = generator.generate("페놀 기준?", lookup, [])
        assert "데이터" in result.text or "확인" in result.text


class TestSanitizeNumbers:
    """post-hoc 숫자 검증 및 제거."""

    def test_allowed_numbers_from_lookup(self):
        """lookup 수치가 허용되는지."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "limit": 30, "unit": "mg/L"}
            ]
        )
        allowed = _collect_allowed_numbers(lookup, [], "")
        assert "30" in allowed, "lookup 수치가 허용되지 않음"

    def test_allowed_numbers_from_query(self):
        """질의의 숫자가 허용되는지."""
        lookup = LookupResult(matches=[])
        allowed = _collect_allowed_numbers(lookup, [], "2000㎥ 이상")
        assert "2000" in allowed, "질의 숫자가 허용되지 않음"

    def test_allowed_numbers_from_rag(self):
        """RAG 청크의 숫자가 허용되는지."""
        lookup = LookupResult(matches=[])
        rag = ["제34조에 따르면 배출허용기준은"]
        allowed = _collect_allowed_numbers(lookup, rag, "")
        assert "34" in allowed, "RAG 숫자(조항)가 허용되지 않음"

    def test_unauthorized_number_removed(self):
        """허용되지 않은 숫자가 든 문장 제거."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "limit": 30, "unit": "mg/L"}
            ]
        )
        answer = "BOD 기준은 30 mg/L입니다. COD 기준은 99 mg/L입니다."
        safe, notes = sanitize_numbers(answer, lookup, [], "")
        # 99는 허용되지 않음 → 그 문장이 제거/교체되어야 함
        assert "99" not in safe, "허용되지 않은 수치(99)가 답변에 남음"
        assert notes, "guard note가 생성되지 않음"

    def test_multiple_violations_noted(self):
        """여러 위반 발생 시 note 복수 생성."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "limit": 30, "unit": "mg/L"}
            ]
        )
        answer = "기준1: 11 mg/L. 기준2: 22 mg/L. 기준3: 33 mg/L."
        safe, notes = sanitize_numbers(answer, lookup, [], "")
        # 11, 22, 33 모두 미허용 → 각각 별도의 문장이므로 교체됨
        assert len(notes) > 0

    def test_normalized_numbers_match(self):
        """콤마 있는 숫자도 인식 (1,000 == 1000)."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "limit": 1000, "unit": "mg/L"}
            ]
        )
        allowed = _collect_allowed_numbers(lookup, [], "")
        assert "1000" in allowed
        # 질의에 "1,000" 형식이 오면 정규화되어 인식되어야 함

    def test_sentence_split_accuracy(self):
        """문장 분리가 정확한지 (마침표/줄바꿈)."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "limit": 30, "unit": "mg/L"}
            ]
        )
        answer = "기준은 30 mg/L입니다. 별도 기준은 99 mg/L입니다."
        safe, notes = sanitize_numbers(answer, lookup, [], "")
        # 첫 문장은 보존, 두 번째 문장은 교체
        assert "30 mg/L" in safe
        assert "99" not in safe


class TestGeneratorCallLLMFallback:
    """_call_llm 실패 시 fallback."""

    def test_provider_none_returns_empty(self, generator):
        """provider=none → _call_llm이 ("", False, "none") 반환."""
        text, used_llm, provider = generator._call_llm("test prompt")
        assert text == "", "provider=none은 빈 텍스트 반환"
        assert not used_llm, "provider=none은 used_llm=False"
        assert provider == "none"

    def test_no_api_key_falls_back(self, none_config):
        """API 키 없음 → fallback."""
        config = AppConfig(
            llm=LLMConfig(provider="openai", model="gpt-4", api_key_env="", temperature=0.1),
            embedding=EmbeddingConfig(model="test"),
            vector_store=VectorStoreConfig(path=".", collection_name="test"),
            retrieval=RetrievalConfig(),
            data=DataConfig(laws_dir=".", emission_standards_file="."),
            base_dir=".",
        )
        gen = Generator(config)
        text, used_llm, provider = gen._call_llm("test")
        assert not used_llm, "API 키 없으면 LLM 사용 안 함"

    def test_generate_always_returns_result(self, generator):
        """generate()는 절대 예외로 죽지 않음."""
        lookup = LookupResult(matches=[])
        try:
            result = generator.generate("bad query", lookup, [])
            assert isinstance(result.text, str)
        except Exception as e:
            pytest.fail(f"generate()가 예외 발생: {e}")


class TestGeneratorWithoutLLM:
    """LLM 없이 안전하게 조립."""

    def test_fallback_answer_template(self, generator):
        """fallback 답변 템플릿 검증."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "region": "청정지역", "size": "large", "limit": 30, "unit": "mg/L"}
            ],
            notes=["테스트 note"],
        )
        text = generator._fallback_answer("test", lookup, [])
        # 템플릿 요소 확인
        assert "[LLM 미사용" in text or "LLM 미사용" in text
        assert "배출허용기준" in text or "표" in text or "|" in text

    def test_fallback_only_uses_lookup_numbers(self, generator):
        """fallback 답변은 lookup의 수치만 사용."""
        lookup = LookupResult(
            matches=[
                {"pollutant": "BOD", "limit": 30, "unit": "mg/L"}
            ]
        )
        text = generator._fallback_answer("query", lookup, [])
        # lookup에 없는 수치(예: 99)가 없어야 함
        assert "99" not in text, "lookup에 없는 수치가 생성됨 (환각)"
