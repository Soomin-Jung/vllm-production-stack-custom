from pathlib import Path
import re
import runpy

root = Path(__file__).resolve().parents[1]
values = root / "helm/values.yaml"
text = values.read_text()

# `servicePort: 80` appears in both servingEngineSpec and routerSpec. Scope the
# production override to routerSpec before running the strict rewrite script.
if "  servicePort: 9400" not in text:
    pattern = r'(routerSpec:\n.*?  # -- Service port\n)  servicePort: 80'
    text, count = re.subn(pattern, r'\1  servicePort: 9400', text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"routerSpec.servicePort: expected 1 match, found {count}")
    values.write_text(text)

runpy.run_path(str(root / "scripts/apply-production-baseline.py"), run_name="__main__")
