# 手机提醒（可选）

到点看播放记录：该做的日常、周常没做完，就用 Server酱推一条到微信，标题是「【雨澜】…」。做完了不发。什么时候提醒、怎么算做完，写在 `remind.py` 开头。

它不在界面里，是一个单独的定时任务（macOS 的 launchd）。不装不影响别的。

## 装

下面的命令都在项目根目录运行。

1. 到 Server酱（sct.ftqq.com）拿 SendKey（SCT 开头的一串），存成只有这一行的文件 `~/.config/phantasia-engine/serverchan_sendkey`。目录没有就先建：

```bash
mkdir -p ~/.config/phantasia-engine
```

2. 先干跑一次，看它现在会发什么（不发、不写状态）。要先在幻想引擎里播放过至少一首：有了播放记录 `runs/history.jsonl` 它才跑得起来，没有记录时会直接报找不到这个文件（故意的，读不到记录就不该发）：

```bash
/usr/bin/python3 tasks/2026-10-19-主动提醒/remind.py --dry
```

3. 把定时任务的模板填上路径，放进 LaunchAgents，再加载：

```bash
sed -e "s#__PROJECT_DIR__#$PWD#g" -e "s#__HOME__#$HOME#g" tasks/2026-10-19-主动提醒/launchd/io.github.npcontext.phantasia-engine.remind.plist > ~/Library/LaunchAgents/io.github.npcontext.phantasia-engine.remind.plist
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.npcontext.phantasia-engine.remind.plist
```

4. 发一条测试消息（会用掉 1 条 Server酱额度）：

```bash
/usr/bin/python3 tasks/2026-10-19-主动提醒/remind.py --selftest
```

## 停

```bash
launchctl bootout gui/$(id -u)/io.github.npcontext.phantasia-engine.remind
```

```bash
rm ~/Library/LaunchAgents/io.github.npcontext.phantasia-engine.remind.plist
```

## 注意

- 项目放在「桌面」「文稿」「下载」里时，后台任务会被 macOS 挡住、读不到播放记录。到「系统设置 → 隐私与安全性 → 完全磁盘访问权限」里加上 `/usr/bin/python3`，或者把项目放到别处。
- 后台任务的输出在 `~/Library/Logs/phantasia-engine-remind.log`。
- 改了提醒规则以后跑一遍测试（不联网、不发消息）：

```bash
/usr/bin/python3 -m unittest discover -s tasks/2026-10-19-主动提醒/tests
```
