# Contributing to HydroLaw-AI

HydroLaw-AI에 관심 가져주셔서 감사합니다. 이 프로젝트는 영세 사업자들이 복잡한 물환경 법령을
더 쉽게 이해할 수 있도록 돕는 것을 목표로 하며, 코드 기여뿐 아니라 법령/조례 데이터 제보도
큰 도움이 됩니다.

## 기여 방법

1. 이 저장소를 Fork 합니다.
2. 새 브랜치를 만듭니다: `git checkout -b feature/my-feature` 또는 `fix/my-bugfix`
3. 변경 사항을 작성하고 커밋합니다.
4. `pytest tests/`로 테스트를 통과하는지 확인합니다.
5. Pull Request를 생성합니다. 어떤 문제를 해결하는지, 어떻게 테스트했는지 설명해주세요.

## 개발 환경 설정

```bash
git clone https://github.com/<your-username>/hydrolaw-ai.git
cd hydrolaw-ai
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env   # 필요 시 API 키 입력

python run.py --rebuild-index
python run.py --query "세차장 BOD 기준이 궁금합니다"
```

## 기여 유형별 가이드

### 🐛 버그 리포트

GitHub Issue에 다음을 포함해 등록해주세요:
- 재현 방법 (실행한 명령어, 입력한 질의)
- 기대했던 동작 vs 실제 동작
- 환경 정보 (OS, Python 버전)

### ✨ 기능 제안

Issue로 먼저 논의해주시면 좋습니다. 특히 아래 로드맵과 관련된 제안을 환영합니다:
- 업종별 사용 예시 추가 (세차장, 식품제조업, 도금업 등)
- 국가법령정보센터 API 연동 자동화
- 지자체별 조례 템플릿 확장
- 환각(Hallucination) 방지 로직 개선

### 📊 법령/조례 데이터 제보 (가장 중요!)

`data/emission_standards.json`과 `data/laws/`는 현재 **더미(예시) 데이터**입니다.
실제 검증된 데이터로 교체하는 기여를 가장 환영합니다.

- 반드시 **국가법령정보센터(law.go.kr)** 또는 관할 지자체 공식 조례 원문을 출처로 사용해주세요.
- PR 설명에 출처 URL과 확인 일자를 함께 남겨주세요.
- `emission_standards.json`의 `_last_verified` 필드를 실제 확인 날짜로 업데이트해주세요.
- 수치에 대한 확신이 없다면 PR을 올리기보다 Issue로 먼저 논의해주세요. 잘못된 법령 정보는
  실제 사용자에게 피해를 줄 수 있습니다.

### 🧑‍💻 코드 기여

- 새 모듈을 추가할 때는 `src/` 아래에 단일 책임 원칙에 맞게 분리해주세요
  (예: 검색 로직, 생성 로직, 데이터 로딩 로직을 서로 분리).
- 외부 API 키가 없어도 최소한 문법 검사와 구조화 데이터 관련 테스트는 통과해야 합니다.
- 새 기능에는 가능한 한 `tests/`에 테스트를 함께 추가해주세요.

## 코드 스타일

- Python 3.10+ 기준으로 작성합니다.
- 타입 힌트를 사용합니다 (`from __future__ import annotations` 패턴 참고).
- 커밋 전 `ruff check`를 권장합니다 (CI에서도 참고용으로 실행됩니다).

## 라이선스

기여하신 코드는 이 프로젝트의 [MIT License](LICENSE)를 따릅니다.

## 행동 강령

서로 존중하는 태도로 소통해주세요. 특히 법령 데이터는 실제 사업자의 규제 준수에 영향을 줄 수 있는
민감한 내용이므로, 근거 없는 정보 추가는 지양해주시기 바랍니다.
