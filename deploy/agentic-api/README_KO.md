# Agentic API v0.5.0 최소 Kubernetes 배포

이 디렉터리는 업스트림 `deploy/kubernetes` 예시 중 운영 경로에 반드시 필요한 리소스만 남긴다.
`ConfigMap`, `Deployment`, `Service`만 Kustomize 대상이며 실제 Secret은 별도로 생성한다. Ingress, Namespace,
ServiceAccount, PDB, NetworkPolicy는 클러스터 공통 정책이나 환경별 overlay가 소유하도록 제외했다.

## 권장 경로

```text
클라이언트 / 인증 API Gateway
  -> Agentic API :9000
  -> LMStack Router :9400
  -> vLLM serving engines
```

Agentic API는 모델 서버가 아니라 stateful protocol gateway다. GPU, 모델 weight, Python, vLLM 런타임은 이 Pod에
필요하지 않다. 첫 배포는 Responses API 중간 변환을 줄이기 위해 Agentic API를 LMStack Router에 직접 연결하는 것을
권장한다. 다른 OpenAI-compatible proxy를 중간에 둘 경우 `/v1/responses`의 typed item, streaming event,
tool-call ID, `previous_response_id`가 손실되지 않는지 먼저 검증한다.

여러 model을 제공하는 권장 topology와 edge의 Completions/Chat/Messages/Responses path 분리, SSE/WebSocket,
retry, LMStack Router 0.1.9, P/D Cell 계약은
[`ROUTING_CONTRACT_KO.md`](./ROUTING_CONTRACT_KO.md)를 따른다. Agentic API의 단일 `LLM_API_BASE`는 단일
physical backend가 아니라 multi-model LMStack Router 같은 하나의 logical upstream을 뜻한다.

## Responses API 계약

기본 listen 주소는 `0.0.0.0:9000`, 주 endpoint는 `POST /v1/responses`다. `LLM_API_BASE`에는 기본값이 없으며,
standalone mode를 시작하려면 반드시 지정해야 한다. v0.5.0의 inference 호출은 upstream
`POST /v1/responses`를 사용하므로 `/v1/chat/completions`만 제공하는 backend로 대체할 수 없다.

| Agentic API endpoint | 역할 |
| --- | --- |
| `POST /v1/responses` | stateless passthrough와 DB-backed stateful execution |
| `GET /v1/responses` | Codex-style Responses WebSocket upgrade |
| `POST /v1/responses/compact` | 저장된 response chain 또는 직접 input compaction |
| `POST /v1/conversations` | conversation 생성 |
| `POST /v1/messages` | Anthropic Messages 호환 입력을 Responses 실행 경로로 변환 |
| `POST /v1/messages/count_tokens` | Anthropic token count 호환 경로 |
| `GET /v1/models` | upstream model 목록 |
| `GET /health`, `GET /ready` | process liveness와 dependency readiness |

Chat Completions 대신 Responses contract를 유지하면 message text만이 아니라 reasoning, function/custom/MCP tool call과
tool output을 typed item으로 보존할 수 있다. 또한 `response_id`, `previous_response_id`, `conversation_id`로 여러 turn의
상태와 branch를 PostgreSQL에서 이어가고, item 단위 SSE event, WebSocket continuation, context compaction을 같은 모델로
처리할 수 있다. 이점은 모델 자체 성능 향상이 아니라 agent protocol의 구조와 상태를 중간 proxy에서 잃지 않는 데 있다.

## 배포 전 필수 조건

1. `docker/Dockerfile.agentic-api`로 빌드해 내부 registry에 올린 versioned image
2. 모든 replica가 공유하는 외부 PostgreSQL과 전용 DB/role
3. `/v1/responses`를 지원하는 LMStack Router 또는 inference endpoint의 Service DNS와 `/health` 도달성
4. 내부 registry pull secret, PostgreSQL CA/client 인증서 등 클러스터별 Secret
5. 외부 공개 시 SSE buffering을 끄고 WebSocket upgrade를 전달하는 인증 gateway/Ingress

SQLite는 단일 Pod 개발 확인에는 쓸 수 있지만 replica 간 상태를 공유하지 않으므로 이 예시는 PostgreSQL을
필수로 한다. PostgreSQL proxy를 사용한다면 session pooling만 지원된다. Agentic API는 각 연결에 session 설정을
적용하므로 transaction pooling은 사용하지 않는다.

## 적용

이미지와 router 주소를 먼저 바꾼다.

```bash
cd deploy/agentic-api
kustomize edit set image agentic-api=registry.example.invalid/llm/agentic-api:0.5.0
sed -i 's#llm-router-service.inference.svc.cluster.local:9400#<실제-router-service>:9400#' configmap.yaml
```

Git에 Secret 값을 기록하지 않고 대상 namespace에 직접 만든다. PostgreSQL private CA가 필요하면 Secret으로
mount하고 `DATABASE_URL`에 `sslrootcert`의 mount 경로를 지정한다.

```bash
kubectl -n inference create secret generic agentic-api \
  --from-literal=DATABASE_URL='postgresql://agentic-api:REPLACE_ME@postgres.example.invalid:5432/agentic_api?sslmode=verify-full'

kubectl apply -k deploy/agentic-api
kubectl -n inference rollout status deployment/agentic-api --timeout=16m
kubectl -n inference get pods,service -l app.kubernetes.io/name=agentic-api
```

upstream이 `/health`를 제공하지 않을 때만 `SKIP_LLM_READY_CHECK=true`로 바꾼다. 이 경우에도 `/ready`의 PostgreSQL
검사는 유지되지만, inference endpoint 도달성은 별도의 smoke test가 소유해야 한다.

## 실행 옵션

컨테이너는 subcommand 없이 `agentic-server` standalone mode로 시작한다. `serve <model>`은 Python/vLLM을 함께
실행하는 통합 개발 모드이므로 이 최소 Rust 이미지에서는 지원 대상이 아니다.

| CLI / 환경 변수 | 기본값 | 용도 |
| --- | --- | --- |
| `--llm-api-base` / `LLM_API_BASE` | 없음, 필수 | 외부 vLLM/OpenAI-compatible endpoint. trailing `/v1`은 정규화됨 |
| `--gateway-host` / `GATEWAY_HOST` | `0.0.0.0` | listen address |
| `--gateway-port` / `GATEWAY_PORT` | `9000` | listen port |
| `--db-url` (`--database-url`) / `DATABASE_URL` | local SQLite | state 저장소. 운영 replica는 PostgreSQL 필수 |
| `--openai-api-key` / `OPENAI_API_KEY` | 없음 | upstream에 전달할 credential. inbound 인증값이 아님 |
| `--oidc-issuer` / `OIDC_ISSUER` | 없음 | 선택적 inbound OIDC issuer |
| `--oidc-audience` / `OIDC_AUDIENCE` | 없음 | OIDC audience. issuer와 반드시 함께 설정 |
| `--llm-ready-timeout-s` | `600` | 시작 시 upstream `/health` 대기 한도 |
| `--llm-ready-interval-s` | `2` | upstream probe 간격 |
| `--skip-llm-ready-check` / `SKIP_LLM_READY_CHECK` | `false` | upstream에 `/health`가 없을 때만 probe 생략 |
| `CORS_ALLOWED_ORIGINS` | 없음 | 브라우저 허용 origin의 comma-separated 목록 |
| `RUST_LOG` | runtime 기본값 | Rust 로그 필터 |

PostgreSQL 튜닝 변수의 v0.5.0 기본값은 다음과 같다.

| 변수 | 기본값(초) | 설명 |
| --- | ---: | --- |
| `POSTGRES_MAX_CONNECTIONS` | `10` | replica 하나의 최대 pool 크기 |
| `POSTGRES_ACQUIRE_TIMEOUT_SECONDS` | `30` | pool connection 획득 대기 |
| `POSTGRES_LOCK_TIMEOUT_SECONDS` | `5` | 일반 lock 대기 |
| `POSTGRES_MIGRATION_TIMEOUT_SECONDS` | `300` | startup migration lock/statement 한도 |
| `POSTGRES_STATEMENT_TIMEOUT_SECONDS` | `30` | 일반 statement 실행 한도 |
| `POSTGRES_IDLE_TIMEOUT_SECONDS` | `600` | idle connection recycle, `0`이면 해제 |
| `POSTGRES_MAX_LIFETIME_SECONDS` | `1800` | connection lifetime, `0`이면 해제 |

전체 DB connection budget은 `replica 수 × POSTGRES_MAX_CONNECTIONS`이며 migration, 관리, failover용 여유를
DB limit에 남긴다. 각 replica는 시작 시 embedded migration을 실행하고 PostgreSQL advisory lock으로 직렬화한다.
별도 migration controller가 schema를 선적용한 경우에만 `AGENTIC_API_SCHEMA_READY=1`을 사용한다. migration 실패를
우회하기 위해 이 값을 설정하면 안 된다.

도구 기능을 사용할 때만 다음 환경을 추가한다.

- Web search: `YOU_API_KEY`, `YOU_API_BASE_URL`
- MCP egress allowlist: `AGENTIC_MCP_ALLOWED_HOSTS`
- Messages tool alias: `MESSAGES_GATEWAY_TOOL_ALIASES`
- 상세 MCP/tool 설정: `/var/lib/agentic-api/config.toml`

## 인증과 노출 범위

OIDC를 설정하지 않으면 inbound 인증은 없다. `OPENAI_API_KEY`는 upstream credential일 뿐 caller 인증이 아니다.
따라서 기본 Service는 `ClusterIP`로 두고 기존 인증 API Gateway 뒤에 배치하거나 `OIDC_ISSUER`와
`OIDC_AUDIENCE`를 함께 설정한다. v0.5.0에서는 인증된 사용자 간 persisted object 권한 격리까지 완결된 것으로
간주하지 말고, [upstream issue #107](https://github.com/vllm-project/agentic-api/issues/107)이 정리되기 전까지
불특정 multi-tenant에 직접 노출하기 전에 trusted edge 또는 tenant 단위 격리를 둔다.

## 운영 검증

배포 성공만으로 stateful 경로가 검증되지는 않는다. 다음을 release gate로 둔다.

1. `/health`와 `/ready`가 모두 200인지 확인한다.
2. `/v1/responses` non-streaming/streaming 및 tool call을 각각 실행한다.
3. 첫 응답 ID를 `previous_response_id`로 넘겨 후속 응답을 실행한다.
4. 두 replica에 요청이 분산된 상태에서 같은 conversation이 이어지는지 확인한다.
5. 한 Pod를 재시작하고 후속 응답, SSE 종료, WebSocket 재연결을 확인한다.
6. PostgreSQL backup/PITR, schema upgrade와 rollback 절차를 별도 운영 runbook으로 검증한다.
7. edge에서 `POST /v1/responses` SSE와 `GET /v1/responses` WebSocket Upgrade가 모두 Agentic으로 route되는지 확인한다.
8. Responses POST에 partial stream/tool execution 이후 blind retry가 적용되지 않는지 확인한다.

PDB, topology spread hard constraint, NetworkPolicy, Ingress/Gateway API, HPA는 공통 플랫폼 정책에 맞춰 overlay에서
추가한다. 최소 예시는 replica anti-affinity를 soft rule로만 제공한다.
