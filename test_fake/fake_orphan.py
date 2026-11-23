"""假工具：自己先退出，留下一个占着输出管道的子进程（sleep 30），测哨兵收尾会不会把它收掉、界面会不会卡在「正在播放」。"""
import subprocess, sys
p = subprocess.Popen(["/bin/sleep", "30"])          # 继承 stdout = 幻想引擎的日志管道
print(f"留下子进程 {p.pid}", flush=True)
sys.exit(0)
