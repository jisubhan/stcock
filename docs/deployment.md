# Railway 연결 및 공개 데모 배포

이 저장소는 Railway에 **Docker 서비스 한 개**로 배포한다. 컨테이너 안에서 Next.js 웹과 Python API를 함께 시작하고, `TOSS_DATA_MODE=demo`로 합성 데이터 대시보드를 제공한다. 먼저 Railway 계정에서 GitHub 저장소를 연결해야 한다.

## 1. GitHub 저장소 연결

1. [Railway 새 프로젝트](https://railway.com/new)를 열어 로그인한다.
2. **Deploy from GitHub repo**를 선택한다.
3. Railway가 GitHub 연결 또는 GitHub App 권한을 요청하면 `jisubhan/stcock` 저장소에 대한 접근을 승인한다.
4. 목록에서 `jisubhan/stcock`을 선택하고 브랜치를 `main`으로 맞춘다. Root Directory는 저장소 루트로 지정해 루트 `Dockerfile`로 배포한다.

저장소가 목록에 보이지 않으면 GitHub의 Railway App 설치 설정에서 이 저장소 접근이 허용되어 있는지 확인한 뒤 Railway의 저장소 목록을 새로고침한다. 현재 서비스 배포는 사용자의 Railway 계정 연결과 생성 화면에서 진행한다. 저장소 파일 준비만으로 계정 연결이나 공개 URL이 생성된 것은 아니다.

## 2. 서비스 설정

Railway는 소스 루트의 이름이 정확히 `Dockerfile`인 파일을 자동 감지한다. 이 프로젝트의 Dockerfile에 build와 실행 명령이 포함되어 있다.

| 설정 | 값 |
| --- | --- |
| 서비스 이름 | `flowtrade-radar` |
| GitHub 저장소 / 브랜치 | `jisubhan/stcock` / `main` |
| Root Directory | 저장소 루트 |
| Builder | Dockerfile |
| Dockerfile path | `Dockerfile` |
| Start command | Dockerfile의 기본 `CMD` 사용 |
| Healthcheck path | `/api/health` |
| Healthcheck timeout | `300`초 |
| Restart policy | `ON_FAILURE` |
| Maximum restart retries | `3` |

서비스 Variables에 다음 값을 설정한다.

```text
TOSS_DATA_MODE=demo
API_DATA_DIR=/app/.runtime
```

웹 서버는 Railway가 제공하는 `PORT`를 읽어 `0.0.0.0`에 바인딩한다. Docker 기본 port는 `10000`이다. 고정하여 설정할 경우 `PORT=10000`을 추가하고 공개 도메인의 target port도 `10000`으로 맞춘다. Railway가 다른 `PORT`를 주입하는 구성을 유지한다면 target port는 실제 주입된 값과 같아야 한다.

API는 같은 컨테이너의 `127.0.0.1:8000`에서 실행되고 웹의 `/api/*` 요청이 내부 API로 전달된다. 공개 도메인은 웹의 `PORT`에 연결한다. 데모에는 Toss client ID, client secret 또는 access token을 등록할 필요가 없다.

Railway 화면에서 플랜, 사용량과 결제 조건을 확인한 뒤 배포한다. 이 저장소가 특정 무료 플랜이나 비용 조건을 보장하지는 않는다.

## 3. 공개 도메인 생성

배포가 정상 상태가 되면 서비스의 **Settings → Networking → Public Networking → Generate Domain**을 선택한다. Railway 공식 문서는 기본 서비스 생성만으로 도메인이 자동 제공되지는 않는다고 설명한다.

Target port를 물으면 웹 서버가 실제 듣는 port를 선택한다. `PORT=10000`을 설정했다면 `10000`이다. Python의 내부 API port인 `8000`을 공개 웹 도메인에 연결하지 않는다.

생성된 HTTPS 주소를 브라우저에서 열고 다음을 확인한다.

- `/api/health`가 HTTP 200과 `status: ok`, `mode: demo`를 반환한다.
- 대시보드, 종목 상세, 테마 편집과 모의매매 화면이 열린다.
- 데이터가 모의 데이터로 표시되고 실제 주문 기능이 활성화되지 않는다.

도메인 설정 후 `Application failed to respond`가 나오면 공개 도메인의 target port와 `PORT`, 웹 bind 주소, API 시작 오류를 서비스 로그에서 확인한다. Healthcheck는 Railway가 주입한 `PORT`로 요청하므로 target port만 별도로 바꾸기보다 `PORT`와 일치시킨다.

## 공유 데이터와 저장 수명

전략 설정, 테마 편집, 알림 읽기와 모의 포지션은 로컬 SQLite/YAML에 저장되며 방문자 전체가 공유한다. 사용자별 계정 분리는 아직 없다.

기본 데모에는 영구 볼륨을 연결하지 않는다. `/app/.runtime` 변경은 재배포 또는 인스턴스 교체 시 사라질 수 있다. 계속 보존하려면 인증·사용자 분리와 함께 별도 영구 저장소를 구성한다. 볼륨이나 데이터베이스의 지원 플랜과 비용은 Railway에서 확인한다.

## 현재 공식 설정 파일 계약

2026-10-07에 확인한 Railway 공식 GitHub 문서는 `railway.json` / `railway.toml` 방식인 **Config as Code를 deprecated**로 안내한다. 새 서비스는 이 방식에 참여할 수 없고, 기존 서비스도 **2026-12-01**부터 파일을 읽지 않는다고 명시한다. 따라서 이번 새 서비스는 위의 Dockerfile 감지와 서비스 설정 화면으로 연결한다.

기존 Config as Code의 정확한 공식 schema URL은 `https://railway.com/railway.schema.json`이며 문서에서 확인한 field는 다음과 같다. 이것은 새 서비스에서 해당 파일이 적용된다는 의미가 아니다.

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "healthcheckPath": "/api/health",
    "healthcheckTimeout": 300,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3
  }
}
```

공식 field enum은 builder가 `RAILPACK` 또는 `DOCKERFILE`, restart policy가 `ON_FAILURE`, `ALWAYS`, `NEVER`다. 새로운 코드 기반 프로젝트 관리는 `.railway/railway.ts`를 사용하는 **Infrastructure as Code**가 공식 대안이다. 이는 Railway CLI의 계정 인증·프로젝트 연결·plan/apply로 반영하며 GitHub deploy 때 파일을 자동 읽는 방식이 아니다. 이 문서 작성 중 CLI 인증이나 서비스 생성·설정 적용을 하지 않았다.

## 실제 Toss 시세를 연결할 때

공개 웹 데모는 `demo`로 먼저 열 수 있다. 실제 시세에는 Toss API에 등록할 고정 공인 출구 IP와 서버 비밀 설정, OAuth·읽기 전용 시세·KRX/NXT 샘플 검증이 별도로 필요하다.

Railway 공식 문서는 **Pro 플랜의 Static Outbound IPs**를 지원한다. 실제 연결을 선택하면 서비스 **Settings → Networking → Enable Static IPs**에서 해당 region에 표시된 IPv4 주소를 확인하고, 모든 표시 주소를 토스증권 WTS **설정 → Open API → 허용 IP 관리**에 등록한 뒤 재배포한다. 최신 HA 방식은 여러 출구 IP로 트래픽을 분산하며 공식 문서는 세 개 주소를 설명한다. 서비스 화면에 표시된 실제 주소를 기준으로 등록한다. region을 바꾸면 주소도 달라진다.

이 기능의 플랜과 비용은 Railway에서 별도로 검토한다. 데모 배포를 위해 활성화할 필요는 없다. 자세한 Toss 확인 결과는 [Toss API 검증 기록](toss-api-verification.md)을 참고한다. 실제 주문은 구현 범위에 포함되지 않는다.

## 공식 근거와 확인 범위

Railway 문서 호스트와 JSON schema의 직접 HTTPS 요청은 이 실행 환경의 프록시 CONNECT 403으로 차단됐다. 같은 요청을 반복하거나 네트워크 정책을 바꾸지 않고 **공식 `railwayapp/docs` GitHub 저장소**를 읽어 현재 계약을 확인했다. 확인한 문서 commit은 `57388af2e6b6fca973eb24f416ee2906cadcf831`이다.

- [GitHub 연결과 새 프로젝트 안내](https://github.com/railwayapp/docs/blob/57388af2e6b6fca973eb24f416ee2906cadcf831/content/guides/actix-web.md#deploy-from-a-github-repo)
- [GitHub App 저장소 권한](https://github.com/railwayapp/docs/blob/57388af2e6b6fca973eb24f416ee2906cadcf831/content/docs/deployments/github-autodeploys.md#troubleshooting)
- [Dockerfile 감지](https://github.com/railwayapp/docs/blob/57388af2e6b6fca973eb24f416ee2906cadcf831/content/docs/builds/dockerfiles.md)
- [공개 Networking과 Generate Domain](https://github.com/railwayapp/docs/blob/57388af2e6b6fca973eb24f416ee2906cadcf831/content/docs/networking/public-networking.md#quick-start)
- [Healthcheck와 PORT](https://github.com/railwayapp/docs/blob/57388af2e6b6fca973eb24f416ee2906cadcf831/content/docs/deployments/healthchecks.md)
- [기존 Config as Code field reference](https://github.com/railwayapp/docs/blob/57388af2e6b6fca973eb24f416ee2906cadcf831/content/docs/config-as-code/reference.md)
- [Infrastructure as Code와 기존 방식의 중단 일정](https://github.com/railwayapp/docs/blob/57388af2e6b6fca973eb24f416ee2906cadcf831/content/docs/infrastructure-as-code.md#iac-vs-config-as-code)
- [Static Outbound IPs](https://github.com/railwayapp/docs/blob/57388af2e6b6fca973eb24f416ee2906cadcf831/content/docs/networking/static-outbound-ips.md)

현재 `railwayapp/cli` source도 읽기 전용으로 확인했다.

준비한 실행 구성의 로컬 검증 결과:

- production Next.js build 통과.
- 실제 `PORT=10000`에서 standalone 웹과 Python API를 함께 시작해 `/api/health`, 추천 후보 세 개와 favicon HTTP 200 확인.
- desktop/mobile 브라우저 검사 40개 통과.
- SIGTERM으로 정상 종료 시 웹·API 두 프로세스가 모두 종료되고 supervisor exit code 0 확인.

컨테이너 이미지 build는 현재 환경의 `registry-1.docker.io` 다운로드가 `Forbidden`으로 차단되어 완료하지 못했다. 로컬 production 실행 검증과 이미지 build 완료는 구분한다. 이 접근 결과만으로 Railway의 원격 build가 실패한다고 판단하지 않는다. 실제 Railway 계정 연결, 원격 Docker build, 서비스 healthcheck와 공개 URL은 서비스 생성 후 확인해야 한다.
