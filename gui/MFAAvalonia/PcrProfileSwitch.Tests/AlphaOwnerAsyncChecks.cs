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
using MFAAvalonia.ViewModels.UsersControls;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using Newtonsoft.Json.Linq;

internal static class AlphaOwnerAsyncChecks
{
    private const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.Static | BindingFlags.NonPublic;
    private const string Activity = """
        Display #0 (activities from top to bottom):
          topResumedActivity=ActivityRecord{fixture u0 com.bilibili.priconne/.MainActivity t1}
          Display: mDisplayId=0 (organized)
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
        var describe = owner.Assembly.GetType("MFAAvalonia.Pcr.PcrAdbDiscovery")!
            .GetMethod("Describe", BindingFlags.Public | BindingFlags.Static)!;
        var transport = owner.Assembly.GetType("MFAAvalonia.Pcr.PcrMuMuDisplay")!
            .GetMethod("RequireTransport", Hidden)!;
        var managerSource = owner.Assembly.GetType("MFAAvalonia.Pcr.PcrMuMuIdentity")!
            .GetMethod("HasManagerSource", Hidden)!;
        foreach (var serial in new[] { "emulator-5556", "emulator-1", "emulator-65535",
                     "127.0.0.1:5555", "127.0.0.1:1", "127.0.0.1:65535",
                     "192.168.1.4:5555", "192.168.0.1:1", "192.168.255.254:65535",
                     "10.0.0.1:1", "10.255.255.254:65535", "172.16.0.1:5555", "172.31.255.254:5555" })
        {
            Require(AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(adb, serial, out var normalizedPath, out var normalizedSerial)
                && normalizedPath == adb && normalizedSerial == serial, "adb_target_preserves_selected_address: " + serial);
            var raw = new AdbDeviceInfo("raw Finder fixture", adb, serial,
                AdbScreencapMethods.Default, AdbInputMethods.Maatouch, "{\"finder_only\":true}");
            var described = (AdbDeviceInfo)describe.Invoke(null, [raw])!;
            Require(described.Name.StartsWith("【ADB】") && described.AdbSerial == serial && described.AdbPath == adb
                && described.Config == "{}" && raw.Config == "{\"finder_only\":true}"
                && described.ScreencapMethods == AdbScreencapMethods.Encode && described.InputMethods == AdbInputMethods.AdbShell,
                "finder_preserves_target_and_clears_extras: " + serial);
            transport.Invoke(null, [new JObject { ["connection"] = new JObject { ["adb_path"] = adb, ["serial"] = serial },
                ["controller"] = new JObject { ["kind"] = "adb", ["app_package"] = "com.bilibili.priconne" } }]);
            Require(PcrAlphaPolicy.IsSupportedAdbTarget(described), "owner_transport_accepts_target: " + serial);
            var managerConnection = new JObject {
                ["adb_path"] = Path.Combine(fixture, "nx_device", "12.0", "shell", "adb.exe"), ["serial"] = serial };
            Require((bool)managerSource.Invoke(null, [managerConnection])! == serial.StartsWith("127.0.0.1:"),
                "lan_target_never_inherits_local_mumu_identity: " + serial);
        }
        var mumuFinder = Path.Combine(fixture, "MuMu", "nx_main", "adb.exe");
        var mumuShell = Path.Combine(fixture, "MuMu", "nx_device", "12.0", "shell", "adb.exe");
        foreach (var file in new[] { mumuFinder, mumuShell })
        {
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            File.WriteAllText(file, "OFFLINE FIXTURE - MUST NEVER EXECUTE");
        }
        var mumuRaw = new AdbDeviceInfo("MuMu LAN fixture", mumuFinder, "192.168.1.4:5555",
            AdbScreencapMethods.Default, AdbInputMethods.Maatouch, "{\"finder_only\":true}");
        var mumuMapped = (AdbDeviceInfo)describe.Invoke(null, [mumuRaw])!;
        Require(mumuMapped.Name.StartsWith("【MuMu】") && mumuMapped.AdbPath == mumuShell
            && mumuMapped.AdbSerial == mumuRaw.AdbSerial && mumuMapped.Config == "{}" && mumuRaw.AdbPath == mumuFinder,
            "mumu_finder_mapping_preserves_lan_endpoint_and_raw_source");
        foreach (var serial in new string?[] {
                     null, "", "remote.example:5555", "8.8.8.8:5555", "11.0.0.1:5555",
                     "172.15.255.254:5555", "172.32.0.1:5555", "192.169.1.4:5555",
                     "127.0.0.2:5555", "169.254.1.1:5555", "100.64.1.4:5555",
                     "0.0.0.0:5555", "224.0.0.1:5555", "255.255.255.255:5555",
                     "[::1]:5555", "[::ffff:192.168.1.4]:5555",
                     "192.168.1.256:5555", "192.168.001.4:5555", "192.168.1:5555",
                     "0xc0a80104:5555", "3232235780:5555",
                     "192.168.1.4:0", "192.168.1.4:65536", "192.168.1.4:05555",
                     "192.168.1.4:+5555", "192.168.1.4:5 555",
                     "192.168.1.4:5555:5556", "192.168.1.4:5555;calc", "192.168.1.4\n:5555",
                     "emulator-0", "emulator-65536", "emulator-05556" })
            Require(!AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(adb, serial, out _, out _),
                "non_lan_or_noncanonical_target_rejected: " + serial);
        foreach (var invalidPath in new[] { "adb.exe", @"\\server\share\adb.exe", Path.Combine(fixture, "missing", "adb.exe") })
            Require(!AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(invalidPath, "192.168.1.4:5555", out _, out _),
                "lan_target_still_requires_existing_local_adb: " + invalidPath);
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
            Set(processor, typeof(MaaProcessor), "<InstanceId>k__BackingField", "pcr-alpha-v1");
            var profileViews = new Dictionary<string, TaskQueueViewModel>((Dictionary<string, TaskQueueViewModel>)typeof(MaaProcessorManager).GetField("_viewModels", Hidden)!.GetValue(manager)!) {
                ["pcr-alpha-v1"] = vm, [PcrClanGuide.ProfileId] = vm };
            Set(manager, typeof(MaaProcessorManager), "_viewModels", profileViews);
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

            foreach (var serial in new[] { "127.0.0.1:16384", "192.168.1.4:5555" })
            foreach (var scenario in new[] { "stop_activity", "stop_input", "target_drift", "config_drift", "restart_after_cancel" })
            {
                config["connection"]!["serial"] = serial;
                original = config.ToString();
                selected = Target(serial);
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
                attempts.Add(new JObject { ["serial"] = serial, ["scenario"] = scenario, ["reads"] = reads, ["controller_allocations"] = allocations,
                    ["owner_cleared"] = true, ["idle"] = vm.Idle, ["log"] = Logs(processor) });
                Console.WriteLine("PCR alpha async scenario passed: " + serial + " " + scenario);
            }
            config["connection"]!["serial"] = "127.0.0.1:16384";
            selected = Target("127.0.0.1:16384");
            nodes["PCRAlphaSelectRewards"]!["attach"]!["selected"] = false;
            nodes["PCRAlphaSelectGuideTrial"] = new JObject { ["attach"] = new JObject { ["selected"] = true } };
            item.InterfaceItem!.Option![0].SelectedCases = ["guide_trial"];
            device.SetValue(vm, selected);
            var plans = JArray.Parse(File.ReadAllText(Path.Combine(args[1],"guide-opening","managed-plan-cases.json")));
            var rejectedTrials = 0;
            foreach (var plan in plans)
            {
                config["guide_trial"] = plan.DeepClone(); File.WriteAllText(path,config.ToString());
                var reached = false;
                PcrAlphaOwner.Confirm = _ => { reached = true; return Task.FromResult<PcrStartConsent?>(null); };
                request=PcrAlphaOwner.RequestStartAsync(vm);
                Until(()=>request.IsCompleted,"guide_owner_preflight"); request.GetAwaiter().GetResult();
                Require(!reached && vm.Idle && current.GetValue(null)==null && pending.GetValue(null)==null
                    && vm.PcrStartError.Contains("单关试打已从正式版本移除"),"removed_trial_rejected_before_confirmation: "+Logs(processor));
                Require((PcrAlphaOwner.ControllerCreations,PcrAlphaOwner.AgentCreations,PcrAlphaOwner.Grants)==initial && allocations==0,"guide_owner_never_allocates_device");
                rejectedTrials++;
            }
            try
            {
                var settings = (PcrPlayerSettings)RuntimeHelpers.GetUninitializedObject(typeof(PcrPlayerSettings));
                settings.ExportForStart(args[0], new JObject { ["selected_modules"] = new JArray("guide_trial") });
                throw new Exception("removed_trial_export_was_allowed");
            }
            catch (InvalidOperationException ex)
            {
                Require(ex.Message.Contains("单关试打已从正式版本移除"), "removed_trial_cannot_export_start_plan");
            }
            attempts.Add(new JObject{["scenario"]="removed_guide_trial_rejected_by_original_start_owner",["plans"]=rejectedTrials,["controller_allocations"]=0,["rejected_before_confirmation"]=true});
            Set(processor, typeof(MaaProcessor), "<InstanceId>k__BackingField", PcrClanGuide.ProfileId);
            config.Remove("guide_trial");
            var clanPlans = JArray.Parse(File.ReadAllText(Path.Combine(args[1], "clan-guide", "managed-plan-cases.json")));
            string? visibleClanHash = null;
            Set(vm, typeof(TaskQueueViewModel), "<PcrClanGuideConfirmation>k__BackingField", new Func<string?>(() => visibleClanHash));
            var clanSelections = 0;
            PcrAlphaOwner.SelectConfiguration = () => { clanSelections++; return Task.FromResult<string?>(path); };
            request = PcrAlphaOwner.RequestStartAsync(vm);
            Until(() => request.IsCompleted, "clan_unsaved_editor_refused"); request.GetAwaiter().GetResult();
            Require(clanSelections == 0 && vm.Idle && pending.GetValue(null) == null && current.GetValue(null) == null,
                "clan_unsaved_editor_refused_before_config_or_authority");
            config["clan_battle_simulation"] = clanPlans[0].DeepClone(); File.WriteAllText(path, config.ToString());
            visibleClanHash = new string('0', 64);
            request = PcrAlphaOwner.RequestStartAsync(vm);
            Until(() => request.IsCompleted, "clan_other_saved_plan_refused"); request.GetAwaiter().GetResult();
            Require(clanSelections == 1 && vm.Idle && pending.GetValue(null) == null && current.GetValue(null) == null,
                "clan_editor_confirmation_must_match_saved_plan");
            var clanForwarded = 0;
            foreach (var plan in clanPlans)
            {
                config["clan_battle_simulation"] = plan.DeepClone(); File.WriteAllText(path, config.ToString());
                visibleClanHash = (string?)plan["confirmed_for"];
                var reached = false;
                PcrAlphaOwner.Confirm = prompt => {
                    Require(JToken.DeepEquals(prompt["configuration"]?["clan_battle_simulation"], plan), "clan_owner_preserves_reviewed_simulation_plan");
                    Require(prompt["combination"]?["selected_modules"]?.ToString(Newtonsoft.Json.Formatting.None) == "[\"clan_battle_simulation\"]"
                        && (bool?)prompt["combination"]?["rewards_final"] == false, "clan_owner_keeps_single_simulation_without_rewards");
                    Require((string?)owner.GetProperty("ActiveProfile", Hidden)!.GetValue(null) == PcrClanGuide.ProfileId, "clan_owner_retains_source_profile_during_validation");
                    reached = true; return Task.FromResult<PcrStartConsent?>(null);
                };
                request = PcrAlphaOwner.RequestStartAsync(vm);
                Until(() => request.IsCompleted, "clan_owner_preflight"); request.GetAwaiter().GetResult();
                Require(reached && vm.Idle && current.GetValue(null) == null && pending.GetValue(null) == null,
                    "clan_owner_validates_then_cancels_without_authority: " + Logs(processor));
                Require((PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants) == initial && allocations == 0,
                    "clan_owner_never_allocates_device");
                clanForwarded++;
            }
            attempts.Add(new JObject { ["scenario"] = "clan_saved_plans_through_original_start_owner", ["plans"] = clanForwarded,
                ["controller_allocations"] = 0, ["cancelled_before_grant"] = true });
            config["clan_battle_simulation"] = clanPlans[0].DeepClone(); File.WriteAllText(path, config.ToString());
            visibleClanHash = (string?)clanPlans[0]["confirmed_for"];
            held = new(TaskCreationOptions.RunContinuationsAsynchronously);
            var clanReadEntered = false; var clanReadToken = default(CancellationToken);
            PcrAlphaOwner.Confirm = _ => Task.FromResult<PcrStartConsent?>(new("", "", [], false));
            PcrAlphaOwner.OfflineDisplayReader = (command, token) => {
                if (command == PcrDisplayRead.Activity) { clanReadEntered = true; clanReadToken = token; return held.Task; }
                return Task.FromResult(Input);
            };
            request = PcrAlphaOwner.RequestStartAsync(vm);
            Until(() => clanReadEntered || request.IsCompleted, "clan_owner_reaches_read");
            Require(clanReadEntered && !request.IsCompleted && !vm.CanEditPcrTarget, "clan_start_exposes_original_stop_before_native_allocation: " + Logs(processor));
            checkSelectors("clan_preparing");
            vm.ToggleCommand.Execute(null);
            Until(() => request.IsCompleted, "clan_real_stop_unwinds_owner"); request.GetAwaiter().GetResult();
            Require(clanReadToken.IsCancellationRequested && vm.Idle && current.GetValue(null) == null && pending.GetValue(null) == null
                && !PcrAlphaOwner.ClaimStart(processor), "clan_real_stop_cancels_original_owner_and_prevents_late_start");
            Require(!held.Task.IsCompleted && allocations == 0
                && (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants) == initial,
                "clan_real_stop_has_no_controller_agent_or_grant");
            held.SetResult(Activity);
            Dispatcher.UIThread.RunJobs();
            Require(current.GetValue(null) == null && !PcrAlphaOwner.ClaimStart(processor), "clan_late_read_cannot_rearm_owner");
            attempts.Add(new JObject { ["scenario"] = "clan_original_start_stop_before_allocation", ["controller_allocations"] = 0,
                ["owner_cleared"] = true, ["cancelled_before_grant"] = true, ["late_result_ignored"] = true });
            held = new(TaskCreationOptions.RunContinuationsAsynchronously); clanReadEntered = false;
            request = PcrAlphaOwner.RequestStartAsync(vm);
            Until(() => clanReadEntered || request.IsCompleted, "clan_editor_drift_reaches_read");
            Require(clanReadEntered && !request.IsCompleted, "clan_editor_drift_starts_at_read_boundary");
            visibleClanHash = null; held.SetResult(Activity);
            Until(() => request.IsCompleted, "clan_editor_drift_unwinds"); request.GetAwaiter().GetResult();
            Require(vm.Idle && pending.GetValue(null) == null && current.GetValue(null) == null && !PcrAlphaOwner.ClaimStart(processor)
                && allocations == 0 && (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants) == initial,
                "clan_unsaved_change_during_preparation_stops_before_allocation");
            attempts.Add(new JObject { ["scenario"] = "clan_editor_confirmation_guard", ["unsaved_rejected_before_configuration"] = true,
                ["different_saved_plan_rejected"] = true, ["preparation_change_rejected"] = true, ["controller_allocations"] = 0 });
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
