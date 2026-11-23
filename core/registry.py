"""工具注册表: 读取/保存 tools.json (含应用名称与 Logo 配置)"""
import json
import os
from pathlib import Path

AGG_DIR = Path(__file__).resolve().parent.parent
TOOLS_JSON = AGG_DIR / "tools.json"


def _default():
    return {
        "app": {"name": "挂机聚合助手", "logo": "logo.png", "version": "0.1.0"},
        "tools": [],
    }


def load() -> dict:
    """读 tools.json。解析失败时返回空配置并带上 "_load_error"，
    这时 save() 会拒绝写盘，避免用空配置盖掉原文件。"""
    if not TOOLS_JSON.exists():
        return _default()
    try:
        with TOOLS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("顶层不是对象")
    except Exception as e:
        data = _default()
        data["_load_error"] = f"{type(e).__name__}: {e}"
        return data
    data.setdefault("app", _default()["app"])
    data.setdefault("tools", [])
    return data


def save(data: dict):
    """写回 tools.json（先写临时文件再替换，中途出错不会留下半截文件）。"""
    if data.get("_load_error"):
        raise RuntimeError("tools.json 读取失败过，为免覆盖原文件，本次不保存")
    clean = {k: v for k, v in data.items() if not str(k).startswith("_")}
    tmp = TOOLS_JSON.with_name(TOOLS_JSON.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TOOLS_JSON)


def tool_path(workdir: str | None) -> Path:
    """配置里的相对路径基于聚合目录(AGG_DIR)解析。"""
    base = AGG_DIR
    if not workdir:
        return AGG_DIR
    p = Path(workdir)
    return (base / p).resolve() if not p.is_absolute() else p.resolve()


IS_WIN = os.name == "nt"


def win_venv_python(path: str) -> str:
    """11-23：tools.json 里按 Mac 写的 venv 解释器（…/bin/python），Windows 上在 …/Scripts/python.exe。别的原样返回。"""
    if not IS_WIN or not path:
        return path
    norm = path.replace("\\", "/")
    for tail in ("/bin/python3", "/bin/python"):
        if norm.endswith(tail):
            return norm[: -len(tail)] + "/Scripts/python.exe"
    return path


def tool_python(tool: dict, workdir: Path) -> str | None:
    """venv 解释器路径: 注意不要 resolve 符号链接!
    (.venv/bin/python 是指向 uv 基础解释器的软链, resolve 后会脱离 venv, 丢失 site-packages)"""
    py = tool.get("python") or ""
    if not py:
        return None
    p = Path(win_venv_python(py))
    if not p.is_absolute():
        p = workdir / p
    return str(p.absolute())


def resolve_command(tool: dict) -> list[str]:
    """把命令 + python/venv 组合成最终执行列表。"""
    cmd = tool.get("command") or []
    if not isinstance(cmd, list) or not cmd:
        return []
    cmd = list(cmd)
    workdir = tool_path(tool.get("workdir"))
    py = tool_python(tool, workdir)
    if py:
        cmd[0] = py
    elif IS_WIN:            # 乐土那几首：命令里直接写着 .venv/bin/python
        cmd[0] = win_venv_python(cmd[0])
    return cmd


def precheck_files(tool: dict) -> list[Path]:
    """自检用文件列表(相对 workdir)。
    11-23 Windows：Vision 识字小程序（…/ocr/ocr、ocr_rev2）不查（那边用 RapidOCR，在脚本环境里）；
    写成 Homebrew 绝对路径的工具（ffmpeg）按 PATH 找；venv 解释器换成 Scripts/python.exe。"""
    workdir = tool_path(tool.get("workdir"))
    files = [f for f in tool.get("precheck", []) if f]
    if not IS_WIN:
        return [workdir / f for f in files]
    import shutil
    out = []
    for f in files:
        norm = f.replace("\\", "/")
        if norm.endswith("/ocr/ocr") or norm.endswith("ocr_rev2"):
            continue
        if norm.startswith("/opt/homebrew/bin/") or norm.startswith("/usr/local/bin/"):
            name = norm.rsplit("/", 1)[1]
            env = os.environ.get("PE_" + name.upper())        # 11-24：和脚本一样先认 PE_FFMPEG 这种指定
            out.append(Path(env if env and os.path.isfile(env) else (shutil.which(name) or (name + ".exe"))))
            continue
        out.append(workdir / win_venv_python(f))
    return out
