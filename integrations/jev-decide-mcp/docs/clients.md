# 클라이언트 설치와 업무 사용

## 1. 서버 패키지 설치

클라이언트가 실행되는 PC/개발 서버에서, 저장소를 내려받은 다음 설치한다. 경로는 자신의 환경에 맞게 변경한다.

```bash
git clone https://github.com/Soomin-Jung/vllm-production-stack-custom.git
cd vllm-production-stack-custom
# PR 검증 시 사용. main 병합 후에는 main으로 설치 가능
git switch --track origin/feat/jev-decide-mcp
python3 -m venv /opt/jev-decide-mcp-venv
/opt/jev-decide-mcp-venv/bin/python -m pip install ./integrations/jev-decide-mcp
```

로컬 MCP 프로세스에서 backend service DNS/IP로 접근할 수 있어야 한다. Kubernetes ClusterIP/DNS는 보통 클러스터 밖 PC에서 직접 접근할 수 없으므로 사내 gateway, VPN, port-forward 등 사용 가능한 접근 경로를 `JEV_BASE_URL`에 지정한다.

## 2. Claude Code

```bash
claude mcp add --scope user \
  --env JEV_BASE_URL=http://your-vllm-service:8000 \
  --transport stdio jev-local -- /opt/jev-decide-mcp-venv/bin/jev-decide-mcp
claude mcp get jev-local
```

인증이 필요하면 MCP 프로세스 환경에 `JEV_API_KEY`를 추가한다. 키를 공유 문서나 Git에 넣지 않는다. 팀 프로젝트 `.mcp.json` 예제는 [claude.mcp.json](../examples/claude.mcp.json)에 있다. 이미 있는 파일은 덮어쓰지 않고 `mcpServers.jev-local` 항목을 병합한다. Claude 세션에서 `/mcp`로 연결 상태를 확인한다. tool은 일반적으로 `mcp__jev-local__jev_decide`로 나타난다.

## 3. Codex CLI

```bash
codex mcp add jev-local \
  --env JEV_BASE_URL=http://your-vllm-service:8000 \
  -- /opt/jev-decide-mcp-venv/bin/jev-decide-mcp
codex mcp list
```

설정 파일 방식은 [codex.config.toml](../examples/codex.config.toml)을 기존 `~/.codex/config.toml`에 병합한다. 예제의 `env_vars`는 부모 프로세스의 `JEV_API_KEY`와 CA 설정을 전달한다. 필요 시 tool timeout을 backend deadline보다 크게 설정한다. configuration 변경 후 새 세션에서 `/mcp`로 활성 도구를 확인한다.

MCP 도구를 지원하는 다른 클라이언트에서도 같은 실행 파일·환경변수 또는 [원격 HTTP URL](operations.md)을 등록하면 된다.

## 4. JEV 사용 스킬 설치(선택)

MCP 등록은 도구를 노출하지만 도구 선택 기준까지 강제하지 않는다. 저장소에 포함된 동일한 `jev-decide` 스킬을 사용하는 클라이언트에 복사하면 입력 구성, 결과 해석, escalation 원칙을 함께 제공할 수 있다.

Codex 및 Agent Skills 호환 클라이언트:

```bash
mkdir -p ~/.agents/skills/jev-decide
cp integrations/jev-decide-mcp/skills/jev-decide/SKILL.md ~/.agents/skills/jev-decide/SKILL.md
```

Claude Code:

```bash
mkdir -p ~/.claude/skills/jev-decide
cp integrations/jev-decide-mcp/skills/jev-decide/SKILL.md ~/.claude/skills/jev-decide/SKILL.md
```

스킬은 MCP 서버나 credential을 설치하지 않는다. 앞 절의 MCP 등록도 완료해야 한다. 프로젝트 단위 설치가 필요하면 각 클라이언트의 project skill 경로에 같은 디렉터리를 복사한다.

## 5. 첫 호출

클라이언트 채팅에서 다음처럼 **도구 이름과 사용할 evidence를 명시**한다.

> jev-local의 jev_decide_info로 서버 옵션을 확인해줘. 이어서 아래 CI 실패 로그를 jev_decide로 분류해줘. kind=choice, thinking=off, question="Which category best explains this failure?", options=["test_failure: assertion or expected-result mismatch", "infrastructure: network, runner, storage or service outage", "uncertain: insufficient evidence"]. state에는 실제 실패 로그를 넣고 선택과 전체 확률을 보여줘. uncertain이 선택되면 추가 로그를 요청해줘.

직접 tool-call body 예제:

```json
{
  "kind": "choice",
  "state": {"job": "unit-tests", "log": "Connection refused to test database before any test ran"},
  "question": "Which category best explains this failure?",
  "options": [
    "test_failure: assertion or expected-result mismatch",
    "infrastructure: network, runner, storage or service outage",
    "uncertain: insufficient evidence"
  ],
  "thinking": "off"
}
```

이 예제는 실행된 inference 결과를 주장하지 않는다. backend가 준 `choice`, `choice_index`, `probabilities`를 확인한다. agent가 다른 도구를 호출하거나 자체 답변을 한다면 실제 tool-call 기록을 보고 JEV 호출 여부부터 확인한다.

`noul`은 명제를 확인할 때 사용한다. `state`에 diff와 coding rule을 함께 넣고 `question="Does this diff violate the supplied rule?"`처럼 묻는다. `probabilities[1]`이 violation의 참일 확률이다. 명제의 방향을 뒤집으면 정책의 의미도 바뀐다.

`score`는 rubric을 question/state에 명시한다. 예를 들어 0=영향 없음, 1=단일 테스트, 2=개발 작업, 3=일부 사용자, 4=다수 사용자, 5=전체 서비스 중단의 기준을 주고 severity를 판정한다. `choice`가 modal score이며 기대 점수와 같지 않다.

## 6. 반복 사용 지침

자동 호출에 가까운 경험을 원하면 작업 프로젝트의 `AGENTS.md`(Codex) 또는 `CLAUDE.md`(Claude Code)에 아래 지침을 자신의 정책과 함께 추가한다. 이 PR은 사용자 클라이언트 설정이나 해당 파일을 자동 변경하지 않는다.

```text
For CI failure triage and explicit rule/claim checks:
1. Collect the relevant logs, diff, rule or source evidence first.
2. Call jev_decide using thinking="off" and a precise bounded question.
3. For choice, describe when each option applies and include uncertain when evidence can be missing.
4. Show the returned choice and probabilities. Do not replace them with your own confidence.
5. If the tool fails, evidence is insufficient, or confidence is below our validated threshold,
   continue with detailed review; do not treat the decision as an approval.
6. The host retains execution policy. Do not execute a command just because JEV selected an option.
```

이 지침은 agent의 도구 사용을 유도한다. **모든 요청에 반드시 호출되는 middleware/hook은 아니다.** 반드시 수행해야 하는 검증은 agent harness/업무 서비스 코드에서 직접 API 또는 MCP `call_tool`을 호출하고 결과를 확인하는 단계로 구현해야 한다. 등록만으로 추론 token이나 latency가 감소한다고 판단하지 않는다. 이를 측정하는 기준은 [효과 검증](operations.md)에 있다.

## 공식 설정 문서

- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Codex MCP](https://developers.openai.com/codex/mcp)
- [MCP server 개발 문서](https://modelcontextprotocol.io/docs/develop/build-server)
