"""自动清理旧的战斗截图和乐土日志（11-03 作者：「加上自动清理，留最近14天」）。
启动时在后台线程跑（runner.cleanup_stale 最后调 clean()），规矩和 11-03 手动清理一样：
- 只管三处，每场 / 每次一个文件夹：超弦、记忆战场的 logs.nosync（战斗脚本每秒存一张画面），乐土的 bh3_auto/logs。
- 文件夹里最后一次写入超过 KEEP_DAYS 天才动；demo_*（用户示范时录的）不动；任务交接、说明、代码里提到过这个文件夹名的不动。
- 只放进废纸篓（macOS 自带的 /usr/bin/trash，访达里能「放回原处」）；没有这个命令就什么都不做，绝不直接删。
- 放进去了哪些、没放成的，记进 runs/cleanup.log。"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from core import registry

AGG_DIR = registry.AGG_DIR
ROOTS = (AGG_DIR / "tasks" / "2026-10-16-超弦空间" / "logs.nosync",
         AGG_DIR / "tasks" / "2026-10-10-记忆战场" / "logs.nosync",
         AGG_DIR / "bh3_auto" / "logs")
REF_DIRS = (AGG_DIR / "tasks", AGG_DIR / "docs", AGG_DIR / "bh3_auto" / "docs",
            AGG_DIR / "bh3_auto" / "scripts", AGG_DIR / "bh3_auto" / "tests")
REF_SUFFIX = (".md", ".py", ".txt", ".json")
SKIP_DIRS = {".venv", ".git", "__pycache__", "frames", "shots"}      # 另外 *.nosync 也不读
KEEP_DAYS = 14
TRASH = "/usr/bin/trash"
LOG = AGG_DIR / "runs" / "cleanup.log"


def newest_mtime(p: Path) -> float:
    """文件夹本身和里面所有东西最后一次修改的时刻。"""
    t = p.stat().st_mtime
    for dp, dn, fn in os.walk(p):
        for n in dn + fn:
            try:
                t = max(t, os.stat(os.path.join(dp, n), follow_symlinks=False).st_mtime)
            except OSError:
                pass
    return t


def referenced_text(dirs=REF_DIRS) -> str:
    """交接、说明、代码的全文（看文件夹名有没有被提到）；不读日志、截图、虚拟环境。11-03 量过约 680 个文件、7.5 MB。"""
    parts = []
    for base in dirs:
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.endswith(".nosync")]
            for f in fn:
                if f.endswith(REF_SUFFIX):
                    try:
                        parts.append(Path(dp, f).read_text(encoding="utf-8", errors="ignore"))
                    except OSError:
                        pass
    return "\n".join(parts)


def candidates(roots=ROOTS, days=KEEP_DAYS, now=None, refs=None) -> list[Path]:
    """该放进废纸篓的文件夹。"""
    now = time.time() if now is None else now
    refs = referenced_text() if refs is None else refs
    out = []
    for root in roots:
        try:
            kids = sorted(root.iterdir())
        except OSError:
            continue
        for p in kids:
            if p.is_symlink() or not p.is_dir() or p.name.startswith(("demo_", ".")) or p.name in refs:
                continue
            try:
                if now - newest_mtime(p) > days * 86400:
                    out.append(p)
            except OSError:
                pass
    return out


def clean(roots=ROOTS, days=KEEP_DAYS, trash=TRASH, log=LOG, now=None, refs=None) -> list[Path]:
    """把旧文件夹放进废纸篓，返回放进去了的；没有 trash 命令就一个都不动。"""
    if not os.access(trash, os.X_OK):
        _log(log, f"没有 {trash}，这次不清理（不直接删）")
        return []
    todo = candidates(roots, days, now, refs)
    done, err = [], ""
    for i in range(0, len(todo), 20):
        batch = todo[i:i + 20]
        try:
            r = subprocess.run([trash] + [str(p) for p in batch], capture_output=True, text=True, timeout=120)
            err += r.stderr
        except Exception as e:
            err += f"{e!r}\n"
        done += [p for p in batch if not p.exists()]
    left = [p for p in todo if p.exists()]
    if todo:
        lines = [f"留最近 {days} 天：放进废纸篓 {len(done)} 个，没放成 {len(left)} 个"]
        lines += [f"  放进 {p}" for p in done] + [f"  没放成 {p}" for p in left]
        if left and err.strip():
            lines.append("  trash 报：" + err.strip()[:500])
        _log(log, "\n".join(lines))
    return done


def _log(log: Path, msg: str) -> None:
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass
