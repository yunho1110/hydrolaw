"""애플리케이션 설정 로더.

run.py 와의 계약:
    config = AppConfig.load(args.config)
    config.llm.provider / config.llm.model / config.llm.api_key_env / config.llm.temperature
    config.embedding.model
    config.vector_store.path / config.vector_store.collection_name
    config.retrieval.top_k / chunk_size / chunk_overlap
    config.data.laws_dir / config.data.emission_standards_file
    config.disclaimer

키가 누락되면 어떤 키가 어디서 빠졌는지 알려주는 친절한 에러를 낸다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    import yaml
except ImportError as e:  # pragma: no cover - 설치 안내
    raise ImportError(
        "pyyaml 이 필요합니다. `pip3 install --user pyyaml` 로 설치하세요."
    ) from e


class ConfigError(ValueError):
    """설정 파일 파싱/검증 실패."""


def _require(mapping: dict, key: str, where: str):
    if not isinstance(mapping, dict):
        raise ConfigError(f"[{where}] 섹션이 매핑(dict)이 아닙니다. config.yaml 을 확인하세요.")
    if key not in mapping:
        raise ConfigError(
            f"[{where}] 에 필수 키 '{key}' 가 없습니다. config.yaml 에 추가하세요."
        )
    return mapping[key]


@dataclass
class LLMConfig:
    provider: str          # "openai" | "anthropic" | "none"
    model: str
    api_key_env: str
    temperature: float = 0.1

    def api_key(self) -> str | None:
        """환경변수에서 API 키를 읽는다. 없으면 None (fallback 유발)."""
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None


@dataclass
class EmbeddingConfig:
    model: str


@dataclass
class VectorStoreConfig:
    path: str
    collection_name: str


@dataclass
class RetrievalConfig:
    top_k: int = 4
    chunk_size: int = 400
    chunk_overlap: int = 80


@dataclass
class DataConfig:
    laws_dir: str
    emission_standards_file: str


@dataclass
class AppConfig:
    llm: LLMConfig
    embedding: EmbeddingConfig
    vector_store: VectorStoreConfig
    retrieval: RetrievalConfig
    data: DataConfig
    disclaimer: str = ""
    # config.yaml 이 위치한 디렉터리 (상대경로 해석 기준)
    base_dir: str = field(default="")

    # ---- 경로 헬퍼: config.yaml 기준 상대경로를 절대경로로 ----
    def _resolve(self, p: str) -> str:
        if not p:
            return p
        if os.path.isabs(p):
            return p
        return os.path.normpath(os.path.join(self.base_dir, p))

    @property
    def laws_dir(self) -> str:
        return self._resolve(self.data.laws_dir)

    @property
    def emission_standards_file(self) -> str:
        return self._resolve(self.data.emission_standards_file)

    @property
    def vector_store_path(self) -> str:
        return self._resolve(self.vector_store.path)

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        if not os.path.exists(path):
            raise ConfigError(f"설정 파일을 찾을 수 없습니다: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"config.yaml 파싱 실패: {e}") from e

        if not isinstance(raw, dict):
            raise ConfigError("config.yaml 최상위가 매핑(dict)이어야 합니다.")

        llm_raw = _require(raw, "llm", "root")
        provider = str(_require(llm_raw, "provider", "llm")).lower().strip()
        if provider not in ("openai", "anthropic", "none"):
            raise ConfigError(
                f"[llm.provider] 값 '{provider}' 은 지원되지 않습니다. "
                "openai | anthropic | none 중 하나여야 합니다."
            )
        llm = LLMConfig(
            provider=provider,
            model=str(_require(llm_raw, "model", "llm")),
            api_key_env=str(llm_raw.get("api_key_env", "") or ""),
            temperature=float(llm_raw.get("temperature", 0.1)),
        )

        emb_raw = _require(raw, "embedding", "root")
        embedding = EmbeddingConfig(model=str(_require(emb_raw, "model", "embedding")))

        vs_raw = _require(raw, "vector_store", "root")
        vector_store = VectorStoreConfig(
            path=str(_require(vs_raw, "path", "vector_store")),
            collection_name=str(_require(vs_raw, "collection_name", "vector_store")),
        )

        r_raw = raw.get("retrieval", {}) or {}
        retrieval = RetrievalConfig(
            top_k=int(r_raw.get("top_k", 4)),
            chunk_size=int(r_raw.get("chunk_size", 400)),
            chunk_overlap=int(r_raw.get("chunk_overlap", 80)),
        )

        data_raw = _require(raw, "data", "root")
        data = DataConfig(
            laws_dir=str(_require(data_raw, "laws_dir", "data")),
            emission_standards_file=str(
                _require(data_raw, "emission_standards_file", "data")
            ),
        )

        disclaimer = str(raw.get("disclaimer", "") or "").strip()

        return cls(
            llm=llm,
            embedding=embedding,
            vector_store=vector_store,
            retrieval=retrieval,
            data=data,
            disclaimer=disclaimer,
            base_dir=os.path.dirname(os.path.abspath(path)),
        )
