# JEV Decide MCP

Self-hosted AutoTrust JEV-27B의 `POST /v1/decide`를 Codex·Claude Code·다른 MCP 클라이언트에 연결하는 독립 Python 패키지다. 사용자 지정 백엔드만 호출하며 TypeSafe·OpenRouter 등 외부 provider로 전환하지 않는다.

## 시작 순서

1. [설치와 클라이언트 등록](docs/clients.md): Python 설치, Codex/Claude Code 설정, 실제 사용 예제.
2. [API 계약](docs/contract.md): 요청 필드, 확률 순서, 최신/기존 백엔드 차이, 오류 의미.
3. [운영과 효과 검증](docs/operations.md): HTTP/Docker 배포, 로그, 폐쇄망, smoke test, 현업 평가.
4. [구현 검증 기록](docs/validation.md): 수행한 테스트와 실제 엔진 검증의 경계.
5. [JEV agent skill](skills/jev-decide/SKILL.md): 입력·출력 해석, 지원 기능, 호출 판단과 활용 절차.

## 제공 도구

| MCP tool | 호출하는 API | 용도 |
| --- | --- | --- |
| `jev_decide` | `POST /v1/decide` | `choice`, `noul`, `score`와 백엔드 reasoning controls |
| `jev_decide_info` | `GET /v1/decide/info` | protocol, 옵션 한도, temperature, defaults 조회 |

`jev_decide`의 인자는 REST body와 같은 평면 JSON이다. 응답 JSON을 MCP `structuredContent`와 JSON 텍스트에 동일하게 넣는다. 확률 재계산, 옵션 정렬, 백엔드 model 이름 변경, implicit System 2 fallback을 하지 않는다. 누락 필드는 누락 상태로 전송하고 명시한 `null`은 유지한다.

MCP 서버는 판정 도구를 제공한다. 에이전트의 도구 호출 여부·실행 정책은 클라이언트가 결정한다. 반복 호출 지침과 강제 실행이 필요한 경우의 경계는 클라이언트 문서에 설명한다.

## 빠른 설치

Python 3.11 이상. 저장소를 내려받은 위치에서 실행한다.

```bash
python3 -m venv /opt/jev-decide-mcp-venv
/opt/jev-decide-mcp-venv/bin/python -m pip install ./integrations/jev-decide-mcp

export JEV_BASE_URL='http://your-vllm-service:8000'
/opt/jev-decide-mcp-venv/bin/jev-decide-mcp
```

마지막 명령은 stdio MCP 서버이므로 JSON-RPC 클라이언트의 연결을 기다린다. 단순 실행 시 출력이 없는 것이 정상이다. GPU, vLLM, 모델 weights는 MCP 프로세스에 필요하지 않다.

## 개발 검증

```bash
python3 -m venv /tmp/jev-mcp-dev
/tmp/jev-mcp-dev/bin/python -m pip install -e './integrations/jev-decide-mcp[dev]'
cd integrations/jev-decide-mcp
/tmp/jev-mcp-dev/bin/pytest
/tmp/jev-mcp-dev/bin/ruff check .
/tmp/jev-mcp-dev/bin/ruff format --check .
/tmp/jev-mcp-dev/bin/python -m build --outdir /tmp/jev-mcp-dist
```

테스트는 로컬 mock backend와 공식 MCP SDK를 사용한다. 실제 모델의 정확도·GPU latency는 검증하지 않는다. 배포된 엔진에는 [smoke script](examples/smoke.py)를 실행한다.
