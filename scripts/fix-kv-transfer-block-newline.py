from pathlib import Path

path = Path(__file__).resolve().parents[1] / "helm/templates/deployment-vllm-multi.yaml"
text = path.read_text()
old = '''          {{- if $modelSpec.lmcacheConfig }}
          {{-   if $modelSpec.lmcacheConfig.enabled }}
          {{-     $kvTransferConfig := dict "kv_connector" "LMCacheConnectorV1" "kv_role" $kv_role }}
          {{-     if $modelSpec.lmcacheConfig.enable_kv_load_failure_policy }}
          {{-       $_ := set $kvTransferConfig "kv_load_failure_policy" "recompute" }}
          {{-     end }}
          - "--kv-transfer-config"
          {{/* kv_role 및 실패 정책은 Helm 렌더 시 동적으로 결정되므로 KV transfer config는 profile이 아닌 template에서 관리 */}}
          - |
            {{ $kvTransferConfig | toJson }}
          {{   end }}
          {{ end }}
'''
new = '''          {{- if $modelSpec.lmcacheConfig }}
          {{-   if $modelSpec.lmcacheConfig.enabled }}
          - "--kv-transfer-config"
          {{/* kv_role 및 실패 정책은 Helm 렌더 시 동적으로 결정되므로 KV transfer config는 profile이 아닌 template에서 관리 */}}
          - |
            {"kv_connector": "LMCacheConnectorV1",
             "kv_role": "{{ $kv_role }}"{{ if $modelSpec.lmcacheConfig.enable_kv_load_failure_policy }},
             "kv_load_failure_policy": "recompute"{{ end }}
            }
          {{-   end }}
          {{- end }}
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected exactly one current KV transfer block, found {count}")
path.write_text(text.replace(old, new, 1))
