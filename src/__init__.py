"""HydroLaw-AI 코어 패키지.

하이브리드 구조:
  - 배출허용기준 수치 → 구조화 JSON 정확 조회 (환각 0% 경로, standards_lookup)
  - 법령 해석·맥락 → RAG 검색 + LLM 생성 (vector_store + generator)

pipeline.HydroLawPipeline 이 위 둘을 오케스트레이션한다.
"""

__all__ = ["config", "standards_lookup", "vector_store", "generator", "pipeline"]
