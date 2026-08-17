from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "helm/templates/deployment-vllm-multi.yaml"
text = PATH.read_text()


def replace_exact(old: str, new: str, label: str) -> None:
    global text
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"{label}: expected source fragment not found")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one source fragment, found {count}")
    text = text.replace(old, new, 1)


# 1) Keep only the stable command skeleton + generic extraArgs before the
# legacy LMCache block. Model path and vLLM engine options are supplied by the
# profile YAML referenced from vllmConfig.extraArgs.
serve_marker = '          - "serve"\n'
lmcache_marker = '          {{- if $modelSpec.lmcacheConfig }}\n'
serve_pos = text.find(serve_marker)
if serve_pos < 0:
    raise RuntimeError("vLLM command: serve marker not found")
lmcache_pos = text.find(lmcache_marker, serve_pos)
if lmcache_pos < 0:
    raise RuntimeError("vLLM command: LMCache marker not found after serve")

command_block = '''          - "serve"
          - "--host"
          - "0.0.0.0"
          - "--port"
          - {{ include "chart.container-port" . | quote }}
          {{/* Downstream: model path and engine options are externalized to profile YAML. */}}
          {{- with $modelSpec.vllmConfig }}
          {{- if .extraArgs }}
          {{- range .extraArgs }}
          - {{ . | quote }}
          {{- end }}
          {{- end }}
          {{- end }}
'''

current_command = text[serve_pos:lmcache_pos]
if current_command != command_block:
    text = text[:serve_pos] + command_block + text[lmcache_pos:]

# The chat-template CLI flag is also part of the profile-driven runtime config.
replace_exact(
    '''          {{- if $modelSpec.chatTemplate }}
          - "--chat-template"
          - "/templates/{{ $modelSpec.chatTemplate }}"
          {{- end }}
''',
    '',
    "chat-template command removal",
)

# 2) PYTHONHASHSEED and HF_HOME are defined once in global-values.yaml.
replace_exact(
    '''          - name: PYTHONHASHSEED
            value: "123"
          - name: HF_HOME
            {{- if hasKey $modelSpec "pvcStorage" }}
            value: /data
            {{- else }}
            value: /tmp
            {{- end }}
''',
    '',
    "global env ownership",
)

# 3) /dev/shm is always mounted for non-Ray vLLM deployments. It no longer
# depends on tensorParallelSize or any other optional volume source.
replace_exact(
    '''          {{- if or (hasKey $modelSpec "pvcStorage") (and $modelSpec.vllmConfig (hasKey $modelSpec.vllmConfig "tensorParallelSize")) (hasKey $modelSpec "chatTemplate") (hasKey $modelSpec "extraVolumeMounts") }}
          volumeMounts:
          {{- end }}''',
    '''          volumeMounts:''',
    "unconditional volumeMounts",
)

replace_exact(
    '''          {{- with $modelSpec.vllmConfig }}
          {{- if hasKey $modelSpec.vllmConfig "tensorParallelSize"}}
          - name: shm
            mountPath: /dev/shm
          {{- end}}
          {{- end}}''',
    '''          - name: shm
            mountPath: /dev/shm''',
    "unconditional shm mount",
)

replace_exact(
    '''      {{- if or (hasKey $modelSpec "pvcStorage") (and $modelSpec.vllmConfig (hasKey $modelSpec.vllmConfig "tensorParallelSize")) (hasKey $modelSpec "chatTemplate") (hasKey $modelSpec "extraVolumes") (hasKey $.Values "sharedPvcStorage") }}
      volumes:
      {{- end}}''',
    '''      volumes:''',
    "unconditional volumes",
)

PATH.write_text(text)

# Hard assertions against regressions in the final non-Ray template.
final = PATH.read_text()
command_end = final.find(lmcache_marker, final.find(serve_marker))
command = final[final.find(serve_marker):command_end]
for forbidden in (
    '$modelSpec.modelURL',
    '--enable-chunked-prefill',
    '--no-enable-chunked-prefill',
    '--enable-prefix-caching',
    '--max-model-len',
    '--tensor-parallel-size',
    '--gpu_memory_utilization',
):
    if forbidden in command:
        raise RuntimeError(f"vLLM command still contains template-managed option: {forbidden}")

if '- name: PYTHONHASHSEED' in final or '- name: HF_HOME' in final:
    raise RuntimeError("template-local PYTHONHASHSEED/HF_HOME still present")

if '''          - name: shm
            mountPath: /dev/shm''' not in final:
    raise RuntimeError("unconditional /dev/shm mount is missing")
if '''        - name: shm
          hostPath:
            path: /dev/shm
            type: Directory''' not in final:
    raise RuntimeError("hostPath-backed /dev/shm volume is missing")

print("Non-Ray vLLM deployment template finalized and validated.")
