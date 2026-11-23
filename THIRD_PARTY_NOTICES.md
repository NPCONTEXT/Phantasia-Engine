# 第三方声明

幻想引擎自己的代码按 MIT 许可发布，见 [LICENSE](LICENSE)。下面这些不是作者写的，或者不随 MIT 授权。

## 仓库里带着的

| 文件 | 是什么 | 来源 | 许可证 |
|---|---|---|---|
| `bh3_auto/vendor/maatouch/universal/maatouch` | 推到设备上跑的触控程序（多指按住、滑动） | MaaTouch 发布页的 `maatouch`（和 v1.1.0 那份同名、同大小），没改过：https://github.com/MaaAssistantArknights/MaaTouch | Apache-2.0，全文在 [third_party/MaaTouch-LICENSE](third_party/MaaTouch-LICENSE) |
| `bh3_auto/models/best.onnx` | 乐土里认门、认刻印的检测模型 | Maa_bbb 仓库的 `assets/resource/base/model/detect/best.onnx`（逐字节相同），没改过：https://github.com/miaojiuqing/Maa_bbb | MIT，Copyright (c) 2024 MaaXYZ，全文在 [third_party/Maa_bbb-LICENSE](third_party/Maa_bbb-LICENSE) |

## 安装时下载、不在仓库里的

| 包 | 用在哪 | 许可证 |
|---|---|---|
| PySide6（Qt for Python）6.8.3 | 界面 | LGPL-3.0（另有 GPL 和商业许可） |
| OpenCV（opencv-python） | 脚本看图、比对模板 | Apache-2.0 |
| NumPy | 同上 | BSD-3-Clause |
| ONNX Runtime | 跑上面那个检测模型 | MIT |

另外用到你自己装的 adb（Android SDK Platform-Tools）、ffmpeg，和 macOS 自带的 Vision（识字）。

## 不随 MIT 授权的

- 模板图（`bh3_auto/assets/`，各首歌目录下的 `tpl/`、`nav_tpl/`、`adv_tpl/`）是从游戏画面里裁出来的按钮和图标，只用来认画面。这些图的权利属于各游戏的权利人。
- 雨澜的头像（`logo.png`）和她这个角色是作者的原创，魔方标志（`mark.png`、程序图标）也是作者的，都不随 MIT 授权；介绍、链接本项目时可以用。
- 本项目和崩坏3、胜利女神：新的希望、绝区零的开发商、运营商没有关系。

这份声明是照作者对各许可证的理解写的，不是法律意见。哪里对不上，请在本仓库提 issue。
