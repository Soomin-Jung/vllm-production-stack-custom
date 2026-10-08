# 운영과 현업 효과 검증

## 로컬 stdio와 중앙 HTTP

stdio는 각 클라이언트가 작은 MCP 프로세스를 실행하고 기존 vLLM HTTP backend를 호출한다. GPU 모델은 중복 로드되지 않는다. 초기 도입은 이 방식으로 가능하다.

여러 클라이언트가 중앙 MCP를 공유하려면 Streamable HTTP로 실행한다. backend API와 MCP API는 서로 다른 주소다.

```bash
export JEV_BASE_URL='http://your-vllm-service:8000'
export JEV_MCP_TOKEN='replace-with-your-mcp-access-token'
export JEV_MCP_ALLOWED_HOSTS='jev-mcp.internal:8080'
jev-decide-mcp --transport streamable-http --host 0.0.0.0 --port 8080
```

`/mcp`는 MCP JSON-RPC endpoint이며 `/v1/decide`는 JEV backend REST endpoint다. remote client에는 `http(s)://jev-mcp.internal:8080/mcp`를 등록한다.

| 설정 | 적용 위치 |
| --- | --- |
| `JEV_API_KEY` | MCP → JEV backend Bearer 인증 |
| `JEV_MCP_TOKEN` | Client → MCP Bearer 인증. loopback 밖 bind 시 필수 |
| `JEV_MCP_ALLOWED_HOSTS` | 쉼표로 구분한 HTTP Host 허용 값. 실제 client/gateway가 보내는 host와 port |
| `JEV_MCP_ALLOWED_ORIGINS` | browser client의 Origin 허용 값. 브라우저 client를 쓸 때 설정 |

서버는 기본적으로 loopback Host/Origin만 허용한다. 외부 DNS로 서비스할 때 실제 공개 hostname을 추가한다. HTTP는 stateless mode이며 요청 간 세션 상태나 대화 history를 보관하지 않는다. Bearer 인증은 모든 HTTP method에 적용한다. remote 통신은 사내 TLS gateway 등으로 보호한다. 이 간단한 token 인증은 OAuth discovery/login 서버가 아니므로 client가 static header를 지원해야 한다.

Claude Code remote 등록:

```bash
claude mcp add --transport http jev-local https://jev-mcp.internal/mcp \
  --header "Authorization: Bearer YOUR_MCP_ACCESS_TOKEN"
```

Codex config 예제는 MCP token 값을 직접 기록하지 않고 환경변수를 참조한다:

```toml
[mcp_servers.jev-local]
url = "https://jev-mcp.internal/mcp"
bearer_token_env_var = "JEV_MCP_TOKEN"
tool_timeout_sec = 90
```

## Docker

```bash
docker build -t jev-decide-mcp:0.1.0 integrations/jev-decide-mcp
docker run --rm -p 8080:8080 \
  -e JEV_BASE_URL=http://your-vllm-service:8000 \
  -e JEV_MCP_TOKEN \
  -e JEV_MCP_ALLOWED_HOSTS=jev-mcp.internal:8080 \
  jev-decide-mcp:0.1.0
```

컨테이너의 localhost는 vLLM 서버 호스트의 localhost와 다르다. 컨테이너가 접근 가능한 service DNS/gateway를 지정한다. 이미지는 non-root UID 10001로 실행하며 runtime에는 GPU가 필요하지 않다. Dockerfile은 runtime dependency snapshot [requirements.txt](../requirements.txt)를 사용한다. Docker build 자체는 이 PR 개발 환경에서 실행하지 않았으며 Python wheel build와 두 transport는 테스트했다.

Kubernetes에서는 별도 Deployment/Service로 이 이미지를 실행하고 `JEV_BASE_URL`은 기존 JEV service를 지정한다. API/MCP key는 Secret의 `env.valueFrom.secretKeyRef`로 주입한다. Service/Ingress의 실제 Host를 allowed hosts에 넣는다. chart나 기존 vLLM 배포는 이 패키지가 수정하지 않는다. HTTP probe로 `/mcp`에 평범한 GET을 보내면 정상 health 확인이 되지 않는다. MCP initialization + info call을 외부 readiness 점검으로 사용하거나 TCP probe로 프로세스 listen 여부만 확인한다.

## 실제 엔진 smoke test

설치한 venv Python으로 실행한다. 세 번의 실제 inference를 수행한다.

```bash
export JEV_BASE_URL='http://your-vllm-service:8000'
# 인증 필요 시 JEV_API_KEY와, 긴 작업이면 JEV_TIMEOUT_SECONDS도 설정
/opt/jev-decide-mcp-venv/bin/python integrations/jev-decide-mcp/examples/smoke.py
```

official MCP client → subprocess stdio → 배포 backend를 거쳐 tool discovery, info, `choice/noul/score`를 확인한다. 출력에는 raw probabilities, usage, backend elapsed time, request count가 포함된다. `PASS`는 API/transport 성공을 의미하며 업무 정확도 합격을 뜻하지 않는다. 실제 업무 evidence로 올바른 결과를 얻는지는 별도로 확인한다.

## 로그와 실패 진단

- stdout은 stdio MCP protocol 전용이다. 운영 log는 stderr로 출력한다.
- HTTP method/status/elapsed만 기록한다. evidence, question, reasoning, token, URL, upstream error body는 log에 쓰지 않는다.
- JSON error의 상세 원인은 MCP tool error에서 확인한다. 결과가 `isError=true`면 분류/승인 성공으로 사용하지 않는다.
- HTTP 404: gateway가 `/v1/decide` 또는 `/v1/decide/info`를 노출하는지 확인한다. 일반 OpenAI chat 서버에 연결한 경우도 있다.
- 401: backend key와 MCP key 중 어느 구간이 실패했는지 구분한다.
- 421/403: HTTP Host/Origin allowlist를 실제 client 요청 값에 맞춘다.
- timeout: vLLM queue/컨텍스트 길이/활성 thinking 및 MCP client timeout을 함께 확인한다. 서버는 자동 재시도하지 않는다.
- controls가 효과 없음: 배포된 `serve_decide.py` revision과 info capabilities를 확인한다. 오래된 source가 필드를 무시할 수 있다.

`JEV_TIMEOUT_SECONDS`는 기본 60초이며 MCP client deadline보다 작게 잡는다. adaptive thinking이나 매우 긴 state는 별도 budget과 deadline 설계가 필요하다. `thinking="off"`를 명시하면 backend default가 바뀌어도 System 1 경로를 요청할 수 있다.

## 폐쇄망 배포

외부 통신 가능한 **동일 OS/architecture/Python** 환경에서 wheel과 dependency wheelhouse를 준비하고 내부로 반입한다. runtime dependency snapshot은 Python 3.12 Linux 기준이다. 다른 플랫폼은 해당 target에서 다시 resolve/download하고 검증한다.

```bash
python3 -m pip install build
cd integrations/jev-decide-mcp
python3 -m build --wheel --outdir /tmp/jev-release
python3 -m pip download -r requirements.txt --dest /tmp/jev-wheelhouse

# 내부 서버에서 wheelhouse와 패키지 wheel을 반입한 뒤
python3 -m pip install --no-index --find-links /path/to/jev-wheelhouse \
  /path/to/jev-release/jev_decide_mcp-0.1.0-py3-none-any.whl
```

## 현업에서 효과를 판정하는 방법

먼저 하나의 반복 업무를 선택한다. 예를 들어 CI failure triage, diff의 명시적 규칙 위반 확인, 후보 runbook 선택이다. 기존 agent가 실제로 쓰는 판단 단계와 evidence를 그대로 사용하며 JEV에게 새 후보/원인/사실을 발명하게 하지 않는다. 첫 도입은 결과를 보여주는 관찰 모드로 시작한다.

1. 사람이 라벨링한 대표 사례와 최근 실패 사례를 준비한다. 정답, context 부족, 모호한 경우를 포함한다.
2. baseline(agent 단독)과 JEV integration을 동일 evidence·동일 후보로 비교한다. 임계값을 정하는 validation set과 평가 set을 분리한다.
3. 전체 workflow wall time p50/p95, main agent 생성 token, JEV prompt/completion token, 요청 수, 정확도/오탐/누락, 추가 검토 비율을 기록한다. MCP 호출 추가 비용도 포함한다.
4. agent가 도구를 실제 호출한 비율과 JEV 결과 때문에 다음 행동이 달라진 비율을 확인한다. tool 등록 수만으로 효과를 평가하지 않는다.
5. 업무별 허용 오탐/누락 기준과 coverage를 만족할 때만 해당 판단 단계의 기존 generation을 줄인다.

낮은 확률이면 기존 상세 추론/사람 검토로 넘길 수 있다. 모든 업무에 0.8/0.9를 일괄 적용하지 말고 실제 라벨/비용 기준으로 threshold를 검증한다. `noul`에서는 P(true)를 판정 방향에 맞게 적용하고 `choice`에서는 최대 확률만으로 후보 누락을 탐지할 수 없으므로 uncertain 후보와 evidence 충분성 검사가 필요하다.

MCP 호출은 host의 tool invocation 비용을 추가한다. 한 번의 사소한 선택이거나 host가 이미 판단을 끝낸 뒤 검증한다면 이득이 작을 수 있다. 같은 state에 대한 반복 판정은 backend prefix caching을 검토하되 MCP가 서버 설정을 변경하거나 GPU 성능을 보장하지 않는다. 업무 필수 gate를 보장하려면 harness 코드의 명시적 단계가 필요하다.
