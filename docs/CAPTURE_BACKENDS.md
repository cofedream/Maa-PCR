# 截图方案预实现与设备侧评估手册

状态：预实现已落盘（`scripts/capture_benchmark.py`、`scripts/experimental/frame_source_controller.py`）；设备侧数字未测。本文不含任何实测延迟。**生产路径仍是 `Encode | RawWithGzip = 6`、`AdbShell = 1`，本文不改变它。**

目的：把"换/加截图后端"从一句想法变成设备恢复后可以直接执行、可比较、可回退的评估流程。

## 1. Maa 可用的安卓截图后端

| 方案 | 掩码 | 无损 | Maa 速度档 | 本项目状态 | 说明 |
|---|---:|---|---|---|---|
| EncodeToFileAndPull | 1 | 是 | 慢 | 未启用 | 设备落盘再拉取 |
| Encode | 2 | 是 | 慢 | 生产候选 | `screencap -p` + PNG 解码 |
| RawWithGzip | 4 | 是 | 中 | 生产候选 | `screencap` + gzip -1 |
| RawByNetcat | 8 | 是 | 快 | 未启用 | 兼容性低，默认被 Maa 排除 |
| MinicapDirect / Stream | 16 / 32 | **否** | 快 | **排除** | JPEG 有损，损害模板匹配 |
| EmulatorExtras | 64 | 是 | 极快 | 只读诊断 | MuMu 12 / LDPlayer 9 / Androws；需厂商 SDK 绑定 |
| scrcpy（CustomController） | — | **否** | 极快 | 实验骨架 | H.264、单 display、需自写控制器 |
| iOS XDeviceBridge | — | 流有损/截图无损 | 快 | 不适用 | 仅 iOS |

生产请求集合 `6` 只表示"允许 Encode 与 RawWithGzip，连接时测速选最快"，不代表实际后端；实际后端由 `capture-performance-v2` 证据记录。

## 2. 已预实现的评估件

| 件 | 位置 | 现在能验证 | 设备恢复后能回答 |
|---|---|---|---|
| 抓帧 benchmark | [`scripts/capture_benchmark.py`](../scripts/capture_benchmark.py) | 参数、统计、JSON、拒绝无 `--yes-device`、extras 缺参；58 项单测 | 四个后端的 P50/P90、失败模式 |
| 帧源控制器骨架 | [`scripts/experimental/frame_source_controller.py`](../scripts/experimental/frame_source_controller.py) | 纯逻辑 helper、参数校验；不依赖 scrcpy/PyAV | 流式帧源接 Maa 的接口形状 |
| MuMu 只读诊断 | `PcrMuMuReadOnlyDiagnostic.cs` | 只读代码审阅 | EmulatorExtras 单帧、分辨率、像素一致性 |
| 离线 scope A/B | [`scripts/perf_scope_ab.py`](../scripts/perf_scope_ab.py) | 公开帧上的识别次数与语义差异 | 收窄 scope 的收益与回退边界 |
| 连接测速证据 | `capture_performance.py` / `PcrCapturePerformance.cs` | 解析与拒绝逻辑 | 实际后端是否为请求集合中的最快者 |

**不预实现的东西**：生产掩码改动、EmulatorExtras 正式接线、scrcpy 生产路径、GUI 界面开关。理由见 §4。

## 3. 设备侧 runbook（设备恢复后执行）

前提：同一台 MuMu、同一分辨率、同一游戏画面；每次只测一个后端；输入固定 `None`（benchmark 不点击）；失败即停，不重试、不切换。

```powershell
# 基线（生产候选）
python -X utf8 -B scripts/capture_benchmark.py --adb "C:\...\adb.exe" --serial 127.0.0.1:16384 --backend raw --samples 50 --yes-device
python -X utf8 -B scripts/capture_benchmark.py --adb "C:\...\adb.exe" --serial 127.0.0.1:16384 --backend encode --samples 50 --yes-device

# 兼容性后端
python -X utf8 -B scripts/capture_benchmark.py --adb "C:\...\adb.exe" --serial 127.0.0.1:16384 --backend netcat --samples 50 --yes-device

# EmulatorExtras（MuMu 12）
python -X utf8 -B scripts/capture_benchmark.py --adb "C:\...\adb.exe" --serial 127.0.0.1:16384 --backend extras --samples 50 --yes-device `
  --mumu-path "C:\Program Files\Netease\MuMuPlayer-12.0" --mumu-lib "C:\Program Files\Netease\MuMuPlayer-12.0\shell\sdk\external_renderer_ipc.dll" `
  --mumu-index 0 --mumu-clone 0
```

（参数名以 `--help` 为准；以上为预实现约定。）

每轮记录：后端、样本数、P50/P90/min/max、失败次数与错误码、连接耗时、分辨率、帧是否与 adb 基线同画面（哈希或肉眼对照）。同一后端至少重复 3 次连接，保留慢样本。

## 4. 判定规则

1. 先用 P0 求"抓帧占整局比例"：`<10%` 关闭评估线；`10–20%` 只做低风险后端替换评估；`>20%` 进入 §5 的资格评估。
2. `Encode` vs `RawWithGzip`：连接测速已经自动选快者，benchmark 只用于核对"实际后端"和 P50 是否与 `capture-performance` 一致。
3. `RawByNetcat`：只有在前两者 P50 明显偏高且设备支持 reverse 时才考虑；兼容性失败即放弃。
4. `EmulatorExtras`：只在 §5 的身份证明通过后进入正式候选；benchmark 的延迟本身不构成准入。
5. `scrcpy`：不作为正式取帧；只允许在预览/实验分支验证"流式帧源"接口；有损与单 display 的限制不因速度改变。
6. Minicap 不测：有损，和现有阈值/回放证据不兼容。

## 5. EmulatorExtras 的额外门槛

benchmark 只能证明"快"。正式准入还需要：

- package / activity / 显示绑定与当前 `screencap -d <物理 ID>` 的对应关系可证；
- 输入目标仍能绑定到逻辑 display（现有 `input -d` 路径或等价证据）；
- 失败、断连、分辨率变化时不返回旧帧；不新增重试或输入重发；
- `capture_performance` 能记录实际后端，`PcrCapturePerformance` 的 6/1 断言有明确的替换方案。

任何一项不能证明，就停留在只读诊断，不进入正式路径。

## 6. 现在不能做的事

- 真实延迟、像素一致性、失败率：没有设备；
- GUI C# 构建与界面开关：本机没有 dotnet；
- 正式掩码/证据改动：必须等 §4 的占比与 §5 的证明；
- 整链百分比：必须等至少 3 组同设备自然运行样本。

## 7. 关联

- [性能优化总纲](OPTIMIZATION_PLAN.md)（P0/P1/P2/P3 批次）
- [黎明界局内计划](LABYRINTH_PERFORMANCE_PLAN.md)（L2-6/L1-2 的局内位置）
- MaaFramework 截图方式：`MaaAdbScreencapMethodEnum`（Encode=2 / RawWithGzip=4 / RawByNetcat=8 / EmulatorExtras=64；Minicap 有损）
