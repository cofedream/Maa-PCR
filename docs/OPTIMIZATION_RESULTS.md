# 本轮离线优化结果（2026-11-24）

状态：本机可做的部分已落地并提交；设备相关项尚未验证。本文只汇总**离线可验证**的结果，不宣称整链提速。

环境：Linux + CPython 3.13.16 + pytest 9.1.1；开发环境 maafw 5.13.0，仓库 `.venv` 为 5.16.1；20 张公开黎明界帧（`tests/data`）；没有 Windows GUI、dotnet、模拟器或真实 ADB。

## 1. 已提交

| 提交 | 内容 | 离线验证 |
|---|---|---|
| `5c94d98` | 原生识别耗时按日志文件字段解析；截图仅在 CustomController 有 leave 行时计数，ADB 保持 `None` | 17 项单测；真实 5.13.0 日志 recognition **93 条**、total 1505 ms；screenshot 4 条（CustomController） |
| `827b280` | `scripts/perf_scope_ab.py`：公开帧的 scope A/B（语义、controls、识别次数、墙钟） | 36 项单测；20 帧全跑通：全规则 **2463** 次 → map scope **236** 次（-90.4%），本机 72.8 s → 6.8 s |
| `e2e46ed` | `scripts/capture_benchmark.py` + `scripts/experimental/frame_source_controller.py` | 58 项单测（maafw 5.13.0 与 5.16.1 均通过）；`--help` 与无 `--yes-device` 拒绝实测；未连设备 |
| `ab80c84` | 滚动 guard 收窄到 `probe_kinds=('map',)`；`locate`/`preview` 按帧哈希复用 `NodePixels.collect` | 12 项新测 + 53 项 autoplay/node 回归；公开地图帧 guard **122 → 14** 次子识别（-88.5%），非 map 仍 `hit=None`、零输入 |

## 2. 结果怎么读

- **计时**：`recognition` 现在能从真实日志取到样本；`screenshot` 对 ADB 仍是 `None`，这是事实缺失，不是 0。抓帧占比仍要靠设备侧外部采样（见下）。
- **scope**：`2463 → 236` 是"全规则 vs map"在 20 张公开帧上的**调用次数与 Linux 离线墙钟**，不是生产 scope（生产会并上 canary），也不是设备耗时。地图帧单次观察约 `123 → 15` 次；滚动 guard 单次 `122 → 14` 次；两者口径不同，不能相加。
- **L1-2**：只有两帧像素完全一致时才复用 `collect`；有动画/过渡时仍重算。真实命中率必须在设备上测，离线只能证明"同帧只算一次、异帧必重算"。
- **抓帧工具**：benchmark 只测"一个后端、输入为 None、必须显式确认设备"；帧源骨架只是接口，不代表 scrcpy 可进正式路径。

## 2.1 验证口径

- 受影响的 9 个测试文件在仓库 `.venv` 的 maafw 5.16.1 下：**233 passed in 33.42 s**。
- 本机默认 Python（maafw 5.13.0）跑 `pytest -k labyrinth`：**2246 passed / 7 failed / 138 skipped**；7 项全部是 `*_sdk_shell_*` / `channels_parity` / `client_launch` 的 shell 与版本回执用例，其中抽样 2 项在基线 `6a079b8` 上以同一环境运行同样失败；7 项都属于同一 shell/版本回执族（本机同时存在 5.13.0 与 5.16.1，加载到多版本时会多一条警告日志）。换成 5.16.1 后 6/7 通过，剩余 1 项仍受双版本安装干扰。**这 7 项不是本分支引入的。**
- 公开帧 scope A/B：7 张地图/首领/map-portal 帧的 full 与 map scope `kind` 一致；13 张非地图帧 map scope 返回 `unknown`（预期，用于证明"纯 map 不能替代全量/弹层 canary"）。

## 3. 未完成 / 必须等设备

1. 抓帧占比与四个后端的 P50/P90：跑 `scripts/capture_benchmark.py`。
2. EmulatorExtras 的帧一致性、失败率与 package/display/input 身份证明：只读诊断 + benchmark。
3. L2-1（`RouteTargetRecognition` 收窄）：必须带弹层 canary，preview 不能纯 map；需要真实帧 A/B。
4. L1-1（遗物双读条件化）、L1-3（轮询 500→150–200 ms）、L1-4（结算盲点改帧变化推进）：逻辑可写，但稳定性必须真机确认。
5. L3（locate/preview 合并、同帧复用扩展）：需要设备时序与整链对照。
6. GUI C#（`PcrCapturePerformance`/`PcrMuMuDisplay` 的掩码与证据）：本机无 dotnet，未构建。
7. 整链百分比：至少 3 组同设备自然运行样本后再谈。

## 4. 设备恢复后的第一批命令

```powershell
# 抓帧后端（一次一个，输入恒为 None）
python -X utf8 -B scripts/capture_benchmark.py --adb "C:\...\adb.exe" --serial 127.0.0.1:16384 --backend raw --samples 50 --yes-device
python -X utf8 -B scripts/capture_benchmark.py --adb "C:\...\adb.exe" --serial 127.0.0.1:16384 --backend encode --samples 50 --yes-device
python -X utf8 -B scripts/capture_benchmark.py --adb "C:\...\adb.exe" --serial 127.0.0.1:16384 --backend netcat --samples 50 --yes-device
python -X utf8 -B scripts/capture_benchmark.py --adb "C:\...\adb.exe" --serial 127.0.0.1:16384 --backend extras --samples 50 --yes-device `
  --mumu-path "C:\Program Files\Netease\MuMuPlayer-12.0" --mumu-lib "...\external_renderer_ipc.dll" --mumu-index 0 --mumu-clone 0
```

判定：先由 P0 求抓帧占比；`<10%` 关闭评估线，`>20%` 才进入 EmulatorExtras 资格评估。详见[截图方案预实现](CAPTURE_BACKENDS.md)。

## 5. 本机验证命令

```bash
python -m pytest tests/test_performance_timings_r53.py tests/test_perf_scope_ab.py tests/test_capture_benchmark.py tests/test_frame_source_controller.py -q
python -m pytest tests/test_labyrinth_scroll_guard_scope.py tests/test_labyrinth_collect_cache.py tests/test_labyrinth_autoplay.py tests/test_labyrinth_node_pixels.py -q
python -m pytest -k labyrinth -q
python -X utf8 -B scripts/perf_scope_ab.py --limit 2
```

## 6. 关联

- [性能优化总纲](OPTIMIZATION_PLAN.md)
- [黎明界局内计划](LABYRINTH_PERFORMANCE_PLAN.md)
- [截图方案预实现](CAPTURE_BACKENDS.md)
