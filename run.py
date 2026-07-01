"""HydroLaw-AI CLI 진입점.

사용 예:
    # 최초 1회: 법령 문서를 임베딩하여 벡터 인덱스 구축
    python run.py --rebuild-index

    # 질의 실행
    python run.py --query "세차장을 운영 중인데 BOD 기준이 궁금합니다"
"""
from __future__ import annotations

import argparse
import sys

from src.config import AppConfig
from src.pipeline import HydroLawPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="HydroLaw-AI: 물환경 법령 안내 RAG 도구")
    parser.add_argument("--query", type=str, help="자연어 질의")
    parser.add_argument(
        "--config", type=str, default="config.yaml", help="설정 파일 경로"
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="data/laws 문서를 다시 읽어 벡터 인덱스를 재구축",
    )
    args = parser.parse_args()

    config = AppConfig.load(args.config)
    pipeline = HydroLawPipeline(config)

    if args.rebuild_index:
        n = pipeline.build_index(reset=True)
        print(f"[인덱스 구축 완료] {n}개 청크를 색인했습니다.")

    if not args.query:
        if not args.rebuild_index:
            parser.print_help()
        sys.exit(0)

    result = pipeline.answer(args.query)

    print("=" * 60)
    print(result.answer)
    print("=" * 60)
    if result.used_placeholder_data:
        print(
            "\n⚠️  현재 배출허용기준 데이터는 검증되지 않은 예시(더미) 데이터입니다.\n"
            "    실제 값은 국가법령정보센터에서 반드시 확인하세요."
        )
    print(f"\n{result.disclaimer}")


if __name__ == "__main__":
    main()
