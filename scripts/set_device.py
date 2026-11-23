#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""把脚本里写的设备序列号换成你的设备。

各游戏脚本里 adb 的 -s 参数是写死的（仓库里是占位 YOUR_DEVICE_SERIAL），装好后跑一次：
    python3 scripts/set_device.py            用 adb devices 里第一台在线的
    python3 scripts/set_device.py 序列号      指定一台（无线调试写 IP:端口）
换设备时再跑一次。现在写的是哪台，记在项目根目录的 .device_serial 里。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = ROOT / '.device_serial'
PLACEHOLDER = 'YOUR_DEVICE_SERIAL'
ADB = '/opt/homebrew/bin/adb'


def online():
    try:
        out = subprocess.run([ADB, 'devices'], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        sys.exit('跑不了 %s：%s' % (ADB, e))
    return [line.split()[0] for line in out.splitlines()[1:] if line.strip().endswith('device')]


def main():
    if len(sys.argv) > 1:
        new = sys.argv[1]
    else:
        devs = online()
        if not devs:
            sys.exit('没有在线的设备。连上设备、允许调试后再跑；或者直接给序列号：python3 scripts/set_device.py 序列号')
        if len(devs) > 1:
            print('在线的有 %d 台，用第一台：%s' % (len(devs), '、'.join(devs)))
        new = devs[0]
    if not re.fullmatch(r'[A-Za-z0-9._:\-]+', new):
        sys.exit('序列号里有不认识的字符：%r' % new)
    old = MARK.read_text(encoding='utf-8').strip() if MARK.exists() else PLACEHOLDER
    a, b = '"%s"' % old, '"%s"' % new
    n = 0
    for sub in ('bh3_auto', 'tasks'):
        for p in sorted((ROOT / sub).rglob('*.py')):
            if '.venv' in p.parts:
                continue
            s = p.read_text(encoding='utf-8')
            if a in s:
                p.write_text(s.replace(a, b), encoding='utf-8')
                n += 1
                print('  ' + str(p.relative_to(ROOT)))
    MARK.write_text(new + '\n', encoding='utf-8')
    print('改了 %d 个文件：%s → %s' % (n, old, new))
    if n == 0 and old != new:
        print('一个都没改到：脚本里现在写的不是 %s。搜一下各脚本里 adb 的 -s 参数，手改。' % old)


if __name__ == '__main__':
    main()
