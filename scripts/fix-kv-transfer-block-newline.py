from pathlib import Path

path = Path(__file__).resolve().parents[1] / "helm/templates/deployment-vllm-multi.yaml"
text = path.read_text()
old = '''            }
          {{-   end }}
          {{- end }}
          imagePullPolicy:'''
new = '''            }
          {{   end }}
          {{ end }}
          imagePullPolicy:'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected exactly one KV transfer block ending, found {count}")
path.write_text(text.replace(old, new, 1))
