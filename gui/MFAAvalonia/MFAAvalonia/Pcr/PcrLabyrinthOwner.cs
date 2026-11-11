using System;
using System.IO;
using System.Linq;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Media;
using MaaFramework.Binding;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Utilities;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// Shares the start boundary with daily execution. The private Job remains owned
// through Stop and failed cleanup; editability returns only after verified exit.
internal static class PcrLabyrinthOwner
{
    private sealed class Run(TaskQueueViewModel vm) {
        internal readonly TaskQueueViewModel ViewModel = vm;
        internal readonly CancellationTokenSource Cancel = new();
        internal readonly TaskCompletionSource Completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal PcrWindowsChild? Child;
    }
    private static Run? current;
    internal static bool IsActive => Volatile.Read(ref current) != null;
    internal static TimeSpan? StartupWait(string? stage) => stage switch {
        "validating" or "dependencies"=>TimeSpan.FromSeconds(30),
        "resources"=>TimeSpan.FromSeconds(120), "connecting"=>TimeSpan.FromSeconds(45),
        "authenticating"=>TimeSpan.FromSeconds(200),
        "recovering" or "retiring"=>TimeSpan.FromSeconds(120),
        "ready" or "opening" or "handoff" or "playing" or "cleared" or "moving" or "team" or "battle" or "shop" or "reward"
            =>Timeout.InfiniteTimeSpan, _=>null };
    internal static void Stop(TaskQueueViewModel vm) {
        lock (PcrProfileRunGate.StartGate) {
            if (current?.ViewModel != vm) return;
            current.Cancel.Cancel();
            vm.LabyrinthSettings.Progress = "正在等待当前操作记账，然后停止执行组件…";
        }
    }
    internal static Task StopForExitAsync() {
        lock (PcrProfileRunGate.StartGate) {
            if (current is not { } run) return Task.CompletedTask;
            Stop(run.ViewModel);
            return run.Completion.Task;
        }
    }
    internal static async Task TogglePauseAsync(TaskQueueViewModel vm) {
        Run? run;
        string command;
        lock (PcrProfileRunGate.StartGate) {
            run=current;
            if (run?.ViewModel!=vm || run.Cancel.IsCancellationRequested || run.Child is not { Exited:false }
                || vm.LabyrinthSettings.PausePending) return;
            command=vm.LabyrinthSettings.IsPaused ? "resume" : "pause";
            vm.LabyrinthSettings.PausePending=true;
        }
        try {
            await run.Child.Send(new JObject { ["kind"]=command }.ToString(Formatting.None)).WaitAsync(TimeSpan.FromSeconds(2));
        } catch {
            lock (PcrProfileRunGate.StartGate) {
                if (ReferenceEquals(current,run)) {
                    vm.LabyrinthSettings.PausePending=false;
                    Stop(vm);
                }
            }
        }
    }
    // 设置卡片沿用原有文案：运行细节只进实时日志，避免设置区被运行信息淹没。
    private static string SettingsStatusFor(string? outcome) => outcome switch {
        "execution_not_qualified"=>"候选尚未完成地图交接、编组和连续执行验收；本次未连接或操作设备。",
        "blocked_in_flight"=>"上次存在结果未知的操作，请先核对原记录；不会自动重发。",
        "mid_run_not_supported" or "kept_existing_run"=>"已保留旧局，当前状态无法续跑。可手动完成旧局，或关闭“保留旧局”后再次开始。",
        "kept_run_unknown"=>"已保留旧局，上次操作结果尚未确认。可手动处理旧局，或关闭“保留旧局”后再次开始。",
        "restart_retire_unknown"=>"上次撤退结果尚未确认，本次没有重复撤退或开新局。请稍后再次开始核对；若仍有旧局，请在游戏里手动撤退后再次开始。",
        "restart_state_unknown"=>"旧局状态未确认，本次没有开新局。请检查网络和游戏状态后再次开始。",
        "restart_state_changed"=>"准备期间出现了新的活动局，本次没有再次撤退。请确认没有其他人或程序在操作，再次开始。",
        "restart_target_mismatch"=>"上次未结束任务属于其他账号或设备。请切回对应账号和设备后再次开始。",
        "restart_records_invalid"=>"上次任务记录无法核对，本次没有开新局。请导出诊断记录，保留原数据。",
        "network_or_protocol_failed" or "top_invalid"=>"旧局状态或网络请求未确认，本次没有开新局。请检查网络后再次开始核对。",
        "defeated"=>"已确认战败，本次批次结束。", "stopped"=>"已停止本次任务。",
        "account_mismatch"=>"协议账号与期望游戏玩家 ID 不一致，本次已停止。",
        "runtime_components_missing"=>"黎明界运行组件或依赖缺失，请核对完整候选包。",
        "runtime_resources_timeout"=>"本地识别资源加载超时，本次已停止。请核对完整程序包与本机资源占用。",
        "runtime_ocr_preparation_failed"=>"本地文字识别初始化未通过，本次未开始游戏任务。请导出黎明界诊断记录。",
        "controller_connection_timeout" or "controller_connection_failed"=>"连接所选设备失败或超时，本次已停止。请核对设备与 ADB 目标。",
        "content_update_required"=>"游戏资源版本已更新，当前程序需匹配新版数据；本次未开始游戏任务。",
        "protocol_version_invalid"=>"服务器资源版本信息未通过校验，本次未开始游戏任务。请稍后重试或导出诊断记录。",
        "game_version_unavailable"=>"无法核对所选设备上已安装的游戏版本，本次未开始登录。请确认已安装所选渠道的游戏，并核对设备。",
        "invalid_server"=>"所选游戏渠道无效，本次未开始登录。",
        "channel_credentials_required"=>"请填写所选渠道的 UID 和 access_key。",
        "channel_client_close_required"=>"所选渠道需要先关闭游戏客户端，关闭状态尚未确认，本次已停止。",
        "login_failed"=>"协议登录未通过校验，本次未开始游戏任务。请检查网络或导出黎明界诊断记录。",
        "sdk_login_failed"=>"账号登录服务未接受本次登录，本次未开始游戏任务。请核对账号资料；若刚完成人工验证码，请稍后重新开始。",
        "manual_verification_required"=>"登录需要人工验证码；本次已停止，没有自动重试。",
        "captcha_cancelled" or "captcha_timeout"=>"人工验证码已取消或超时，本次已停止。",
        "captcha_failed" or "captcha_rejected" or "captcha_network_failed" or "captcha_network_or_protocol_failed"=>"人工验证码未通过或加载失败，本次已停止。",
        "captcha_response_invalid" or "captcha_proof_invalid" or "captcha_request_invalid"=>"人工验证码数据或验证结果不完整，本次已停止。请重新开始验证；仍失败时导出本次日志。",
        "captcha_type_unsupported"=>"登录服务要求的验证码类型暂不支持，本次已停止。请导出本次日志反馈。",
        "game_session_readback_unknown"=>"游戏客户端页面未通过校验，本次已停止。请检查游戏是否仍在加载或有弹窗，并导出黎明界诊断记录。",
        "sdk_login_timeout" or "protocol_login_timeout"=>"协议账号登录网络超时，本次已停止；不会自动重试。请检查网络后重新点击开始。",
        "complete"=>"已完成本次目标。",
        _=>"本次未确认完成，请查看独立黎明界运行记录。" };

    // 实时日志里的原因：补充更具体的原因；未列出的沿用设置区文案并带上原因代码。
    private static string StatusFor(string? outcome) => outcome switch {
        "opening_not_matching"=>"当前开局候选不符合路线计划，且未开启“重新开局(REROLL)”，本次已停止。可开启“重新开局”后再次开始。",
        "reroll_attempt_limit"=>"已达到开局重试次数上限，本次已停止。请放宽路线条件或提高上限后再次开始。",
        "reroll_time_limit"=>"已达到开局重试时间上限，本次已停止。请放宽路线条件或提高上限后再次开始。",
        "opening_slots_invalid" or "opening_identity_required" or "opening_creation_identity_required"=>"开局识别出的可选角色不符合配置，本次已停止。",
        "opening_selection_unknown"=>"选人界面初始状态未确认，本次已停止。请确认界面停在“角色选择”后再次开始。",
        "character_identity_unknown"=>"“角色加入”弹窗里的角色名无法匹配图鉴（多为文字识别错误），本次已停止。请导出黎明界诊断记录，保留现场截图。",
        "character_identity_ambiguous"=>"“角色加入”弹窗里的角色名匹配到多个角色，无法唯一确认，本次已停止。",
        "opening_joined_unknown" or "game_port_invitation_unknown"=>"已加入的角色与所选角色不一致，本次已停止。",
        "portrait_joined_unknown" or "portrait_joined_layout_unknown" or "portrait_ambiguous" or "portrait_conflicting"=>"“角色加入”弹窗的布局或头像校验未通过，本次已停止。",
        "character_not_visible"=>"筛选后找不到目标角色，本次已停止。请核对角色筛选条件与账号角色。",
        "character_acquisition_click_limit"=>"选人点击次数超出上限，本次已停止。",
        "character_readback_unknown" or "character_count_unknown" or "character_page_unknown"=>"选人界面未通过校验，本次已停止。",
        "battle_result_unknown" or "battle_wait_restore_failed"=>"战斗结果未确认，本次已停止；不会重复进入战斗。请核对本局后再次开始。",
        "team_identity_unknown" or "team_readback_unknown" or "team_selection_changed"=>"编组页面未通过校验，本次已停止。",
        "observation_unknown"=>"页面观察结果未通过校验，本次已停止。",
        "native_input_not_sent"=>"本次输入没有发出，已停止。",
        "shop_blocked" or "shop_readback_unknown"=>"商店页面未通过校验，本次已停止。",
        "scene_unhandled" or "scene_unknown"=>"出现了程序未处理的界面，本次已停止。请导出黎明界诊断记录。",
        null or ""=>SettingsStatusFor(outcome),
        _=>SettingsStatusFor(outcome)+$"（原因代码：{outcome}）" };

    // 实时日志面板一行；VM 会再带前缀写一份进共享日志，以便落盘与导出。
    private static void Trace(TaskQueueViewModel vm,string text) => vm.AddLabyrinthLog(text);

    private static string ProgressCount(JObject value,JObject request) {
        if (value["completed"] is not JObject completed || request["settings"]?["goals"] is not JArray goals) return "";
        int total=0,done=0;
        foreach (var goal in goals) {
            var id=(int?)goal["guild_id"]; var target=(int?)goal["clears"];
            if (id is null || target is null || target<1 || target>999) return "";
            var current=completed[id.Value.ToString(CultureInfo.InvariantCulture)];
            if (current?.Type!=JTokenType.Integer || (int)current<0 || (int)current>target) return "";
            total+=target.Value; done+=(int)current;
        }
        return $"（目标已完成 {done}/{total} 次）";
    }
    internal static async Task RequestStartAsync(TaskQueueViewModel vm) {
        Run run;
        JObject request;
        long feedbackRun;
        lock (PcrProfileRunGate.StartGate) {
            if (PcrProfileRunGate.Busy || !vm.CanEditPcrTarget) return;
            feedbackRun=vm.BeginPcrRunFeedback();
            // 从点击开始就记：启动阶段失败也要在面板上有据可查。
            Trace(vm,$"—— 本次开始 {DateTime.Now:MM-dd HH:mm} ——");
            Trace(vm,"已请求开始，正在校验黎明界配置与运行资源…");
            try {
                if (!vm.IsLabyrinthProfile) throw new InvalidOperationException("黎明界配置入口无效。");
                var settings=vm.LabyrinthSettings;
                settings.HideCredentials();
                settings.Save();
                var snapshot=settings.Snapshot();
                if (!long.TryParse(settings.ExpectedPlayerId,NumberStyles.None,CultureInfo.InvariantCulture,out var accountId) || accountId<=0)
                    throw new InvalidOperationException("请填写游戏玩家 ID（个人简介，非B站 UID），必须为正整数。");
                if (vm.CurrentDevice is not AdbDeviceInfo device || !AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(device.AdbPath,device.AdbSerial,out var path,out var serial))
                    throw new InvalidOperationException("请先在设备栏选择有效的 ADB 目标。");
                request=new JObject { ["version"]=1,["settings"]=snapshot,["account_id"]=accountId,["server"]=settings.Server,
                    ["keep_old_run"]=settings.KeepOldRun,
                    ["connection"]=new JObject { ["adb_path"]=path,["serial"]=serial },
                    ["credentials"]=new JObject { ["username"]=settings.Username,["password"]=settings.Password } };
                current=run=new Run(vm);
                settings.IsPaused=false;
                settings.PausePending=false;
                vm.IsRunning=true;
                settings.Progress="正在校验黎明界配置与运行资源…";
                PcrProfileRunGate.Notify();
            } catch (Exception ex) {
                vm.LabyrinthSettings.Status=ex.Message;
                vm.ShowPcrStartError(feedbackRun,"本次未能开始："+ex.Message);
                vm.FinishPlayerLog("not_started");
                vm.Processor.AddLog(ex.Message,(IBrush?)null);
                return;
            }
        }
        var released=false;
        var cancelled=false;
        var finished=false;
        var terminalOutcome="stopped";
        using var timeout=new CancellationTokenSource(TimeSpan.FromHours(24));
        using var startup=new CancellationTokenSource(TimeSpan.FromSeconds(30));
        using var cancel=CancellationTokenSource.CreateLinkedTokenSource(run.Cancel.Token,timeout.Token,startup.Token);
        try {
            var root=PcrAlphaOwner.Repository;
            PcrRuntimePaths.RequireUpdateComplete(AppContext.BaseDirectory,root);
            var script=Path.Combine(root,"scripts","start-labyrinth-owned-agent.py");
            if (!File.Exists(script)) throw new InvalidOperationException("黎明界执行组件缺失，请重新解压完整候选包。");
            cancel.Token.ThrowIfCancellationRequested();
            run.Child=new PcrWindowsChild();
            var arguments = new[] { "-X", "utf8", "-B", "-u", script };
            if (vm.PlayerLog is { } playerLog) arguments = arguments.Concat(new[] { "--player-log", playerLog.RunId }).ToArray();
            run.Child.StartWithEnvironment(PcrRuntimePaths.ResolvePython(root),root,
                PcrNetworkProxy.DirectEnvironment(),arguments);
            await run.Child.Send(request.ToString(Formatting.None)).WaitAsync(cancel.Token);
            request.Remove("credentials");
            bool receipt=false;
            var lastLogged=string.Empty;
            var resumeProgress=string.Empty;
            string? lastStage="validating";
            await foreach (var line in run.Child.Lines.Reader.ReadAllAsync(cancel.Token)) {
                if (line.Error) continue; // never display SDK exception text or tokens
                JObject value;
                try {
                    if (line.Text.Length>16384) throw new JsonException();
                    value=JObject.Parse(line.Text,new JsonLoadSettings {
                        DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error });
                } catch { throw new InvalidOperationException("黎明界组件返回了无效消息，本次任务已停止。"); }
                var kind=(string?)value["kind"];
                // Background logs and pause notifications can arrive during the human wait.
                if (kind is "progress" or "result") PcrCaptchaHost.Hide();
                if (kind=="player_log") { vm.ReceivePlayerLog(feedbackRun,value); continue; }
                if (kind=="control") {
                    var state=(string?)value["state"];
                    if (value.Properties().Count()!=2 || state is not ("paused" or "running"))
                        throw new InvalidOperationException("黎明界暂停状态无效，本次任务已停止。");
                    if (state=="paused") {
                        resumeProgress=vm.LabyrinthSettings.Progress;
                        vm.LabyrinthSettings.IsPaused=true;
                        vm.LabyrinthSettings.Progress="已暂停，当前操作已完成记账。";
                        startup.CancelAfter(Timeout.InfiniteTimeSpan);
                    } else {
                        vm.LabyrinthSettings.IsPaused=false;
                        vm.LabyrinthSettings.Progress=resumeProgress;
                        startup.CancelAfter(StartupWait(lastStage) ?? TimeSpan.FromSeconds(30));
                    }
                    vm.LabyrinthSettings.PausePending=false;
                    Trace(vm,state=="paused" ? "已暂停。" : "继续本次任务。");
                    continue;
                }
                if (kind=="log") {
                    // Live business trace from the owner; display only, no protocol meaning.
                    var text=(string?)value["text"];
                    if (text is { Length: > 0 and <= 512 }) Trace(vm,text);
                    continue;
                }
                if (kind=="captcha") {
                    if (!PcrLabyrinthCaptchaMessage.TryParse(line.Text,out var captcha))
                        throw new InvalidOperationException("人工验证码消息无效，本次任务已停止。");
                    lock (PcrProfileRunGate.StartGate) {
                        cancel.Token.ThrowIfCancellationRequested();
                        if (!ReferenceEquals(current,run) || !vm.IsRunning || run.Child==null || run.Child.Exited)
                            throw new InvalidOperationException("人工验证码等待已结束，本次任务已停止。");
                    }
                    // Show the same loopback page inside the main window; only when the
                    // embedded window is unavailable fall back to the default browser.
                    var inApp=await PcrCaptchaHost.OpenAsync(captcha!.Url);
                    cancel.Token.ThrowIfCancellationRequested();
                    if (!inApp) captcha.OpenPage(UrlUtilities.OpenUrl);
                    // Retain the 300-second human wait plus the bounded remaining login requests.
                    startup.CancelAfter(TimeSpan.FromSeconds(600));
                    vm.LabyrinthSettings.Progress=inApp
                        ? "请在软件内的人工验证窗口完成验证码；正在等待验证结果…"
                        : "请在浏览器中手动完成验证码；正在等待验证结果…";
                    lastLogged=vm.LabyrinthSettings.Progress;
                    Trace(vm,lastLogged);
                    continue;
                }
                if (kind=="progress") {
                    var stage=(string?)value["stage"];
                    lastStage=stage;
                    if (StartupWait(stage) is { } wait) startup.CancelAfter(wait);
                    vm.LabyrinthSettings.Progress=stage switch {
                        "validating"=>"正在校验黎明界配置…", "dependencies"=>"正在加载黎明界执行组件…",
                        "resources"=>"正在加载本地识别资源…", "connecting"=>"正在连接所选设备…",
                        "authenticating"=>"正在登录协议账号并核对游戏玩家 ID…",
                        "recovering"=>"正在核对上次停止后的旧局状态…",
                        "retiring"=>"正在放弃旧局，确认结束后开始新局…",
                        "ready"=>"协议账号核对完成，正在准备本次任务…",
                        "opening"=>"正在读取和筛选新开局…", "handoff"=>"正在恢复客户端并核对账号、地图…",
                        "playing"=>"正在执行黎明界路线…", "cleared"=>"已确认本局通关。",
                        "moving"=>"正在移动到路线目标…", "team"=>"正在选择并核对队伍…",
                        "battle"=>"正在战斗并等待结果…", "shop"=>"正在处理商店…",
                        "reward"=>"正在选择奖励…", _=>"正在校验运行状态…" };
                    vm.LabyrinthSettings.Progress+=ProgressCount(value,request);
                    // One line per visible change keeps the panel readable.
                    if (vm.LabyrinthSettings.Progress!=lastLogged) {
                        lastLogged=vm.LabyrinthSettings.Progress;
                        Trace(vm,lastLogged);
                    }
                }
                if (kind=="result") {
                    if ((string?)value["outcome"]=="complete" && (bool?)value["batch_complete"]!=true)
                        throw new InvalidOperationException("黎明界通关结果不完整，本次未确认完成。");
                    receipt=true;
                    var outcome=(string?)value["outcome"];
                    terminalOutcome=outcome ?? "unknown";
                    finished=!string.IsNullOrWhiteSpace(outcome) && outcome!="stopped";
                    vm.LabyrinthSettings.Progress=outcome=="complete" ? "目标次数已全部完成。" : "本次任务已停止。";
                    vm.LabyrinthSettings.Status=SettingsStatusFor(outcome);
                    // 固定运行栏与日志复用同一原因；设置卡片保持原内容。
                    lastLogged=vm.LabyrinthSettings.Progress+ProgressCount(value,request);
                    vm.LabyrinthSettings.Progress=lastLogged;
                    Trace(vm,lastLogged);
                    var reason=StatusFor(outcome);
                    if (outcome!="complete") vm.ShowPcrStartError(feedbackRun,reason);
                    else Trace(vm,reason);
                    break; // Terminal receipt; the owned Job closes descendant pipes in finally.
                }
            }
            if (!receipt) throw new InvalidOperationException("黎明界执行组件未返回有效结果，任务未确认完成。");
        } catch (OperationCanceledException) {
            cancelled=true;
            vm.PcrCleanupPending=true;
            var timedOut=startup.IsCancellationRequested && !run.Cancel.IsCancellationRequested;
            var waiting=vm.LabyrinthSettings.Progress.TrimEnd('…');
            vm.LabyrinthSettings.Progress="已请求停止；正在回收执行组件…";
            Trace(vm,vm.LabyrinthSettings.Progress);
            vm.LabyrinthSettings.Status=timedOut ? waiting+"等待超时，本次已停止。" : "已停止本次任务。";
            if (timedOut) vm.ShowPcrStartError(feedbackRun,vm.LabyrinthSettings.Status);
            else Trace(vm,vm.LabyrinthSettings.Status);
            if (run.Child is { Exited: false }) {
                try {
                    await run.Child.Send("{\"kind\":\"stop\"}").WaitAsync(TimeSpan.FromSeconds(1));
                    // The owned process exiting is the stop boundary: allow the bounded
                    // 90-second native action and 20-second cleanup to finish. Its stdout/
                    // stderr pipes can stay open after exit while a descendant of this
                    // private Job keeps an inherited handle, so pipe EOF is not an exit
                    // signal; Dispose terminates the Job and proves the final exit.
                    var exitedBy=DateTime.UtcNow.AddSeconds(120);
                    while (!run.Child.Exited && DateTime.UtcNow<exitedBy)
                        await Task.Delay(50);
                } catch { /* The private Job enforces final exit below. */ }
            }
        } catch (Exception ex) {
            // Local construction failures contain no credentials. The child's
            // network errors are intentionally never interpolated into UI logs.
            vm.LabyrinthSettings.Status=ex is IOException ? "黎明界组件通信失败，请核对运行记录。" : ex.Message;
            vm.ShowPcrStartError(feedbackRun,vm.LabyrinthSettings.Status);
        } finally {
            try {
            PcrCaptchaHost.Hide();
            vm.PcrCleanupPending=true;
            Trace(vm,"任务已结束，正在释放执行资源…");
            request.Remove("credentials");
            try {
                if (run.Child!=null) await Task.Run(run.Child.Dispose);
                released=true;
            } catch {
                vm.LabyrinthSettings.Status="执行组件退出尚未确认，请退出 Maa PCR 后重新打开。原记录会保留，下次开始时会核对旧局状态。";
                vm.ShowPcrStartError(feedbackRun,vm.LabyrinthSettings.Status);
            }
            if (released) {
                if (cancelled) vm.LabyrinthSettings.Progress="本次任务已停止。";
                lock (PcrProfileRunGate.StartGate) {
                    if (ReferenceEquals(current,run)) current=null;
                    cancelled|=run.Cancel.IsCancellationRequested;
                    run.Cancel.Dispose();
                    vm.IsRunning=false;
                    vm.LabyrinthSettings.IsPaused=false;
                    vm.LabyrinthSettings.PausePending=false;
                    PcrProfileRunGate.Notify();
                }
            }
            vm.Processor.AddLog(vm.LabyrinthSettings.Status,(IBrush?)null);
            vm.PcrCleanupPending=false;
            vm.FinishPlayerLog(released ? cancelled ? "stopped" : terminalOutcome : "cleanup_unconfirmed");
            } finally { run.Completion.TrySetResult(); }
        }
        if (released && finished && !cancelled)
            vm.Processor.HandleAfterTaskOperation();
    }
}
