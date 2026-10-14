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
        lock (PcrProfileRunGate.StartGate) {
            if (PcrProfileRunGate.Busy || !vm.CanEditPcrTarget) return;
            try {
                if (!vm.IsLabyrinthProfile) throw new InvalidOperationException("黎明界配置入口无效。");
                var settings=vm.LabyrinthSettings;
                settings.HideCredentials();
                settings.Save();
                var snapshot=settings.Snapshot();
                if (!long.TryParse(settings.ExpectedPlayerId,NumberStyles.None,CultureInfo.InvariantCulture,out var accountId) || accountId<=0)
                    throw new InvalidOperationException("请填写游戏玩家 ID（个人简介，非B站 UID），必须为正整数。");
                if (vm.CurrentDevice is not AdbDeviceInfo device || !AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(device.AdbPath,device.AdbSerial,out var path,out var serial))
                    throw new InvalidOperationException("请先在设备栏选择有效的本机 ADB 目标。");
                request=new JObject { ["version"]=1,["settings"]=snapshot,["account_id"]=accountId,
                    ["keep_old_run"]=settings.KeepOldRun,
                    ["connection"]=new JObject { ["adb_path"]=path,["serial"]=serial },
                    ["credentials"]=new JObject { ["username"]=settings.Username,["password"]=settings.Password } };
                current=run=new Run(vm);
                vm.IsRunning=true;
                settings.Progress="正在校验黎明界配置与运行资源…";
                PcrProfileRunGate.Notify();
            } catch (Exception ex) {
                vm.LabyrinthSettings.Status=ex.Message;
                vm.Processor.AddLog(ex.Message,(IBrush?)null);
                return;
            }
        }
        var released=false;
        var cancelled=false;
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
            run.Child.Start(PcrRuntimePaths.ResolvePython(root),root,"-X","utf8","-B","-u",script);
            await run.Child.Send(request.ToString(Formatting.None)).WaitAsync(cancel.Token);
            request.Remove("credentials");
            bool receipt=false;
            await foreach (var line in run.Child.Lines.Reader.ReadAllAsync(cancel.Token)) {
                if (line.Error) continue; // never display SDK exception text or tokens
                JObject value;
                try {
                    if (line.Text.Length>16384) throw new JsonException();
                    value=JObject.Parse(line.Text,new JsonLoadSettings {
                        DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error });
                } catch { throw new InvalidOperationException("黎明界组件返回了无效消息，本次任务已停止。"); }
                var kind=(string?)value["kind"];
                if (kind=="captcha") {
                    if (!PcrLabyrinthCaptchaMessage.TryParse(line.Text,out var captcha))
                        throw new InvalidOperationException("人工验证码消息无效，本次任务已停止。");
                    lock (PcrProfileRunGate.StartGate) {
                        cancel.Token.ThrowIfCancellationRequested();
                        if (!ReferenceEquals(current,run) || !vm.IsRunning || run.Child==null || run.Child.Exited)
                            throw new InvalidOperationException("人工验证码等待已结束，本次任务已停止。");
                        captcha!.OpenPage(UrlUtilities.OpenUrl);
                        // Retain the 300-second human wait plus the bounded remaining login requests.
                        startup.CancelAfter(TimeSpan.FromSeconds(600));
                        vm.LabyrinthSettings.Progress="请在浏览器中手动完成验证码；正在等待验证结果…";
                    }
                    continue;
                }
                if (kind=="progress") {
                    var stage=(string?)value["stage"];
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
                }
                if (kind=="result") {
                    if ((string?)value["outcome"]=="complete" && (bool?)value["batch_complete"]!=true)
                        throw new InvalidOperationException("黎明界通关结果不完整，本次未确认完成。");
                    receipt=true;
                    vm.LabyrinthSettings.Progress=(string?)value["outcome"]=="complete" ? "目标次数已全部完成。" : "本次任务已停止。";
                    vm.LabyrinthSettings.Status=(string?)value["outcome"] switch {
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
                        "controller_connection_timeout" or "controller_connection_failed"=>"连接所选设备失败或超时，本次已停止。请核对设备与 ADB 目标。",
                        "sdk_login_timeout" or "protocol_login_timeout"=>"协议账号登录网络超时，本次已停止；不会自动重试。请检查网络后重新点击开始。",
                        "complete"=>"已完成本次目标。", _=>"本次未确认完成，请查看独立黎明界运行记录。" };
                    vm.LabyrinthSettings.Progress+=ProgressCount(value,request);
                    break; // Terminal receipt; the owned Job closes descendant pipes in finally.
                }
            }
            if (!receipt) throw new InvalidOperationException("黎明界执行组件未返回有效结果，任务未确认完成。");
        } catch (OperationCanceledException) {
            cancelled=true;
            var timedOut=startup.IsCancellationRequested && !run.Cancel.IsCancellationRequested;
            var waiting=vm.LabyrinthSettings.Progress.TrimEnd('…');
            vm.LabyrinthSettings.Progress="已请求停止；正在回收执行组件…";
            vm.LabyrinthSettings.Status=timedOut ? waiting+"等待超时，本次已停止。" : "已停止本次任务。";
            if (run.Child is { Exited: false }) {
                try {
                    await run.Child.Send("{\"kind\":\"stop\"}").WaitAsync(TimeSpan.FromSeconds(1));
                    // Allow the bounded 90-second native action and 20-second cleanup to finish.
                    await Task.WhenAll(run.Child.StdoutCompletion,run.Child.StderrCompletion).WaitAsync(TimeSpan.FromSeconds(120));
                } catch { /* The private Job enforces final exit below. */ }
            }
        } catch (Exception ex) {
            // Local construction failures contain no credentials. The child's
            // network errors are intentionally never interpolated into UI logs.
            vm.LabyrinthSettings.Status=ex is IOException ? "黎明界组件通信失败，请核对运行记录。" : ex.Message;
        } finally {
            request.Remove("credentials");
            try {
                if (run.Child!=null) await Task.Run(run.Child.Dispose);
                released=true;
            } catch {
                vm.LabyrinthSettings.Status="执行组件退出尚未确认，请退出 Maa PCR 后重新打开。原记录会保留，下次开始时会核对旧局状态。";
            }
            if (released) {
                if (cancelled) vm.LabyrinthSettings.Progress="本次任务已停止。";
                lock (PcrProfileRunGate.StartGate) {
                    if (ReferenceEquals(current,run)) current=null;
                    run.Cancel.Dispose();
                    vm.IsRunning=false;
                    PcrProfileRunGate.Notify();
                }
            }
            vm.Processor.AddLog(vm.LabyrinthSettings.Status,(IBrush?)null);
        }
    }
}
