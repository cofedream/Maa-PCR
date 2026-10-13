using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Threading;
using Avalonia.Styling;
using MaaFramework.Binding;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Other;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using Newtonsoft.Json.Linq;

internal static class AlphaOwnerAsyncChecks
{
    private const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.Static | BindingFlags.NonPublic;
    private const string Activity = """
        Display #0 (activities from top to bottom):
          topResumedActivity=ActivityRecord{fixture u0 com.bilibili.priconne/.MainActivity t1}
          Display: mDisplayId=0 rootTasks=1
            mFocusedApp=ActivityRecord{fixture u0 com.bilibili.priconne/.MainActivity t1}
            mCurrentFocus=Window{fixture u0 com.bilibili.priconne/.MainActivity}
        """;
    private const string Input = """
          FocusedDisplayId: 0
          FocusedApplications:
            displayId=0, name='ActivityRecord{fixture u0 com.bilibili.priconne/.MainActivity t1}'
          FocusedWindows:
            displayId=0, name='fixture com.bilibili.priconne/.MainActivity'
          Viewport INTERNAL: displayId=0, uniqueId=local:0, isActive=[1]
        """;

    internal static void Run(MaaProcessor processor, TaskQueueViewModel vm, MaaProcessorManager manager,
        Action<string> checkSelectors, string[] args)
    {
        var count = 0;
        void Require(bool value, string check)
        {
            if (!value) throw new InvalidOperationException(check);
            count++;
        }
        var owner = typeof(PcrAlphaOwner);
        var current = owner.GetField("current", Hidden)!;
        var pending = owner.GetField("pendingStart", Hidden)!;
        var prompting = owner.GetField("prompting", Hidden)!;
        Require(current.GetValue(null) == null && pending.GetValue(null) == null
            && (int)prompting.GetValue(null)! == 0, "async_fixture_requires_idle_owner");
        var restore = new List<Action>();
        void Set(object? target, Type type, string name, object? value)
        {
            var field = type.GetField(name, Hidden)!;
            var previous = field.GetValue(target);
            restore.Add(() => field.SetValue(target, previous));
            field.SetValue(target, value);
        }
        void NewField(object target, string name)
        {
            var type = target.GetType();
            Set(target, type, name, Activator.CreateInstance(type.GetField(name, Hidden)!.FieldType, nonPublic: true));
        }
        foreach (var name in new[] { "Confirm", "SelectConfiguration", "ControllerBuilder", "OfflineMuMuFactory",
                     "OfflineAgentScript", "OfflineDisplayReader", "OfflineManagerReader" })
        {
            var property = owner.GetProperty(name)!;
            var previous = property.GetValue(null);
            restore.Add(() => property.SetValue(null, previous));
        }
        var initial = (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants);
        var commandThread = typeof(MaaProcessor).GetField("_commandThread", Hidden)!;
        Require(commandThread.GetValue(processor) == null, "async_fixture_has_no_processor_job_thread");
        var attempts = new JArray();
        var fixture = PrepareFixture(args);
        var path = Path.Combine(fixture, "daily-config.json");
        var adb = Path.Combine(fixture, "adb.exe");
        File.WriteAllText(adb, "OFFLINE FIXTURE - MUST NEVER EXECUTE");
        var config = new JObject {
            ["version"] = 4, ["start"] = "home", ["count_context"] = null,
            ["connection"] = new JObject { ["adb_path"] = adb, ["serial"] = "127.0.0.1:16384" },
            ["controller"] = new JObject { ["kind"] = "adb", ["app_package"] = "com.bilibili.priconne" },
            ["presets"] = new JArray(new[] { "deep", "very_hard", "hard" }.Select(kind => new JObject {
                ["kind"] = kind, ["slot"] = null, ["label"] = kind, ["target_per_stage"] = 0 })) };
        var original = config.ToString();
        AdbDeviceInfo Target(string serial) => new("isolated fixture", adb, serial,
            AdbScreencapMethods.Encode, AdbInputMethods.AdbShell, "{}");
        var selected = Target("127.0.0.1:16384");
        var alternate = Target("127.0.0.1:16385");
        var device = typeof(TaskQueueViewModel).GetField("_currentDevice", Hidden)!;
        var source = new CancellationTokenSource();
        Task? request = null;
        TaskCompletionSource<string>? held = null;
        try
        {
            var context = SynchronizationContext.Current;
            restore.Add(() => SynchronizationContext.SetSynchronizationContext(context));
            SynchronizationContext.SetSynchronizationContext(new AvaloniaSynchronizationContext(Dispatcher.UIThread, DispatcherPriority.Normal));
            var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", Hidden)!.GetValue(null)!;
            var guiType = typeof(GuiSettingsUserControlModel);
            var hadGui = cache.TryGetValue(guiType, out var previousGui);
            restore.Add(() => { if (hadGui) cache[guiType] = previousGui!; else cache.TryRemove(guiType, out _); });
            var gui = (GuiSettingsUserControlModel)RuntimeHelpers.GetUninitializedObject(guiType);
            guiType.GetField("_baseTheme", Hidden)!.SetValue(gui, ThemeVariant.Light);
            cache[guiType] = new Lazy<object>(() => gui);
            Set(null, owner, "repository", fixture);
            Set(null, owner, "configPath", path);
            foreach (var name in new[] { "_agentContexts", "_screenshotTaskerInitLock", "_liveViewScreencapJobLock",
                         "_detachedScreenshotTaskersLock", "_detachedScreenshotTaskers", "<LogItemViewModels>k__BackingField" })
                NewField(processor, name);
            NewField(manager, "_instanceNames");
            NewField(vm, "_pcrDiscovery");
            NewField(vm, "_currentResources");
            // No processor job exists during mapping. Stop still synchronously revokes
            // the real Owner; its RequestStartAsync must perform all session cleanup.
            source.Cancel();
            Set(processor, typeof(MaaProcessor), "_commandThreadCts", source);
            Set(vm, typeof(TaskQueueViewModel), "_currentDevice", selected);
            var nodes = new Dictionary<string, JToken>();
            nodes["PCRAlphaEntry"] = new JObject { ["custom_action_param"] = new JObject { ["selection_mode"] = "pi_nodes_v1" } };
            foreach (var name in new[] { "Schedule", "Event", "Abyss", "Deep", "Rewards", "VeryHard", "Hard", "Normal", "RewardsFinal" })
                nodes["PCRAlphaSelect" + name] = new JObject { ["attach"] = new JObject { ["selected"] = name == "Rewards" } };
            var item = (DragItemViewModel)RuntimeHelpers.GetUninitializedObject(typeof(DragItemViewModel));
            typeof(DragItemViewModel).GetField("_interfaceItem", Hidden)!.SetValue(item, new MaaInterface.MaaInterfaceTask {
                Name = "PCRAlpha", Entry = "PCRAlphaEntry", PipelineOverride = nodes,
                Option = [new MaaInterface.MaaInterfaceSelectOption { Name = "AlphaModules", SelectedCases = ["rewards"] }] });
            Set(vm, typeof(TaskQueueViewModel), "_taskItemViewModels", new ObservableCollection<DragItemViewModel> { item });
            PcrAlphaOwner.ControllerBuilder = null;
            var allocations = 0;
            PcrAlphaOwner.OfflineMuMuFactory = (_, _, _, _, _) => {
                allocations++;
                throw new InvalidOperationException("async_fixture_must_never_allocate_controller");
            };
            PcrAlphaOwner.OfflineAgentScript = "offline-never-start-agent";
            PcrAlphaOwner.OfflineManagerReader = (_, _, _) => throw new InvalidOperationException("unexpected_manager_read");
            PcrAlphaOwner.SelectConfiguration = () => Task.FromResult<string?>(path);
            PcrAlphaOwner.Confirm = _ => Task.FromResult<PcrStartConsent?>(new("", "", [], false));

            foreach (var scenario in new[] { "stop_activity", "stop_input", "target_drift", "config_drift", "restart_after_cancel" })
            {
                File.WriteAllText(path, original);
                device.SetValue(vm, selected);
                processor.LogItemViewModels.Clear();
                held = new(TaskCreationOptions.RunContinuationsAsynchronously);
                var entered = false;
                var readToken = default(CancellationToken);
                var reads = 0;
                var pauseAt = scenario == "stop_input" ? PcrDisplayRead.Input : PcrDisplayRead.Activity;
                PcrAlphaOwner.OfflineDisplayReader = (command, token) => {
                    reads++;
                    if (command == pauseAt) { readToken = token; entered = true; return held.Task; }
                    return Task.FromResult(command == PcrDisplayRead.Activity ? Activity : Input);
                };
                request = PcrAlphaOwner.RequestStartAsync(vm);
                Until(() => entered || request.IsCompleted, scenario + "_reach_read");
                Require(entered && !request.IsCompleted, scenario + "_reaches_real_mapping: " + Logs(processor));
                Require(vm.IsRunning && !vm.CanEditPcrTarget && current.GetValue(null) != null
                    && pending.GetValue(null) != null && (int)prompting.GetValue(null)! == 1, scenario + "_has_pending_owner_and_stop");
                var duplicate = PcrAlphaOwner.RequestStartAsync(vm);
                Require(duplicate.IsCompleted && reads == (pauseAt == PcrDisplayRead.Activity ? 1 : 2), scenario + "_duplicate_start_ignored");
                vm.CurrentDevice = alternate;
                Require(ReferenceEquals(vm.CurrentDevice, selected) && !vm.SelectManualPcrAdbTarget(alternate), scenario + "_normal_device_edits_refused");
                checkSelectors(scenario);
                Require(File.ReadAllText(path) == original, scenario + "_rejected_ui_edits_preserve_config");
                if (scenario == "target_drift") device.SetValue(vm, alternate);
                else if (scenario == "config_drift")
                {
                    var changed = (JObject)config.DeepClone();
                    changed["collect_gift_stamina"] = true;
                    File.WriteAllText(path, changed.ToString());
                }
                else
                {
                    vm.ToggleCommand.Execute(null);
                    Require(readToken.IsCancellationRequested, scenario + "_real_toggle_cancels_read_token");
                    Until(() => request.IsCompleted, scenario + "_stop_unwinds_without_read_result");
                    Require(!held.Task.IsCompleted, scenario + "_reader_result_still_pending");
                }
                held.SetResult(pauseAt == PcrDisplayRead.Activity ? Activity : Input);
                Until(() => request.IsCompleted, scenario + "_request_finishes");
                request.GetAwaiter().GetResult();
                Dispatcher.UIThread.RunJobs();
                Require(current.GetValue(null) == null && pending.GetValue(null) == null
                    && (int)prompting.GetValue(null)! == 0 && vm.Idle && !PcrAlphaOwner.ClaimStart(processor), scenario + "_cleanup_idle_no_reusable_authority");
                Require((PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants) == initial
                    && allocations == 0 && processor.MaaTasker == null, scenario + "_zero_controller_agent_grant");
                Require(commandThread.GetValue(processor) == null, scenario + "_never_creates_processor_job_thread");
                if (scenario == "target_drift") Require(Logs(processor).Contains("GUI 目标与当前 v3 配置不一致"), "target_drift_rechecked_after_mapping");
                else if (scenario == "config_drift") Require(Logs(processor).Contains("Configuration changed during mapping"), "config_drift_rechecked_after_mapping");
                else Require(reads == (pauseAt == PcrDisplayRead.Activity ? 1 : 2), scenario + "_late_result_does_not_resume_reads");
                attempts.Add(new JObject { ["scenario"] = scenario, ["reads"] = reads, ["controller_allocations"] = allocations,
                    ["owner_cleared"] = true, ["idle"] = vm.Idle, ["log"] = Logs(processor) });
                Console.WriteLine("PCR alpha async scenario passed: " + scenario);
            }
            File.WriteAllText(Path.Combine(fixture, "async-results.json"), attempts.ToString());
            Console.WriteLine($"PCR alpha async checks passed: {count}; controller/agent/grant starts=0; fixture={fixture}");
        }
        finally
        {
            if (request is { IsCompleted: false })
            {
                PcrAlphaOwner.Revoke(processor);
                held?.TrySetResult(Activity);
                Until(() => request.IsCompleted, "fixture_cleanup");
            }
            processor.LogItemViewModels?.Clear();
            foreach (var undo in restore.AsEnumerable().Reverse()) undo();
            source.Dispose();
        }
    }

    private static string Logs(MaaProcessor processor) => string.Join("\n", processor.LogItemViewModels.Select(log => log.Content));

    private static void Until(Func<bool> done, string phase)
    {
        var watch = Stopwatch.StartNew();
        while (!done())
        {
            if (watch.Elapsed > TimeSpan.FromSeconds(20)) throw new TimeoutException(phase);
            Dispatcher.UIThread.RunJobs();
            Thread.Sleep(5);
        }
    }

    private static string PrepareFixture(string[] args)
    {
        var source = Path.GetFullPath(args[0]);
        var fixture = Path.Combine(Path.GetFullPath(args[1]), Guid.NewGuid().ToString("N"));
        var info = new ProcessStartInfo(Path.GetFullPath(args[2])) {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true,
            RedirectStandardError = true, WorkingDirectory = source };
        foreach (var value in new[] { "-X", "utf8", "-B", "-c", """
            from pathlib import Path
            import shutil, sys, venv
            source, fixture = map(Path, sys.argv[1:])
            (fixture / 'scripts').mkdir(parents=True)
            shutil.copy2(source / 'scripts/validate-alpha-config.py', fixture / 'scripts')
            shutil.copytree(source / 'src/pcr_maa', fixture / 'src/pcr_maa', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            venv.create(fixture / '.venv', with_pip=False, system_site_packages=True)
            """, source, fixture }) info.ArgumentList.Add(value);
        using var process = Process.Start(info) ?? throw new InvalidOperationException("Cannot create isolated Python fixture");
        var stdout = process.StandardOutput.ReadToEndAsync();
        var stderr = process.StandardError.ReadToEndAsync();
        if (!process.WaitForExit(60000)) { process.Kill(true); process.WaitForExit(); throw new TimeoutException("Isolated Python fixture setup"); }
        if (process.ExitCode != 0) throw new InvalidOperationException("Isolated Python fixture setup: " + stderr.GetAwaiter().GetResult() + stdout.GetAwaiter().GetResult());
        return fixture;
    }
}
