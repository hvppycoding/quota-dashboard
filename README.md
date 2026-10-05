# Quota Dashboard

개인 구독 사용량을 한 화면에서 확인하는 작은 macOS 대시보드입니다. Python 표준 라이브러리와 설치된 Codex CLI, CodexBarCLI를 사용합니다.

- Codex와 Claude의 사용률, 자동 리셋 예정 시각
- 기간 경과를 표시하는 작은 삼각형과, 경과보다 앞서 쓴 부분을 표시하는 주황색 오버페이스
- Codex 수동 리셋권의 수량과 만료일, Claude 리셋권 조회 미지원 표시
- OpenRouter 계정 크레딧과 최근 7일 일별 토큰 그래프, Tokens·Requests·Spend 표
- 7일 총 토큰 수가 큰 순서대로 모델 5개와 나머지 합계인 Others

## 실행

Python 3, Codex CLI(`~/.local/bin/codex`), Claude CLI(`~/.local/bin/claude`), CodexBar 0.71.0이 설치되어 있고 각 앱의 기존 로그인이 필요합니다. 사용자 홈은 운영체제 계정 정보로 확인합니다.

```sh
cp config.example.json config.cli.json
python3 server.py
```

`http://127.0.0.1:8081/`에서 확인할 수 있습니다. 서버는 루프백에만 바인딩합니다. 기존 프록시를 통해 사용할 경우 허용할 호스트 이름을 `QUOTA_DASHBOARD_HOST` 환경변수로 전달합니다. 이 저장소는 네트워크·인증 설정을 자동 변경하지 않습니다.

OpenRouter를 사용하려면 기존 환경변수 `OPENROUTER_API_KEY`를 사용하는 상태에서 다음처럼 실행합니다.

```sh
python3 server.py --enable-openrouter
```

일반 OpenRouter API 키로 계정 크레딧은 읽을 수 있지만 일별 모델 상세 사용량 API는 관리 키가 필요합니다. 상세 그래프용 `OPENROUTER_MANAGEMENT_KEY`가 기존 실행 환경에 제공된 경우에만 공식 Activity API를 GET 조회합니다. 연결되지 않은 경우 최근 7일 사용액과 그래프는 조회 불가로 표시하며, 없는 정보를 0으로 채우지 않습니다.

공식 Activity API가 제공하는 **완료된 UTC 날짜 7일**을 집계합니다. 오늘의 부분 사용량은 포함하지 않으며 날짜 기준을 화면에 표시합니다. Tokens는 prompt + completion이며 cached·reasoning을 다시 더하지 않습니다.

## 데이터와 보안

수집 주기는 5분입니다. 실패하면 마지막 확인값을 유지하고 오래된 상태를 표시합니다. HTTP 라우트는 `/`와 `/api/usage`뿐이며 GET·HEAD만 지원합니다. API 응답은 표시할 사용량 필드만 포함하고 원본 응답이나 계정 식별정보를 공개하지 않습니다.

리셋권 사용, 구매, 계정 전환은 구현하지 않습니다. Claude의 자동 리셋 시각과 수동 리셋권은 별개입니다. 현재 Claude 소스는 CLI이며, Web 전용 수동 리셋권 정보는 이 대시보드에서 수집하지 않습니다.

저장소에는 운영 설정, 실제 사용 내역, 인증정보, 로그, 개인 프록시 주소, 자동 로그인 또는 서비스 설치 스크립트가 포함되어 있지 않습니다. 실제 서비스 접근을 통제하는 인증·프록시 구성은 각자의 실행 환경에서 관리해야 합니다.

## 검증

```sh
python3 -m unittest discover -p test_activity.py
```

테스트는 예시 데이터로 상위 5개 + Others의 합계 보존, 완료된 UTC 7일 범위, 잘못된 수치와 미조회 상태의 처리를 확인합니다.

이 저장소는 CodexBar의 소스를 포함하지 않으며 설치된 CLI를 호출합니다.
