import base64
import hashlib
import json
import subprocess
import sys
import zoneinfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_MODULES = (
    "__init__", "brain", "danger_service", "decide", "geo", "payloads",
    "profiles", "state", "timefmt", "tme",
)


def kyiv_tzif() -> str:
    for base in zoneinfo.TZPATH:
        path = Path(base) / "Europe" / "Kyiv"
        if path.is_file():
            return f'DATA = "{base64.b64encode(path.read_bytes()).decode()}"\n'
    raise SystemExit("Europe/Kyiv tz file not found on this machine")


def brain_files() -> dict[str, str]:
    files = {
        "worker.py": (ROOT / "edge" / "worker.py").read_text(),
        "kyiv_tzif.py": kyiv_tzif(),
    }
    for name in APP_MODULES:
        files[f"app/{name}.py"] = (ROOT / "app" / f"{name}.py").read_text()
    danger = ROOT / "vendor" / "custom_components" / "aerial_danger" / "danger"
    for path in sorted(danger.glob("*.py")):
        files[f"custom_components/aerial_danger/danger/{path.name}"] = path.read_text()
    return files


def edge_files() -> dict[str, str]:
    edge = ROOT / "edge"
    subprocess.run(["npm", "run", "--silent", "build"], cwd=edge, check=True)
    wasm = (edge / "dist" / "mtcute.wasm").read_bytes()
    return {
        "edge.js": (edge / "dist" / "edge.js").read_text(),
        "mtcute.wasm": base64.b64encode(wasm).decode(),
    }


def digest(name: str, text: str) -> str:
    data = base64.b64decode(text) if name.endswith(".wasm") else text.encode()
    return hashlib.sha256(data).hexdigest()[:16]


def main() -> None:
    files = {"brain": brain_files(), "edge": edge_files()}
    hashes = {
        f"{group}/{name}": digest(name, text)
        for group, group_files in files.items() for name, text in group_files.items()
    }
    Path(sys.argv[1]).write_text(json.dumps({"files": files, "hashes": hashes}, ensure_ascii=False))
    print(json.dumps(hashes))


if __name__ == "__main__":
    main()
