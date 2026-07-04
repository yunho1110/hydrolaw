"""공용 fixture 및 테스트 설정.

- API 키 환경변수 제거 (모든 테스트는 키 없이 실행)
- session-scope pipeline fixture (인덱스 1회만 생성)
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.config import AppConfig, LLMConfig, EmbeddingConfig, VectorStoreConfig, RetrievalConfig, DataConfig
from src.pipeline import HydroLawPipeline


@pytest.fixture(scope="session", autouse=True)
def clear_api_keys():
    """모든 테스트가 API 키 없이 실행되도록 환경변수 제거."""
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
        if key in os.environ:
            del os.environ[key]


@pytest.fixture(scope="session")
def test_config():
    """테스트 설정: provider=none, 임시 벡터스토어."""
    tmpdir = Path(tempfile.gettempdir()) / "hydrolaw_test_vs"
    tmpdir.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        llm=LLMConfig(
            provider="none",
            model="gpt-4o-mini",
            api_key_env="",
            temperature=0.1,
        ),
        embedding=EmbeddingConfig(model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
        vector_store=VectorStoreConfig(path=str(tmpdir), collection_name="test_laws"),
        retrieval=RetrievalConfig(top_k=4, chunk_size=400, chunk_overlap=80),
        data=DataConfig(
            laws_dir="./data/laws",
            emission_standards_file="./data/emission_standards.json",
        ),
        disclaimer="테스트 데이터입니다.",
        base_dir=os.path.abspath("."),
    )


@pytest.fixture(scope="session")
def pipeline(test_config):
    """인덱스는 1회만 생성, 이후 재사용."""
    p = HydroLawPipeline(test_config)
    # 빌드 (이미 인덱스가 있으면 reset=False로 빠르게)
    p.build_index(reset=True)
    return p


@pytest.fixture(scope="session")
def emission_standards_data():
    """data/emission_standards.json 원본 로드."""
    import json
    with open("./data/emission_standards.json", "r", encoding="utf-8") as f:
        return json.load(f)
