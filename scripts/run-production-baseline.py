from pathlib import Path
import re
import runpy

root = Path(__file__).resolve().parents[1]

# `servicePort: 80` appears in both servingEngineSpec and routerSpec. Scope the
# production override to routerSpec before running the strict rewrite script.
values = root / "helm/values.yaml"
text = values.read_text()
if "  servicePort: 9400" not in text:
    pattern = r'(routerSpec:\n.*?  # -- Service port\n)  servicePort: 80'
    text, count = re.subn(pattern, r'\1  servicePort: 9400', text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"routerSpec.servicePort: expected 1 match, found {count}")
    values.write_text(text)

# Head and worker use the same indentation for the model extraVolumeMounts
# fragment. Apply both copies together so the strict script cannot silently
# update only one Ray group.
ray = root / "helm/templates/ray-cluster.yaml"
text = ray.read_text()
old = '''              {{- if hasKey $modelSpec "extraVolumeMounts" }}
              {{- toYaml $modelSpec.extraVolumeMounts | nindent 14 }}
              {{- end }}'''
new = '''              {{- $modelMountMap := dict }}
              {{- if hasKey $modelSpec "extraVolumeMounts" }}
              {{- range $e := $modelSpec.extraVolumeMounts }}
              {{- $_ := set $modelMountMap $e.name $e }}
              {{- end }}
              {{- end }}
              {{- $mergedMountMap := mergeOverwrite (deepCopy $globalMountMap) $modelMountMap }}
              {{- $mergedMountList := list }}
              {{- range $k, $v := $mergedMountMap }}
              {{- $mergedMountList = append $mergedMountList $v }}
              {{- end }}
              {{- with $mergedMountList }}
              {{- toYaml . | nindent 14 }}
              {{- end }}'''
if old in text:
    count = text.count(old)
    if count != 2:
        raise RuntimeError(f"Ray extraVolumeMounts: expected 2 matches, found {count}")
    ray.write_text(text.replace(old, new))

runpy.run_path(str(root / "scripts/apply-production-baseline.py"), run_name="__main__")
