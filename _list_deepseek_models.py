"""List models available on the DeepSeek API."""
import os, urllib.request, json
from pathlib import Path

# Load .env
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

api_key = os.environ.get("DEEPSEEK_API_KEY")
if not api_key:
    raise SystemExit("DEEPSEEK_API_KEY not set")

req = urllib.request.Request(
    "https://api.deepseek.com/models",
    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
)

with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())

models = data.get("data", [])
print(f"{'ID':<30}  {'Owned by'}")
print("-" * 45)
for m in models:
    print(f"  {m['id']:<28}  {m.get('owned_by', '')}")
print(f"\n{len(models)} model(s) found.")
