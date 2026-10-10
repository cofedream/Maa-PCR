using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Platform.Storage;
using MaaFramework.Binding;
using MaaFramework.Binding.Buffers;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed record PcrStartConsent(string GameDay, string CountContext,
    string[] ScheduleAuthorizations, bool AttestCurrentPlan, bool ConfirmNewCycle = false);
public enum PcrDisplayRead { Activity, Input }
public enum PcrManagerRead { Info, AppInfo, Instances }

/// <summary>Single process owner. Each explicit button press creates a fresh child,
/// controller and grant. Saved instance data can never create this object.</summary>
public static class PcrAlphaOwner
{
    private static readonly object Gate = new();
    private static Session? current;
    private static int prompting;
    internal static bool HasActivePlanningBoundary { get { lock (Gate) return prompting != 0 || current != null; } }
    private sealed class PendingStart(MaaProcessor processor)
    {
        public readonly MaaProcessor Processor = processor;
        public readonly CancellationTokenSource Cancel = new();
        public bool Revoked;
    }
    private static PendingStart? pendingStart;
    // SetTasker(null) synchronously calls Revoke(this) as its first operation.
    // Consume exactly that call on this thread, not a concurrent Stop, and never
    // suppress revocation of an existing Session or clear a prior cancellation.
    [ThreadStatic] private static PendingStart? cleanupRevoke;
    private static string repository = "";
    private static string configPath = "";
    public static Func<JObject, Task<PcrStartConsent?>>? Confirm { private get; set; }
    // Replaces only the file picker in headless tests; it never grants input.
    public static Func<Task<string?>>? SelectConfiguration { private get; set; }
    // Offline acceptance substitutes only construction, never Start/job/owner routing.
    public static Func<JObject, MaaController>? ControllerBuilder { private get; set; }
    // Only native allocation is substituted. Without an offline Agent script this
    // exercises formal read-only preparation, never business qualification.
    // This typed seam cannot return an ADB/Win32 controller.
    public static Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? OfflineMuMuFactory { private get; set; }
    public static string? OfflineAgentScript { private get; set; }
    // A fixed read command seam, accepted only with an official CustomController
    // factory. It cannot turn a configuration or cached frame into authority.
    public static Func<PcrDisplayRead, CancellationToken, Task<string>>? OfflineDisplayReader { private get; set; }
    public static Func<PcrManagerRead, int?, CancellationToken, Task<string>>? OfflineManagerReader { private get; set; }
    private static bool HasOfflineConstruction => OfflineAgentScript != null
        && ((ControllerBuilder != null) ^ (OfflineMuMuFactory != null));
    internal static string? ActiveProfile { get { lock (Gate) return pendingStart?.Processor.InstanceId ?? current?.Processor.InstanceId; } }
    internal static bool HasActiveRun { get { lock (Gate) return prompting != 0 || current != null; } }
    internal static string Repository => repository;
    public static int ControllerCreations { get; private set; }
    public static int AgentCreations { get; private set; }
    public static int Grants { get; private set; }
    public static int SessionCreations { get; private set; }
    public static int LastAgentPort { get; private set; }

    // Internal explicit API only: no UI route, Start, business schema or grants.
    // The optional offline seam can return only the official CustomController.
    internal static PcrMuMuReadOnlyDiagnostic PrepareMuMuReadOnlyDiagnostic(JObject configuration,
        Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? offlineFactory = null)
        => new(configuration, offlineFactory);

    public static void Install(string root, string? isolatedConfigPath = null)
    {
        repository = Path.GetFullPath(root);
        configPath = isolatedConfigPath ?? Path.Combine(repository, ".runtime", "daily-config.json");
        PlatformControllerFactory.Create = _ => CreateController();
        PlatformAgentFactory.Start = StartAgent;
        AppDomain.CurrentDomain.ProcessExit += (_, _) => { Revoke(); current?.Child?.Dispose(); };
    }

    public static JObject Selection(string entry, string parameters)
    {
        if (entry != "PCRAlphaEntry") throw new InvalidOperationException("PCR 组合任务入口无效");
        var overrides = JToken.Parse(parameters);
        var p = new JObject();
        var layers = overrides is JArray array ? array : new JArray(overrides);
        foreach (var layer in layers)
        {
            if (layer is not JObject obj) throw new InvalidOperationException("Invalid pipeline override");
            foreach (var node in obj.Properties())
            {
                if (node.Value is not JObject fields) throw new InvalidOperationException("Invalid node override");
                var combined = p[node.Name] as JObject ?? new JObject();
                foreach (var field in fields.Properties()) combined[field.Name] = field.Value.DeepClone();
                p[node.Name] = combined;
            }
        }
        string[] nodes = {"Schedule", "Event", "Abyss", "Deep", "Rewards", "VeryHard", "Hard", "Normal", "RewardsFinal"};
        string[] modules = {"schedule_onekey", "event", "abyss", "deep", "rewards", "very_hard", "hard", "normal"};
        var selected = new JArray(); bool final = false;
        for (int i = 0; i < nodes.Length; i++)
        {
            var token = p["PCRAlphaSelect" + nodes[i]]?["attach"]?["selected"];
            if (nodes[i] == "Abyss" && token == null) continue; // old PI snapshots did not include this opt-in node
            if (token?.Type != JTokenType.Boolean) throw new InvalidOperationException("Invalid selection node");
            if (i == nodes.Length - 1) final = (bool)token;
            else if ((bool)token) selected.Add(modules[i]);
        }
        if (p["PCRAlphaEntry"]?["custom_action_param"] is JObject custom && custom["selected_modules"] is { } ordered)
        {
            if ((string?)custom["selection_mode"] != "pi_nodes_v1"
                || custom.Properties().Any(field => field.Name != "selection_mode" && field.Name != "selected_modules")
                || ordered is not JArray order || order.Any(value => value.Type != JTokenType.String))
                throw new InvalidOperationException("PCR 模块执行顺序无效");
            var ids = order.Values<string>().ToArray();
            if (ids.Distinct().Count() != ids.Length || ids.Length != selected.Count
                || ids.Any(id => !selected.Values<string>().Contains(id)))
                throw new InvalidOperationException("PCR 模块执行顺序与勾选模块不一致");
            selected = new JArray(ids);
        }
        if (selected.Count == 0 && !final) throw new InvalidOperationException("请先选择本次模块或末尾补领");
        return new JObject { ["version"] = 1, ["selected_modules"] = selected, ["rewards_final"] = final };
    }

    public static async Task RequestStartAsync(TaskQueueViewModel vm)
    {
        PendingStart attempt;
        lock (PcrProfileRunGate.StartGate)
        lock (Gate)
        {
            if (prompting != 0 || PcrLabyrinthOwner.IsActive || PcrProfileRunGate.UpdatePending) return;
            prompting = 1;
            pendingStart = attempt = new PendingStart(vm.Processor);
        }
        var handedToProcessor = false;
        try
        {
            PcrRuntimePaths.RequireUpdateComplete(AppContext.BaseDirectory, repository);
            lock (Gate) if (current != null && !current.Revoked) throw new InvalidOperationException("Previous owner has not closed");
            var offline = HasOfflineConstruction;
            var offlineBuilder = ControllerBuilder;
            var offlineMuMu = OfflineMuMuFactory;
            var offlineScript = OfflineAgentScript;
            var offlineReader = OfflineDisplayReader;
            var node = vm.Processor.PreviewPcrTask();
            var selection = Selection(node.Entry!, node.Param!);
            PcrFormalPreflight.RequireSelection(vm, selection); // before any Manager read or config selection
            if (PcrFormalPreflight.Enabled && (Confirm != null || SelectConfiguration != null || offline
                || ControllerBuilder != null || OfflineMuMuFactory != null || OfflineAgentScript != null
                || OfflineDisplayReader != null || OfflineManagerReader != null))
                throw new InvalidOperationException("diagnostic_test_seams_forbidden");
            var playerSettings = Confirm == null && SelectConfiguration == null;
            var validatePhysicalViewport = !playerSettings || vm.PcrSettings.ValidateDisplayMapping;
            var review = playerSettings || !offline || SelectConfiguration != null;
            var selectedTarget = review ? SelectedTarget(vm) : null;
            JObject? managerIdentity = null;
            // GUI one-key uses a fresh Owner-bound session and the current game button.
            // MuMu continuity remains checked where its Manager source exists;
            // generic ADB must not fabricate or inherit a historical instance identity.
            if (!PcrFormalPreflight.Enabled && playerSettings && selection["selected_modules"]!.Values<string>().Contains("schedule_onekey")
                && PcrMuMuIdentity.HasManagerSource(new JObject { ["adb_path"] = selectedTarget!.AdbPath, ["serial"] = selectedTarget.Serial }))
            {
                // Stop is available during the read. Restore editability only for
                // the synchronous export, after validating this same pending Start.
                vm.IsRunning = true;
                managerIdentity = await ReadManagerIdentityAsync(attempt, selectedTarget!, offline);
                RequirePendingStart(attempt);
                if (SelectedTarget(vm) != selectedTarget) throw new InvalidOperationException("schedule_identity_target_changed");
                vm.IsRunning = false;
                vm.PcrSettings.AcceptManagerIdentity(managerIdentity, new JObject {
                    ["adb_path"] = selectedTarget!.AdbPath, ["serial"] = selectedTarget.Serial });
            }
            var selectedPath = PcrFormalPreflight.Enabled ? PcrFormalPreflight.Export(vm, selection)
                : playerSettings ? vm.PcrSettings.ExportForStart(offline ? Path.GetDirectoryName(configPath)! : repository, selection)
                : SelectConfiguration != null ? await SelectConfiguration() : configPath;
            RequirePendingStart(attempt);
            if (selectedPath == null) return;
            if (!Path.IsPathFullyQualified(selectedPath) || selectedPath.StartsWith(@"\\")
                || selectedPath.StartsWith("//"))
                throw new InvalidOperationException("请选择本机独立 v3 配置文件的完整路径");
            // Export requires editable settings. Once it is complete, expose Stop
            // while the data-only Python child runs, and lock target edits until
            // the pending attempt has unwound. Toggle must not become a no-op
            // duplicate Start during this first asynchronous phase.
            vm.IsRunning = true;
            var snapshot = await ValidateConfigAsync(selectedPath, review, attempt.Cancel.Token);
            RequirePendingStart(attempt);
            if (review) RequireSelectedTarget(vm, selectedTarget!, snapshot);
            if (PcrFormalPreflight.Enabled)
            {
                PcrFormalPreflight.Trace("hard_stop_after_preflight");
                vm.Processor.AddLog("PCR 仅预检诊断完成：已硬停止，未创建会话/控制器/Agent，未发现或操作设备。", (Avalonia.Media.IBrush?)null);
                return;
            }
            var prompt = snapshot.Binding();
            prompt["combination"] = selection.DeepClone();
            if (review)
            {
                prompt["configuration_path"] = selectedPath;
                prompt["configuration"] = JObject.Parse(snapshot.Json)["config"]!.DeepClone();
            }
            var consent = playerSettings ? vm.PcrSettings.ConsentForStart(selection)
                : Confirm != null ? await Confirm(prompt) : null;
            RequirePendingStart(attempt);
            if (consent == null) return;
            consent = ValidateConsent(selection, (JObject)JObject.Parse(snapshot.Json)["config"]!, consent);
            var nowNode = vm.Processor.PreviewPcrTask();
            if (!JToken.DeepEquals(JToken.Parse(node.Param!), JToken.Parse(nowNode.Param!)))
                throw new InvalidOperationException("Selection changed during confirmation; Start again");
            if (snapshot != await ValidateConfigAsync(selectedPath, review, attempt.Cancel.Token))
                throw new InvalidOperationException("配置在确认期间已变化，请重新核对并开始");
            RequirePendingStart(attempt);
            if (review) RequireSelectedTarget(vm, selectedTarget!, snapshot);
            // Partial/ambiguous business hooks must not fall through to a device.
            // A typed CustomController factory alone tests preparation only.
            if (!offline && (offlineBuilder != null || offlineScript != null))
                PcrMuMuDisplay.RequireProductionQualification();
            // Release previous native tasker/Agent before arming; SetTasker revokes.
            RequirePendingStart(attempt);
            cleanupRevoke = attempt;
            try { vm.Processor.SetTasker(requireAllStopped: true); }
            finally { cleanupRevoke = null; }
            lock (Gate)
            {
                RequirePendingStart(attempt);
                if (current != null) throw new InvalidOperationException("Previous owner cleanup not confirmed");
                if (offlineBuilder != ControllerBuilder || offlineMuMu != OfflineMuMuFactory || offlineScript != OfflineAgentScript || offlineReader != OfflineDisplayReader)
                    throw new InvalidOperationException("Offline construction changed during Start");
                if (offline && offlineReader == null || offlineReader != null && offlineMuMu == null && offlineBuilder == null)
                    throw new InvalidOperationException("Offline allocation and fixed readback must be installed together");
                current = new Session(vm.Processor, selection, snapshot, consent) {
                    SelectedConfigPath = review ? selectedPath : null,
                    ConfigFileHash = review ? Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(selectedPath))) : null,
                    PreparationOnly = !offline && offlineMuMu != null,
                    Preparing = true, Target = selectedTarget, ValidatePhysicalViewport = validatePhysicalViewport };
                SessionCreations++;
            }
            if (!offline && offlineMuMu != null)
            {
                await PrepareReadOnlyAsync(vm, attempt, selectedTarget!);
                return;
            }
            Session s;
            lock (Gate) s = current!;
            vm.IsRunning = true;
            try
            {
                if (s.OfflineControllerBuilder == null)
                {
                    var mapping = await ReadMappingAsync(s);
                    RequirePendingStart(attempt);
                    if (managerIdentity != null && !JToken.DeepEquals(managerIdentity,
                        await ReadManagerIdentityAsync(attempt, selectedTarget!, offline)))
                        throw new InvalidOperationException("schedule_identity_drift_during_start");
                    if (snapshot != await ValidateConfigAsync(selectedPath, review, attempt.Cancel.Token))
                        throw new InvalidOperationException("Configuration changed during mapping");
                    RequireConfigFile(s);
                    RequirePendingStart(attempt);
                    if (review) RequireSelectedTarget(vm, selectedTarget!, snapshot);
                    var latest = vm.Processor.PreviewPcrTask();
                    if (!JToken.DeepEquals(selection, Selection(latest.Entry!, latest.Param!)))
                        throw new InvalidOperationException("Selection changed during mapping");
                    lock (Gate) { RequirePendingStart(attempt); s.Mapping = mapping; s.MappingChecked = s.Watch.Elapsed; }
                }
                lock (Gate) { RequirePendingStart(attempt); s.Preparing = false; }
            }
            catch { lock (Gate) { s.Preparing = false; } Revoke(vm.Processor); Release(vm.Processor); throw; }
            finally { vm.IsRunning = false; }
            RequirePendingStart(attempt);
            vm.StartTask();
            handedToProcessor = true;
        }
        catch (Exception ex)
        {
            PcrFormalPreflight.Trace("owner_refused", new JObject { ["error"] = ex.Message });
            var canceled = ex is OperationCanceledException && attempt.Cancel.IsCancellationRequested;
            Revoke();
            var message = "PCR Alpha 拒绝启动：" + ex.Message;
            if (canceled)
                message += "\n本次启动已取消。";
            else if (ex.Message == "schedule_manager_timeout")
                message += "\n读取所选 MuMu 信息超过 5 秒，任务尚未开始。请等模拟器启动完成，确认 PCR 已打开，再开始；仍超时时请保留日志。";
            else if (ex.Message is "PCR resumed target missing or ambiguous" or "PCR activity focus mismatch" or "PCR input focus mismatch")
                message += "\n无法确认所选模拟器中的 PCR 游戏画面，任务尚未开始。请打开并登录 PCR，切回游戏主页后再开始；仍提示冲突时请核对设备选择并保留日志。";
            else if (ex.Message.StartsWith("schedule_manager_", StringComparison.Ordinal))
                message += "\n无法核验所选模拟器，任务尚未开始。请确认所选 MuMu 和 PCR 已打开并核对设备；仍失败时保留日志交维护端核查。";
            vm.Processor.AddLog(message, (Avalonia.Media.IBrush?)null);
        }
        finally
        {
            lock (Gate) { pendingStart = null; prompting = 0; }
            if (!handedToProcessor) vm.IsRunning = false;
            attempt.Cancel.Dispose();
            PcrFormalPreflight.Trace("owner_finally");
        }
    }

    private static async Task<JObject> ReadManagerIdentityAsync(PendingStart attempt, GuiTarget target, bool offline)
    {
        var connection = new JObject { ["adb_path"] = target.AdbPath, ["serial"] = target.Serial };
        var reader = OfflineManagerReader;
        if (!PcrMuMuIdentity.HasManagerSource(connection))
            throw new InvalidOperationException("日程尚未取得此模拟器可核验的跨重启实例身份；雷电索引、PID和端口不足以接续账本。请取消日程后运行其他已选任务，保留原设置和未决记录。[schedule_identity_source_unverified]");
        if (reader != null && (!offline || OfflineMuMuFactory == null))
            throw new InvalidOperationException("Manager replay requires official offline CustomController");
        if (reader == null && (offline || OfflineMuMuFactory != null || ControllerBuilder != null))
            throw new InvalidOperationException("Offline Manager readback missing");
        if (reader == null) PcrMuMuDisplay.RequireTransport(new JObject { ["connection"] = connection,
            ["controller"] = new JObject { ["kind"] = "adb", ["app_package"] = PcrMuMuDisplay.Package } });
        async Task<string> Read(PcrManagerRead command, int? index)
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(attempt.Cancel.Token);
            timeout.CancelAfter(TimeSpan.FromSeconds(5));
            RequirePendingStart(attempt);
            var arguments = PcrMuMuIdentity.Arguments(command, index);
            try
            {
                if (reader != null) return await reader(command, index, timeout.Token).WaitAsync(timeout.Token);
                var info = new ProcessStartInfo(PcrMuMuIdentity.ManagerFor(connection)) { UseShellExecute = false,
                    CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                    StandardOutputEncoding = System.Text.Encoding.UTF8, StandardErrorEncoding = System.Text.Encoding.UTF8,
                    WorkingDirectory = repository };
                foreach (var arg in arguments)
                    info.ArgumentList.Add(arg);
                Process started;
                lock (Gate) { RequirePendingStart(attempt); timeout.Token.ThrowIfCancellationRequested();
                    started = Process.Start(info) ?? throw new InvalidOperationException("schedule_manager_start_failed"); }
                using var process = started;
                var output = process.StandardOutput.ReadToEndAsync(); var error = process.StandardError.ReadToEndAsync();
                try {
                    await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
                    var stdout = await output.WaitAsync(timeout.Token).ConfigureAwait(false);
                    var stderr = await error.WaitAsync(timeout.Token).ConfigureAwait(false);
                    if (process.ExitCode != 0 || !string.IsNullOrWhiteSpace(stderr))
                        throw new InvalidOperationException("schedule_manager_command_failed:" + process.ExitCode);
                    return stdout;
                } catch {
                    if (!process.HasExited) process.Kill(true);
                    if (!process.WaitForExit(5000)) throw new InvalidOperationException("schedule_manager_did_not_exit");
                    throw;
                }
            }
            catch (OperationCanceledException ex) when (timeout.IsCancellationRequested && !attempt.Cancel.IsCancellationRequested)
            {
                throw new InvalidOperationException("schedule_manager_timeout", ex);
            }
        }
        var inventory = await Read(PcrManagerRead.Instances, null);
        var index = PcrMuMuIdentity.FindIndex(inventory, connection);
        var infoText = await Read(PcrManagerRead.Info, index);
        var appText = await Read(PcrManagerRead.AppInfo, index);
        RequirePendingStart(attempt);
        if (reader != OfflineManagerReader) throw new InvalidOperationException("Manager readback changed during Start");
        var identity = PcrMuMuIdentity.Parse(infoText, appText, connection);
        if ((int?)identity["index"] != index)
            throw new InvalidOperationException("schedule_manager_instance_changed_during_Start");
        return identity;
    }

    private static async Task<PcrMuMuDisplay.Mapping> ReadMappingAsync(Session s)
    {
        async Task<string> Read(PcrDisplayRead command)
        {
            lock (Gate)
                if (current != s || s.Revoked) throw new InvalidOperationException("Mapping session revoked");
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(s.Cancel.Token);
            timeout.CancelAfter(TimeSpan.FromSeconds(5));
            if (s.DisplayReader != null)
                return await s.DisplayReader(command, timeout.Token).WaitAsync(timeout.Token).ConfigureAwait(false);
            if (s.IsOffline || s.MuMuFactory != null || s.OfflineControllerBuilder != null)
                throw new InvalidOperationException("Offline mapping readback missing");
            var binding = s.Config.Binding();
            PcrMuMuDisplay.RequireTransport(binding);
            var info = new ProcessStartInfo((string)binding["connection"]!["adb_path"]!) {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true,
                RedirectStandardError = true, WorkingDirectory = repository };
            foreach (var arg in new[] { "-s", (string)binding["connection"]!["serial"]!, "shell", "dumpsys",
                command == PcrDisplayRead.Activity ? "activity" : "input" }) info.ArgumentList.Add(arg);
            if (command == PcrDisplayRead.Activity) info.ArgumentList.Add("activities");
            // Same bounded original-process ownership as config preflight. No
            // discovery, connect, server control, shell string or retry surface.
            Process started;
            lock (Gate)
            {
                // Serialize launch with Stop: cancellation after the earlier
                // read check must not launch a new process for a revoked owner.
                if (current != s || s.Revoked) throw new InvalidOperationException("Mapping session revoked");
                timeout.Token.ThrowIfCancellationRequested();
                started = Process.Start(info) ?? throw new InvalidOperationException("Mapping read failed to start");
            }
            using var process = started;
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            try { await process.WaitForExitAsync(timeout.Token).ConfigureAwait(false); }
            catch
            {
                if (!process.HasExited) process.Kill(true);
                if (!process.WaitForExit(5000)) throw new InvalidOperationException("Mapping reader did not exit");
                throw;
            }
            if (process.ExitCode != 0 || !string.IsNullOrWhiteSpace(await error.WaitAsync(timeout.Token).ConfigureAwait(false)))
                throw new InvalidOperationException("Mapping read failed");
            return await output.WaitAsync(timeout.Token).ConfigureAwait(false);
        }
        var activity = await Read(PcrDisplayRead.Activity).ConfigureAwait(false);
        var input = await Read(PcrDisplayRead.Input).ConfigureAwait(false);
        s.Cancel.Token.ThrowIfCancellationRequested();
        return PcrMuMuDisplay.ParseMapping(activity, input, s.ValidatePhysicalViewport);
    }

    private static async Task CheckMappingAsync(Session s)
    {
        var observed = await ReadMappingAsync(s).ConfigureAwait(false);
        RequireConfigFile(s);
        if (s.Target != null) RequireSelectedTarget(s.Processor.ViewModel!, s.Target, s.Config);
        lock (Gate)
        {
            if (current != s || s.Revoked || s.Controller?.IsConnected != true || observed != s.Mapping)
                throw new InvalidOperationException("Mapping changed, disconnected or revoked; no reconnect");
            s.MappingChecked = s.Watch.Elapsed;
        }
    }

    private static void RequireConfigFile(Session s)
    {
        if (s.SelectedConfigPath != null && s.ConfigFileHash !=
            Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(s.SelectedConfigPath))))
            throw new InvalidOperationException("Configuration changed during session");
    }

    private static async Task WatchMappingAsync(Session s)
    {
        try
        {
            while (!s.Cancel.IsCancellationRequested)
            {
                await Task.Delay(1000, s.Cancel.Token).ConfigureAwait(false);
                await CheckMappingAsync(s).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (s.Cancel.IsCancellationRequested) { }
        catch (Exception ex)
        {
            lock (Gate)
            {
                if (current != s) return;
                Revoke(s.Processor);
            }
            await Avalonia.Threading.Dispatcher.UIThread.InvokeAsync(() => {
                lock (Gate) if (current != s) return;
                s.Processor.AddLog("PCR mapping stopped: " + ex.Message, (Avalonia.Media.IBrush?)null);
                s.Processor.Stop(MFAAvalonia.Helper.ValueType.MFATask.MFATaskStatus.STOPPED);
            });
        }
    }

    private static void RequirePendingStart(PendingStart attempt)
    {
        lock (Gate)
            if (pendingStart != attempt || attempt.Revoked)
                throw new InvalidOperationException("Start revoked while awaiting confirmation or cleanup");
    }

    // Remains in this same Owner lifecycle. No Tasker, StartTask, Agent, Append,
    // grant or input operation is allowed while preparing or after it closes.
    private static async Task PrepareReadOnlyAsync(TaskQueueViewModel vm, PendingStart attempt, GuiTarget target)
    {
        Session s;
        lock (Gate) s = current ?? throw new InvalidOperationException("Preparation owner missing");
        var result = new JObject { ["invocation_id"] = s.Invocation, ["read_only"] = true,
            ["production_qualified"] = false, ["binding"] = s.Config.Binding(),
            ["configuration_path"] = s.SelectedConfigPath, ["outcome"] = "failed" };
        vm.IsRunning = true; // Keep the existing GUI Stop command available.
        try
        {
            RequireSelectedTarget(vm, target, s.Config);
            await Task.Run(() =>
            {
                RequirePreparation(s, attempt);
                var controller = CreateController(preparation: true);
                if (!controller.SetOption_ScreenshotUseRawSize(true))
                    throw new InvalidOperationException("Read-only raw screenshot option failed");
                RequirePreparation(s, attempt);
                if (controller.LinkStart().Wait() != MaaJobStatus.Succeeded || !controller.IsConnected)
                    throw new InvalidOperationException("Read-only connection failed; no reconnect");
                RequirePreparation(s, attempt);
                if (controller.Screencap().Wait() != MaaJobStatus.Succeeded || !controller.IsConnected)
                    throw new InvalidOperationException("Read-only screenshot failed/disconnected; no retry");
                RequirePreparation(s, attempt);
                using var raw = new MaaImageBuffer();
                if (!controller.GetCachedImage(raw) || raw.IsEmpty || raw.Width <= 0 || raw.Height <= 0
                    || !raw.TryGetEncodedData(out byte[]? bytes) || bytes == null || bytes.Length == 0)
                    throw new InvalidOperationException("Read-only raw frame unavailable");
                result["raw_width"] = raw.Width; result["raw_height"] = raw.Height;
                result["frame_sha256"] = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(bytes));
                result["observed_utc"] = DateTimeOffset.UtcNow.ToString("O");
                PcrMuMuDisplay.RequireFrameSize(raw.Width, raw.Height);
                RequirePreparation(s, attempt);
                if (!controller.IsConnected) throw new InvalidOperationException("Read-only screenshot failed/disconnected");
            });
            RequirePreparation(s, attempt);
            RequireSelectedTarget(vm, target, s.Config);
            var node = vm.Processor.PreviewPcrTask();
            if (!JToken.DeepEquals(s.Selection, Selection(node.Entry!, node.Param!)))
                throw new InvalidOperationException("Selection changed during preparation; candidate revoked");
            if (s.Config != await ValidateConfigAsync(s.SelectedConfigPath!, true, s.Cancel.Token))
                throw new InvalidOperationException("Configuration changed during preparation; candidate revoked");
            RequirePreparation(s, attempt);
            RequireSelectedTarget(vm, target, s.Config);
            if (s.Controller?.IsConnected != true) throw new InvalidOperationException("Read-only screenshot failed/disconnected");
            result["outcome"] = "raw_observed";
            // Even a plausible current frame is not proof of input identity.
            PcrMuMuDisplay.RequireProductionQualification();
        }
        catch (Exception ex) { result["reason"] = ex.Message; }
        finally
        {
            // Revoke immediately on Stop; dispose only after the in-flight native
            // call has returned. Release must not drop this owner prematurely.
            lock (Gate) s.Revoked = true;
            try
            {
                s.Controller?.Dispose();
                result["controller_disposed"] = true;
                lock (Gate) if (current == s) current = null;
            }
            catch (Exception ex) { result["controller_disposed"] = false; result["dispose_failure"] = ex.Message; }
            vm.IsRunning = false;
            vm.Processor.AddLog("PCR Alpha 只读准备（尚无业务资格）：" + result.ToString(Formatting.None), (Avalonia.Media.IBrush?)null);
        }
    }

    private static void RequirePreparation(Session s, PendingStart attempt)
    {
        lock (Gate)
        {
            RequirePendingStart(attempt);
            if (current != s || !s.PreparationOnly || s.Revoked || s.Watch.Elapsed.TotalSeconds >= 1800
                || s.Processor != attempt.Processor || s.Processor != MaaProcessorManager.Instance.Current)
                throw new InvalidOperationException("Read-only candidate revoked; no reconnect or arming");
        }
    }

    private sealed record GuiTarget(string AdbPath, string Serial);
    private static GuiTarget SelectedTarget(TaskQueueViewModel vm)
    {
        if (vm.CurrentDevice is not AdbDeviceInfo device
            || !AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(device.AdbPath, device.AdbSerial,
                out var path, out var serial))
            throw new InvalidOperationException("设备栏：请选择有效的本机 ADB 目标后开始；任务草稿已保留。");
        return new GuiTarget(path, serial);
    }
    private static void RequireSelectedTarget(TaskQueueViewModel vm, GuiTarget target, ValidatedConfig snapshot)
    {
        var connection = snapshot.Binding()["connection"]!;
        if (SelectedTarget(vm) != target
            || !string.Equals(target.AdbPath, Path.GetFullPath((string)connection["adb_path"]!), StringComparison.OrdinalIgnoreCase)
            || target.Serial != (string?)connection["serial"])
            throw new InvalidOperationException("GUI 目标与当前 v3 配置不一致，请重新选择并核对");
    }
    private static async Task<string?> SelectConfigurationDialog()
    {
        var storage = Instances.StorageProvider ?? throw new InvalidOperationException("本机配置文件选择器不可用");
        var files = await storage.OpenFilePickerAsync(new FilePickerOpenOptions {
            Title = "选择独立 v3 MuMu 配置（只读核对，不迁移旧配置）", AllowMultiple = false,
            FileTypeFilter = [new FilePickerFileType("JSON 配置") { Patterns = ["*.json"] }] });
        return files.Count == 1 ? files[0].TryGetLocalPath() : null;
    }

    // The snapshot contains only immutable text. Every consumer gets a fresh parse.
    private sealed record ValidatedConfig(string Json)
    {
        public JObject Binding()
        {
            var data = JObject.Parse(Json);
            var key = (int?)data["config"]!["version"] switch { 3 => "mumu", 4 => "controller", _ => "device" };
            return new JObject { [key] = data["config"]![key]!.DeepClone(),
                ["connection"] = data["connection"]!.DeepClone() };
        }
    }
    private static async Task<ValidatedConfig> ValidateConfigAsync(string selectedPath, bool review,
        CancellationToken cancellationToken = default)
    {
        var watch = Stopwatch.StartNew();
        var traceRoot = Path.Combine(PcrFormalPreflight.Enabled ? PcrFormalPreflight.Output
            : HasOfflineConstruction ? Path.GetDirectoryName(configPath)! : repository,
            ".runtime", "alpha-agent", "preflight");
        Directory.CreateDirectory(traceRoot);
        var trace = Path.Combine(traceRoot, Guid.NewGuid().ToString("N") + ".jsonl");
        void Trace(string phase, int? pid = null, int? exit = null)
        {
            try { File.AppendAllText(trace,
                new JObject { ["phase"] = phase, ["elapsed_ms"] = watch.ElapsedMilliseconds,
                    ["pid"] = pid, ["exit"] = exit, ["thread"] = Environment.CurrentManagedThreadId,
                    ["ui_context"] = SynchronizationContext.Current?.GetType().Name,
                    ["unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() }.ToString(Formatting.None) + Environment.NewLine); }
            // Diagnostic IO must not interrupt original-process cleanup.
            catch (IOException ex) { LoggerHelper.Warning("PCR 预检诊断写入失败：" + ex.Message); }
            catch (UnauthorizedAccessException ex) { LoggerHelper.Warning("PCR 预检诊断写入失败：" + ex.Message); }
        }
        Trace("before_process_start");
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(timeout.Token, cancellationToken);
        var info = new ProcessStartInfo(PcrRuntimePaths.ResolvePython(repository))
        { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true,
            RedirectStandardError = true, WorkingDirectory = repository,
            StandardOutputEncoding = System.Text.Encoding.UTF8, StandardErrorEncoding = System.Text.Encoding.UTF8 };
        info.Environment["PYTHONUTF8"] = "1";
        if (PcrFormalPreflight.Enabled)
            info.Environment["PCR_PREFLIGHT_PHASE_TRACE"] = trace + ".python.jsonl";
        info.ArgumentList.Add("-B"); info.ArgumentList.Add(Path.Combine(repository, "scripts", "validate-alpha-config.py"));
        info.ArgumentList.Add(selectedPath);
        if (review)
            info.ArgumentList.Add("--review-v3");
        else if (HasOfflineConstruction)
            info.ArgumentList.Add("--offline-custom-controller");
        if (PcrFormalPreflight.Enabled) PcrFormalPreflight.Trace("validator_launch", new JObject { ["python"] = info.FileName,
            ["cwd"] = info.WorkingDirectory, ["config"] = selectedPath,
            ["config_sha256"] = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(selectedPath))),
            ["review"] = review, ["redirect_stdout"] = info.RedirectStandardOutput, ["redirect_stderr"] = info.RedirectStandardError });
        using var process = Process.Start(info) ?? throw new InvalidOperationException("无法启动配置预检查 Python");
        Trace("process_started", process.Id);
        var output = process.StandardOutput.ReadToEndAsync(); var error = process.StandardError.ReadToEndAsync();
        Trace("pipes_reading", process.Id);
        var phase = "等待 Python 退出";
        try
        {
            await process.WaitForExitAsync(linked.Token).ConfigureAwait(false);
            Trace("process_exited", process.Id, process.ExitCode);
            phase = "读取 Python 输出";
            var stdout = await output.WaitAsync(linked.Token).ConfigureAwait(false);
            var stderr = await error.WaitAsync(linked.Token).ConfigureAwait(false);
            Trace("pipes_eof", process.Id, process.ExitCode);
            if (process.ExitCode != 0)
                throw new InvalidOperationException("配置预检查失败：" + (string.IsNullOrWhiteSpace(stderr) ? $"Python 退出码 {process.ExitCode}" : stderr.Trim()));
            return new ValidatedConfig(stdout.Trim());
        }
        catch (OperationCanceledException)
        {
            if (PcrFormalPreflight.Enabled) PcrFormalPreflight.Trace("validator_cancel_state", new JObject {
                ["pid"] = process.Id, ["process_exited"] = process.HasExited,
                ["stdout_completed"] = output.IsCompleted, ["stderr_completed"] = error.IsCompleted,
                ["user_cancelled"] = cancellationToken.IsCancellationRequested });
            if (!process.HasExited) process.Kill(true);
            if (!process.WaitForExit(5000))
                throw new InvalidOperationException("配置预检查取消后 Python 未在 5 秒内退出");
            Trace("cancelled_reaped", process.Id, process.ExitCode);
            if (cancellationToken.IsCancellationRequested)
                throw new InvalidOperationException("本次启动已取消，配置预检查子进程已回收");
            throw new InvalidOperationException($"配置预检查超时：{phase}超过 10 秒（实测 {watch.ElapsedMilliseconds} ms）；Python 子进程已回收；诊断：{trace}");
        }
        catch
        {
            if (!process.HasExited) { process.Kill(true); process.WaitForExit(5000); }
            throw;
        }
    }

    public static void Require(MaaProcessor processor)
    {
        lock (Gate)
        {
            if (current == null || current.Processor != processor || current.Revoked || current.Watch.Elapsed.TotalSeconds >= 1800)
                throw new InvalidOperationException("Explicit current Start required");
            if (current.PreparationOnly || current.Preparing || !current.IsOffline && current.Mapping == null)
                PcrMuMuDisplay.RequireProductionQualification();
            if (current.Mapping != null && (current.Watch.Elapsed - current.MappingChecked).TotalSeconds > 15)
                throw new InvalidOperationException("Session mapping expired");
            if (current.RawVerified && current.Controller?.IsConnected != true)
                throw new InvalidOperationException("Session disconnected; no reconnect");
        }
    }
    public static bool ClaimStart(MaaProcessor processor)
    {
        lock (Gate)
        {
            if (current == null || current.Processor != processor || current.Revoked || current.Started || current.PreparationOnly || current.Preparing || !current.IsOffline && current.Mapping == null) return false;
            current.Started = true; return true;
        }
    }
    public static void BeforeAppend(MaaProcessor processor, string entry, string param)
    {
        lock (Gate)
        {
            Require(processor);
            if (!current!.RawVerified || current.Appending || !JToken.DeepEquals(current.Selection, Selection(entry, param)))
                throw new InvalidOperationException("Root/selection already used or changed");
            var tasker = processor.MaaTasker ?? throw new InvalidOperationException("Owner Tasker missing");
            if (current.BoundTasker != null || !ReferenceEquals(tasker.Controller, current.Controller)
                || tasker.Resource == null)
                throw new InvalidOperationException("Owner actual binding changed before Append");
            // Hold the actual objects for the entire callback lifetime. A new
            // object with the same UUID/hash cannot inherit this invocation.
            current.BoundTasker = tasker;
            current.BoundResource = tasker.Resource;
            current.Appending = true;
        }
    }
    public static void RegisterJob(MaaProcessor processor, long id)
    {
        lock (Gate)
        {
            Require(processor);
            if (id <= 0 || current!.Root != 0 || !current.Appending) throw new InvalidOperationException("Invalid root registration");
            current.Root = id; Monitor.PulseAll(Gate);
        }
    }
    private static bool BindingUnchanged(Session s)
    {
        var tasker = s.BoundTasker;
        return tasker != null && ReferenceEquals(s.Processor.MaaTasker, tasker)
            && ReferenceEquals(tasker.Resource, s.BoundResource)
            && ReferenceEquals(tasker.Controller, s.Controller)
            && tasker.IsInitialized && !tasker.IsStopping && s.Controller?.IsConnected == true;
    }

    public static void Revoke(MaaProcessor? processor = null)
    {
        lock (Gate)
        {
            if (pendingStart != null && (processor == null || pendingStart.Processor == processor))
            {
                if (processor != null && cleanupRevoke == pendingStart)
                    cleanupRevoke = null;
                else
                {
                    pendingStart.Revoked = true;
                    pendingStart.Cancel.Cancel();
                }
            }
            if (current == null || processor != null && current.Processor != processor) return;
            current.Revoked = true; Monitor.PulseAll(Gate);
            current.Cancel.Cancel();
            current.Child?.RevokeAuthorization();
        }
    }
    public static void Release(MaaProcessor processor)
    {
        lock (Gate)
        {
            if (current?.Processor != processor) return;
            current.Revoked = true;
            current.Cancel.Cancel();
            if (current.PreparationOnly || current.Preparing) return; // preparation finally owns disposal
            current.Child?.Dispose();
            current = null; Monitor.PulseAll(Gate);
        }
    }
    private static MaaController CreateController(bool preparation = false)
    {
        lock (Gate)
        {
            if (current == null) throw new InvalidOperationException("Controller factory has no current Start");
            if (preparation) RequirePreparation(current, pendingStart ?? throw new InvalidOperationException("Preparation revoked"));
            else Require(current.Processor);
            if (current.ConstructionAttempted) throw new InvalidOperationException("Controller already constructed; no reuse/reconnect");
            current.ConstructionAttempted = true;
            var binding = current.Config.Binding();
            var builder = current.OfflineControllerBuilder;
            var controller = preparation
                ? PcrMuMuDisplay.CreatePreparationController(binding, Path.Combine(AppContext.BaseDirectory, "libs", "MaaAgentBinary"), current.MuMuFactory)
                : current.Mapping != null
                ? PcrMuMuDisplay.CreateStandardController(binding, Path.Combine(AppContext.BaseDirectory, "libs", "MaaAgentBinary"), current.Mapping, current.MuMuFactory)
                : builder != null
                ? builder((JObject)binding.DeepClone()) ?? throw new InvalidOperationException("Injected controller returned null")
                : PcrMuMuDisplay.CreateController(binding, Path.Combine(AppContext.BaseDirectory, "libs", "MaaAgentBinary"));
            if (builder != null && controller is not MaaCustomController)
            {
                controller.Dispose();
                throw new InvalidOperationException("Offline acceptance requires the official CustomController");
            }
            current.Controller = controller;
            ControllerCreations++;
            return current.Controller;
        }
    }
    private static IPlatformAgentSession StartAgent(PlatformAgentStartRequest request)
    {
        lock (Gate)
        {
            var s = current ?? throw new InvalidOperationException("Agent has no owner");
            Require(s.Processor);
            if (request.InstanceId != s.Processor.InstanceId || s.Controller == null || s.Child != null || !s.Controller.IsConnected || !s.RawVerified)
                throw new InvalidOperationException("Agent owner/controller mismatch");
            if (!int.TryParse(request.Identifier, out var port) || port < 1 || port > 65535
                || !System.Net.NetworkInformation.IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners().Any(
                    ep => ep.Port == port && ep.Address.Equals(System.Net.IPAddress.Loopback)))
                throw new InvalidOperationException("PCR Agent requires a verified 127.0.0.1 TCP listener; no IPC fallback");
            LastAgentPort = port;
            s.Uuid = s.Controller.Uuid;
            var script = s.AgentScript ?? Path.Combine(repository, "scripts", "start-alpha-owned-agent.py");
            s.Child = new ChildSession(s, request, repository, script); AgentCreations++;
            return s.Child;
        }
    }
    public static void RequestRaw(MaaProcessor processor, MaaController controller)
    {
        Require(processor);
        if (current!.Controller != controller || !controller.SetOption_ScreenshotUseRawSize(true))
            throw new InvalidOperationException("Cannot request raw screenshot");
    }
    public static void VerifyRaw(MaaProcessor processor, MaaController controller)
    {
        Require(processor);
        var s = current!;
        if (s.Controller != controller || s.RawVerified) throw new InvalidOperationException("Raw controller mismatch or old frame");
        // Explicit successful capture, never accept GetImage's cached fallback.
        if (controller.Screencap().Wait() != MaaJobStatus.Succeeded || !controller.IsConnected)
            throw new InvalidOperationException("Fresh screenshot failed/disconnected");
        using var raw = processor.GetImage(controller, false);
        if (raw == null) throw new InvalidOperationException("Fresh raw frame unavailable");
        PcrMuMuDisplay.RequireFrameSize(raw.Width, raw.Height);
        if (!controller.SetOption_ScreenshotUseRawSize(false)) throw new InvalidOperationException("Cannot disable raw screenshot");
        if (!controller.SetOption_ScreenshotTargetShortSide(720)) throw new InvalidOperationException("Cannot set 720 short side");
        if (s.Mapping != null) CheckMappingAsync(s).GetAwaiter().GetResult();
        lock (Gate)
        {
            Require(processor);
            if (current != s || !controller.IsConnected) throw new InvalidOperationException("Fresh frame session revoked");
            s.RawVerified = true;
            if (s.Mapping != null) s.MappingWatch = Task.Run(() => WatchMappingAsync(s));
        }
    }
    private sealed class Session(MaaProcessor processor, JObject selection, ValidatedConfig config, PcrStartConsent consent)
    {
        // Capture the test-only construction seam per session. Changing static
        // hooks later cannot turn a production session into an offline one.
        public readonly Func<JObject, MaaController>? OfflineControllerBuilder = OfflineAgentScript != null ? ControllerBuilder : null;
        public readonly Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? MuMuFactory = OfflineMuMuFactory;
        public readonly string? AgentScript = OfflineAgentScript;
        public readonly Func<PcrDisplayRead, CancellationToken, Task<string>>? DisplayReader = OfflineDisplayReader;
        public readonly CancellationTokenSource Cancel = new();
        public bool ValidatePhysicalViewport { get; init; } = true;
        public PcrMuMuDisplay.Mapping? Mapping;
        public TimeSpan MappingChecked;
        public Task? MappingWatch;
        public GuiTarget? Target;
        public bool Preparing;
        public bool IsOffline => AgentScript != null && ((OfflineControllerBuilder != null) ^ (MuMuFactory != null));
        public readonly MaaProcessor Processor = processor;
        public readonly JObject Selection = (JObject)selection.DeepClone();
        public readonly ValidatedConfig Config = config;
        public string? SelectedConfigPath { get; init; }
        public string? ConfigFileHash { get; init; }
        public bool PreparationOnly { get; init; }
        public readonly PcrStartConsent Consent = consent;
        public readonly Stopwatch Watch = Stopwatch.StartNew();
        public readonly string Invocation = Guid.NewGuid().ToString("N");
        public bool Revoked, Started, Appending, Consumed, RawVerified, ConstructionAttempted;
        public long Root;
        public string Uuid = "";
        public MaaController? Controller;
        public MaaTasker? BoundTasker;
        public MaaResource? BoundResource;
        public ChildSession? Child;
    }
    private sealed class ChildSession : IPlatformAgentSession, IPlatformAgentStartupStatus
    {
        private readonly PcrWindowsChild process = new();
        private readonly Session session;
        private int disposed;
        private readonly object exitGate = new();
        public string? StartupFailure
        {
            get
            {
                lock (exitGate)
                    return disposed != 0 ? "PCR Agent session disposed" : process.StartupFailure;
            }
        }
        public void CompleteStartup()
        {
            lock (exitGate)
            {
                if (disposed != 0) throw new ObjectDisposedException(nameof(ChildSession));
                process.VerifyMembership();
                if (StartupFailure is { } failure) throw new InvalidOperationException(failure);
            }
        }
        public void RevokeAuthorization()
        {
            lock (exitGate)
            {
                // Existing grants are rejected by the next live source probe
                // and native Stop. Keep EOF in the established disposal order;
                // killing the server before AgentClient.Disconnect can deadlock.
                if (disposed == 0 && !process.RevokeGrants()) Dispose();
            }
        }
        public void RevokeInput()
        {
            lock (exitGate)
            {
                session.Revoked = true;
                if (disposed == 0) { process.CloseInput(); RevokeAuthorization(); }
            }
        }
        public ChildSession(Session owner, PlatformAgentStartRequest request, string root, string script)
        {
            session = owner;
            // Publish before the first native allocation: even a failed constructor
            // must leave an owner that Release can retry, blocking a new Start.
            owner.Child = this;
            void RevokeChannel(Task ended)
            {
                _ = ended.Exception;
                try
                {
                    lock (Gate) { owner.Revoked = true; Monitor.PulseAll(Gate); RevokeAuthorization(); }
                }
                catch (Exception ex) { request.Output("error: PCR owner revoke: " + ex.GetType().Name); }
            }
            try
            {
                var arguments = owner.SelectedConfigPath is { } selectedPath
                    ? new[] { "-B", script, request.Identifier, selectedPath }
                    : new[] { "-B", script, request.Identifier };
                process.Start(PcrRuntimePaths.ResolvePython(root), root, arguments);
                _ = process.Send(owner.Config.Json).ContinueWith(RevokeChannel,
                    CancellationToken.None, TaskContinuationOptions.OnlyOnFaulted, TaskScheduler.Default);
            }
            catch
            {
                owner.Revoked = true;
                Dispose(); // throws and retains this owner if exit/EOF is unproved
                throw;
            }
            // Revocation must not wait for stderr EOF. These continuations are
            // separate from the pipe tasks that Dispose waits on under Gate.
            _ = process.StdoutCompletion.ContinueWith(RevokeChannel, TaskScheduler.Default);
            _ = process.StderrCompletion.ContinueWith(RevokeChannel,
                CancellationToken.None, TaskContinuationOptions.OnlyOnFaulted, TaskScheduler.Default);
            _ = Task.Run(async () =>
            {
                try
                {
                    // Pipe pumps never enter Gate or call GUI code. Release may
                    // hold Gate while waiting for native exit and both pipe EOFs.
                    await foreach (var (error, line) in process.Lines.Reader.ReadAllAsync())
                    {
                        if (error) request.Output("error: " + line);
                        else if (line.StartsWith("pcr-owner-request:", StringComparison.Ordinal))
                        {
                            var reply = Consume(JObject.Parse(line[18..]));
                            await ((bool?)reply["ok"] == true
                                ? process.SendGrant(reply.ToString(Formatting.None))
                                : process.Send(reply.ToString(Formatting.None)));
                        }
                        else if (line.StartsWith("pcr-owner-source:", StringComparison.Ordinal))
                        {
                            var reply = CheckSource(JObject.Parse(line[17..]));
                            await ((bool?)reply["ok"] == true
                                ? process.SendGrant(reply.ToString(Formatting.None))
                                : process.Send(reply.ToString(Formatting.None)));
                        }
                        else if (line.StartsWith("info:") || line.StartsWith("error:") || line.StartsWith("warning:")) request.Output(line);
                    }
                }
                catch (Exception ex) { request.Output("error: PCR owner channel: " + ex.GetType().Name); }
                finally { RevokeChannel(Task.CompletedTask); }
            });
        }
        private JObject CheckSource(JObject request)
        {
            lock (Gate)
            {
                var reply = (JObject)request.DeepClone();
                var valid = session == current && session.Consumed && !session.Revoked
                    && session.Watch.Elapsed.TotalSeconds < 1800
                    && (session.Mapping == null || (session.Watch.Elapsed - session.MappingChecked).TotalSeconds <= 15)
                    && (long?)request["root_task_id"] == session.Root
                    && (string?)request["controller_uuid"] == session.Uuid
                    && (string?)request["invocation_id"] == session.Invocation
                    && Guid.TryParseExact((string?)request["probe_id"], "N", out _)
                    && BindingUnchanged(session);
                if (!valid) session.Revoked = true;
                reply["ok"] = valid;
                return reply;
            }
        }
        private JObject Consume(JObject request)
        {
            lock (Gate)
            {
                // Callback can precede AppendTask's managed return. Never call the
                // SDK under this lock; registration has a bounded wait, no retry.
                var wait = Stopwatch.StartNew();
                while (session.Root == 0 && !session.Revoked && wait.Elapsed.TotalSeconds < 2)
                    Monitor.Wait(Gate, 20);
                if (session != current || session.PreparationOnly || session.Preparing || !session.IsOffline && session.Mapping == null || session.Revoked || session.Consumed || session.Root == 0
                    || session.Mapping != null && (session.Watch.Elapsed - session.MappingChecked).TotalSeconds > 15
                    || !BindingUnchanged(session)
                    || session.Watch.Elapsed.TotalSeconds >= 1800
                    || (long?)request["root_task_id"] != session.Root || (string?)request["controller_uuid"] != session.Uuid
                    || !JToken.DeepEquals(request["combination"], session.Selection))
                { session.Revoked = true; return new JObject { ["ok"] = false }; }
                session.Consumed = true; Grants++;
                var binding = session.Config.Binding();
                var reply = new JObject { ["ok"] = true, ["fixed_binding"] = true, ["invocation_id"] = session.Invocation,
                    ["root_task_id"] = session.Root, ["controller_uuid"] = session.Uuid,
                    ["serial"] = binding["connection"]!["serial"],
                    ["combination"] = session.Selection.DeepClone(), ["execute"] = true,
                    ["game_day"] = session.Consent.GameDay, ["count_context"] = session.Consent.CountContext,
                    ["remaining_seconds"] = 1800 - session.Watch.Elapsed.TotalSeconds,
                    ["schedule_authorizations"] = new JArray(session.Consent.ScheduleAuthorizations),
                    ["schedule_attest_current_plan"] = session.Consent.AttestCurrentPlan,
                    ["schedule_confirm_new_cycle"] = session.Consent.ConfirmNewCycle };
                if (binding["controller"] != null) reply["controller"] = binding["controller"]!.DeepClone();
                else if (binding["mumu"] != null) reply["mumu"] = binding["mumu"]!.DeepClone();
                else
                {
                    reply["input_display"] = binding["device"]!["input_display"];
                    reply["screenshot_display"] = binding["device"]!["screenshot_display"];
                }
                return reply;
            }
        }
        public void Dispose()
        {
            lock (exitGate)
            {
                if (disposed != 0) return;
                session.Revoked = true;
                process.Dispose();
                disposed = 1; // only after Job zero, original root Wait and dual EOF
            }
        }
    }
    private static readonly string[] ScheduleItems = {"arena_coins", "princess_arena_coins", "dungeon_skip", "mana_skip", "exp_skip", "normal_gacha_free", "guild_like", "house_items", "memorial_rewards"};

    private static (bool Day, bool Count, bool Schedule) ConsentRequirements(JObject selection, JObject config)
    {
        var modules = selection["selected_modules"]!.Values<string>().ToArray();
        bool schedule = modules.Contains("schedule_onekey");
        bool sweep = modules.Contains("event") || modules.Contains("abyss") || config["presets"]!.Any(p =>
            (string?)p["kind"] != "normal" && modules.Contains((string?)p["kind"]) && (int?)p["target_per_stage"] > 0);
        return (sweep || schedule, sweep, schedule);
    }

    private static PcrStartConsent ValidateConsent(JObject selection, JObject config, PcrStartConsent consent)
    {
        var needs = ConsentRequirements(selection, config);
        // GUI requests fresh counter/completion observations, never a human
        // assertion. Python derives the bookkeeping day with its game-day tool.
        bool currentState = consent.CountContext == "gui-current-state";
        if (currentState && ((int?)config["version"] is not (3 or 4) || consent.GameDay != ""))
            throw new InvalidOperationException("当前状态语义只适用于本机 GUI 配置，日期由内部工具生成");
        if (!currentState && (needs.Day && !DateOnly.TryParseExact(consent.GameDay, "yyyy-MM-dd", out _)
            || needs.Count && consent.CountContext != "user-reported-unreset"))
            throw new InvalidOperationException("本次游戏日及未重置次数声明无效");
        if (consent.ConfirmNewCycle && (!currentState || !needs.Schedule))
            throw new InvalidOperationException("日程恢复确认只适用于本次 GUI 日程启动。");
        var auth = consent.ScheduleAuthorizations;
        if (auth == null || (needs.Schedule
            ? (!currentState && !consent.AttestCurrentPlan) || auth.Length == 0 || auth.Distinct().Count() != auth.Length || auth.Any(n => !ScheduleItems.Contains(n))
            : consent.AttestCurrentPlan || auth.Length != 0))
            throw new InvalidOperationException(needs.Schedule ? "日程设置：请至少选择一个有效且不重复的本次项目；CLI 仍须当前计划声明。" : "未选择日程：不能附带日程许可或声明。");
        // An omitted reward-only date is derived and labelled by supported_daily.
        // Never turn an irrelevant saved/checked field into a user assertion.
        return consent with { GameDay = needs.Day ? consent.GameDay : "",
            CountContext = needs.Count || currentState && needs.Schedule ? consent.CountContext : "" };
    }

    private static async Task<PcrStartConsent?> ConfirmDialog(JObject summary)
    {
        var panel = new StackPanel { Spacing = 8, Margin = new Avalonia.Thickness(16) };
        string[] moduleIds = {"schedule_onekey", "event", "abyss", "deep", "rewards", "very_hard", "hard", "normal"};
        string[] moduleNames = {"日程一键", "活动困难", "深渊前哨扫荡", "深域", "每日任务与礼物", "高难", "困难", "普通（本次次数）"};
        var selected = summary["combination"]!["selected_modules"]!.Values<string>()
            .Select(id => moduleNames[Array.IndexOf(moduleIds, id)]).ToList();
        if ((bool?)summary["combination"]!["rewards_final"] == true) selected.Add("末尾补领");
        var identity = summary["controller"] is { } controller ? $"标准 ADB；包：{controller["app_package"]}"
            : summary["mumu"] is { } mumu
            ? $"MuMu 实例：{mumu["index"]}；包：{mumu["app_package"]}；克隆：{mumu["app_cloned_index"]}"
            : $"输入 display：{summary["device"]!["input_display"]}；截图 display：{summary["device"]!["screenshot_display"]}";
        panel.Children.Add(new TextBlock { Text = $"设备：{summary["connection"]!["serial"]}\nADB：{summary["connection"]!["adb_path"]}\n{identity}\n本次选集：{string.Join(" → ", selected)}\n允许处理当次授权的未完成项目，不改玩家设置、已完成跳过。", TextWrapping = Avalonia.Media.TextWrapping.Wrap });
        if (summary["configuration"] is { } configuration)
        {
            panel.Children.Add(new TextBlock { Text = $"本次配置：{summary["configuration_path"]}\n{configuration.ToString(Formatting.Indented)}",
                TextWrapping = Avalonia.Media.TextWrapping.Wrap });
            panel.Children.Add(new TextBlock { Text = "确认后将只读核对当前 PCR 显示映射，连接所选设备并抓取新鲜画面。核对一致后会运行本次选集并产生业务输入（含获准领取或扫荡）；映射变化、断连或未知结算即停止，不重发。显示映射和本次许可仅用于当前会话，不保存。",
                TextWrapping = Avalonia.Media.TextWrapping.Wrap });
        }
        var needs = ConsentRequirements((JObject)summary["combination"]!,
            (JObject)(summary["configuration"] ?? summary));
        bool schedule = needs.Schedule;
        string[] names = {"竞技场金币", "公主竞技场金币", "地下城跳过", "玛那探索", "经验探索", "普通免费扭蛋", "公会点赞", "公会之家道具", "追忆战场奖励"};
        var checks = names.Select(n => new CheckBox { Content = "本次授权：" + n, IsChecked = false }).ToArray();
        if (schedule) { foreach (var c in checks) panel.Children.Add(c); }
        var window = new Window { Title = "PCR Alpha — 仅授权本次开始", Width = 620, Height = 740, Content = new ScrollViewer { Content = panel } };
        var accept = new Button { Content = "确认当前配置与选集，请求本次 Start", HorizontalAlignment = HorizontalAlignment.Stretch };
        var missing = new TextBlock { TextWrapping = Avalonia.Media.TextWrapping.Wrap };
        panel.Children.Add(missing);
        accept.Click += (_, _) => {
            if (schedule && !checks.Any(c => c.IsChecked == true))
            { missing.Text = "日程设置：请至少选择一个本次项目；原生整组如有未选未完成项，将在一键前提示冲突。"; return; }
            window.Close(new PcrStartConsent("", needs.Day ? "gui-current-state" : "", schedule ? ScheduleItems.Where((_, i) => checks[i].IsChecked == true).ToArray() : Array.Empty<string>(), false));
        };
        panel.Children.Add(accept);
        return await window.ShowDialog<PcrStartConsent?>(Instances.RootView);
    }
}
