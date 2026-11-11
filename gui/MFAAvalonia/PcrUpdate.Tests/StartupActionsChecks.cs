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
using Avalonia;
using Avalonia.Controls;
using Avalonia.LogicalTree;
using Avalonia.Media.Imaging;
using Avalonia.Media;
using Avalonia.Styling;
using Avalonia.Threading;
using MaaFramework.Binding;
using MFAAvalonia.Configuration;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Other;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using MFAAvalonia.ViewModels.Windows;
using MFAAvalonia.Views.Windows;
using MFAAvalonia.Views.UserControls.Settings;
using Newtonsoft.Json.Linq;

internal static class StartupActionsChecks
{
    private static bool IsDotnetHost => string.Equals(Path.GetFileNameWithoutExtension(Environment.ProcessPath), "dotnet", StringComparison.OrdinalIgnoreCase);
    private static string AssemblyArgument => IsDotnetHost ? $"\"{typeof(StartupActionsChecks).Assembly.Location}\" " : "";
    private const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.Static | BindingFlags.NonPublic;
    private static readonly Type Startup = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrStartupActions", true)!;
    private static readonly Type Gate = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrProfileRunGate", true)!;
    private static int checks, ownerStarts;

    private static void Require(bool value, string name)
    {
        if (!value) throw new InvalidOperationException("startup_actions:" + name);
        checks++;
    }
    private static void Set(object? target, string name, object? value, Type? type = null) =>
        (type ?? target!.GetType()).GetField(name, Hidden)!.SetValue(target, value);
    private static void New(object target, string name) => Set(target, name,
        Activator.CreateInstance(target.GetType().GetField(name, Hidden)!.FieldType, nonPublic: true));
    private static void Complete(Task task)
    {
        var timer = Stopwatch.StartNew();
        while (!task.IsCompleted && timer.Elapsed < TimeSpan.FromSeconds(15))
        {
            Dispatcher.UIThread.RunJobs();
            Thread.Sleep(2);
        }
        Require(task.IsCompleted, "bounded_task_completion");
        task.GetAwaiter().GetResult();
        Dispatcher.UIThread.RunJobs();
    }
    private static Task Start(TaskQueueViewModel vm, Func<CancellationToken, Task>? launch = null) =>
        (Task)Startup.GetMethod("RunAsync", Hidden, null, launch == null
            ? [typeof(TaskQueueViewModel)] : [typeof(TaskQueueViewModel), typeof(Func<CancellationToken, Task>)], null)!
            .Invoke(null, launch == null ? [vm] : [vm, launch])!;

    internal static void Run(string directory, bool restartChild = false, string? repositoryRoot = null)
    {
        // The labyrinth settings page loads data catalogs from the checkout, exactly like
        // the PcrLabyrinth test projects. Without the source root it can only report a
        // missing Boss catalog, which would mask the start-validation path under test.
        if (repositoryRoot is not null)
        {
            PcrLabyrinthBossCatalog.UseRepositoryRoot(repositoryRoot);
            PcrLabyrinthCharacterCatalog.UseRepositoryRoot(repositoryRoot);
            PcrLabyrinthEventCatalog.UseRepositoryRoot(repositoryRoot);
        }
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        AppBuilder.Configure<Application>().UseWin32().UseSkia().SetupWithoutStarting();
        Application.Current!.RequestedThemeVariant = ThemeVariant.Light;
        Application.Current!.Styles.Add(new Avalonia.Themes.Fluent.FluentTheme());
        Application.Current.Styles.Add(new FluentAvalonia.Styling.FluentAvaloniaTheme());
        Application.Current.Styles.Add(new SukiUI.SukiTheme());
        SynchronizationContext.SetSynchronizationContext(new AvaloniaSynchronizationContext(Dispatcher.UIThread, DispatcherPriority.Normal));
        var root = Path.Combine(directory, "startup-actions");
        Directory.CreateDirectory(Path.Combine(root, "config", "instances"));
        Set(null, "_configDirectory", Path.Combine(root, "config"), typeof(AppPaths));
        Set(null, "_initialized", true, typeof(AppPaths));
        Set(null, "<DataRoot>k__BackingField", root, typeof(AppPaths));
        ConfigurationManager.Current = new MFAConfiguration("Default", "config", new Dictionary<string, object>
            { [PcrSoftwareUpdateService.CheckSetting] = false });
        var manager = (MaaProcessorManager)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessorManager));
        var lazy = (Lazy<MaaProcessorManager>)typeof(MaaProcessorManager).GetField("LazyInstance", Hidden)!.GetValue(null)!;
        Set(lazy, "_factory", new Func<MaaProcessorManager>(() => manager));
        _ = MaaProcessorManager.Instance;
        New(manager, "_lock");
        New(manager, "_instanceNames");
        var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", Hidden)!.GetValue(null)!;
        foreach (var type in new[] { typeof(RootViewModel), typeof(VersionUpdateSettingsUserControlModel), typeof(InstanceTabBarViewModel), typeof(GuiSettingsUserControlModel) })
            cache[type] = new Lazy<object>(() => RuntimeHelpers.GetUninitializedObject(type));
        Set(Instances.GuiSettingsUserControlModel, "_baseTheme", ThemeVariant.Light);
        LanguageHelper.Initialize();
        PcrAlphaOwner.Install(root);
        PcrAlphaOwner.SelectConfiguration = () => { ownerStarts++; return Task.FromResult<string?>(null); };
        var vm = Owner(manager, "pcr-alpha-v1");
        var config = vm.Processor.InstanceConfiguration;
        if (restartChild)
        {
            RootStartsOnce(vm);
            Require(ownerStarts == 1, "restarted_root_reaches_fresh_owner_once");
            Require(config.GetValue(ConfigurationKeys.BeforeTask, "None") == "StartupSoftwareAndScript"
                && config.GetValue(ConfigurationKeys.AfterTask, "None") == "CloseEmulatorAndMFA", "restart_keeps_both_actions");
            Require(Effects(vm.Processor).SequenceEqual(["emulator", "application"]), "restarted_completion_dispatches_both_effects");
            CheckSettingsView(root);
            Console.WriteLine("RESTART_STARTUP_AND_COMPLETION_ACTIONS=passed");
            return;
        }
        foreach (var action in new[] { "None", "StartupSoftware", "StartupSoftwareAndScript", "StartupScriptOnly" })
        {
            config.SetValue(ConfigurationKeys.BeforeTask, action);
            var launches = 0;
            var before = ownerStarts;
            Complete(Start(vm, _ => { launches++; return Task.CompletedTask; }));
            Require(launches == (action is "StartupSoftware" or "StartupSoftwareAndScript" ? 1 : 0), "launch_selection:" + action);
            Require(ownerStarts - before == (action is "StartupSoftwareAndScript" or "StartupScriptOnly" ? 1 : 0), "owner_selection:" + action + ":" + vm.PcrStartError);
        }
        config.SetValue(ConfigurationKeys.BeforeTask, "StartupSoftwareAndScript");
        var selected = ownerStarts;
        Complete(Start(vm, _ => throw new IOException("synthetic launch failure")));
        Require(ownerStarts == selected && vm.PcrStartError.Contains("synthetic launch failure"), "failed_launch_does_not_start_script");
        var entered = false;
        var stopped = 0;
        var pending = Start(vm, token => { entered = true; return Task.Delay(Timeout.Infinite, token); });
        Require(entered && vm.IsRunning && !vm.CanEditPcrTarget, "launch_wait_is_stoppable_and_locks_target");
        Complete(Start(vm, _ => throw new Exception("duplicate launch")));
        vm.StopTask(() => stopped++);
        Complete(pending);
        Require(ownerStarts == selected && !vm.IsRunning && stopped == 1 && vm.CanEditPcrTarget, "stop_never_hands_off_and_runs_callback");
        Set(null, "UpdatePending", true, Gate);
        try { Complete(Start(vm, _ => throw new Exception("launch during update"))); }
        finally { Set(null, "UpdatePending", false, Gate); }
        Require(ownerStarts == selected, "update_blocks_startup");
        foreach (var action in new[] { "None", "StartupSoftware", "StartupSoftwareAndScript", "StartupScriptOnly" })
        {
            config.SetValue(ConfigurationKeys.BeforeTask, action);
            var savedProfile = File.ReadAllBytes(config.GetConfigFilePath());
            GlobalConfiguration.SetValue("UpdateRestartKeep", "unchanged");
            GlobalConfiguration.SetValue(ConfigurationKeys.NoAutoStart, bool.TrueString);
            Complete(Start(vm, _ => throw new Exception("suppressed launch")));
            Require(ownerStarts == selected && GlobalConfiguration.GetValue(ConfigurationKeys.NoAutoStart, "") == bool.FalseString,
                "restart_suppression_is_consumed_once:" + action);
            Require(File.ReadAllBytes(config.GetConfigFilePath()).SequenceEqual(savedProfile)
                && GlobalConfiguration.GetValue("UpdateRestartKeep", "") == "unchanged", "restart_keeps_saved_actions:" + action);
            var launches = 0;
            Complete(Start(vm, _ => { launches++; return Task.CompletedTask; }));
            Require(launches == (action is "StartupSoftware" or "StartupSoftwareAndScript" ? 1 : 0)
                && ownerStarts - selected == (action is "StartupSoftwareAndScript" or "StartupScriptOnly" ? 1 : 0),
                "next_normal_start_keeps_saved_action:" + action);
            selected = ownerStarts;
        }
        CheckLegacyUpdateRestart(vm, root);
        selected = ownerStarts;
        config.SetValue(ConfigurationKeys.BeforeTask, "invalid-action");
        Complete(Start(vm));
        Require(vm.PcrStartError.Contains("启动后操作无效"), "invalid_saved_action_is_rejected");
        config.SetValue(ConfigurationKeys.BeforeTask, "StartupSoftwareAndScript");
        foreach (var path in new[] { "", Path.Combine(root, "missing-game.exe") })
        {
            config.SetValue(ConfigurationKeys.SoftwarePath, path);
            Complete(Start(vm));
            Require(ownerStarts == selected && vm.PcrStartError.Contains("游戏路径"), "invalid_game_path_never_starts_script");
        }
        CheckAfterActions(vm.Processor);
        CheckShortcut(vm, root);
        var labyrinth = Owner(manager, "pcr-labyrinth-v1");
        labyrinth.Processor.InstanceConfiguration.SetValue(ConfigurationKeys.BeforeTask, "StartupScriptOnly");
        Complete(Start(labyrinth));
        Require(labyrinth.PcrStartError.Contains("游戏玩家 ID") && ownerStarts == selected, "labyrinth_routes_to_its_own_validation");
        CheckLabyrinthCompletion(labyrinth, root);
        vm = Owner(manager, "pcr-alpha-v1");
        config = vm.Processor.InstanceConfiguration;
        var marker = Path.Combine(root, "launches.txt");
        config.SetValue(ConfigurationKeys.BeforeTask, "StartupSoftwareAndScript");
        config.SetValue(ConfigurationKeys.AfterTask, "CloseEmulatorAndMFA");
        config.SetValue(ConfigurationKeys.SoftwarePath, Environment.ProcessPath!);
        config.SetValue(ConfigurationKeys.EmulatorConfig, $"{AssemblyArgument}\"{marker}\" --startup-launch-marker");
        config.SetValue(ConfigurationKeys.WaitSoftwareTime, 0.0);
        for (var restart = 0; restart < 2; restart++)
        {
            var info = new ProcessStartInfo(Environment.ProcessPath!) { UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = root };
            if (IsDotnetHost) info.ArgumentList.Add(typeof(StartupActionsChecks).Assembly.Location);
            info.ArgumentList.Add(directory);
            info.ArgumentList.Add("--startup-restart-fixture");
            if (repositoryRoot is not null) info.ArgumentList.Add(repositoryRoot);
            using var child = Process.Start(info)!;
            var output = child.StandardOutput.ReadToEndAsync();
            var errors = child.StandardError.ReadToEndAsync();
            if (!child.WaitForExit(30000)) { child.Kill(true); throw new TimeoutException("startup_restart_child_timeout"); }
            File.WriteAllText(Path.Combine(root, $"restart-{restart}.log"), output.GetAwaiter().GetResult() + errors.GetAwaiter().GetResult());
            Require(child.ExitCode == 0 && output.Result.Contains("RESTART_STARTUP_AND_COMPLETION_ACTIONS=passed"), "separate_restart_process:" + restart);
            var timer = Stopwatch.StartNew();
            while ((!File.Exists(marker) || File.ReadAllLines(marker).Length < restart + 1) && timer.ElapsedMilliseconds < 5000) Thread.Sleep(10);
            Require(File.Exists(marker) && File.ReadAllLines(marker).Length == restart + 1, "real_software_launch_once_per_process:" + restart);
        }
        Require(PcrAlphaOwner.ControllerCreations == 0 && PcrAlphaOwner.AgentCreations == 0 && PcrAlphaOwner.Grants == 0,
            "no_real_controller_agent_or_grant");
        PlayerLogChecks.Run(directory, id => Owner(manager, id));
        CheckUnreadableGlobalRestart(Owner(manager, "pcr-alpha-v1"));
        CheckUpdateProxySettings(root);
        Console.WriteLine($"PCR startup/completion actions checks passed: {checks}; two fresh processes, real harmless launch marker; game inputs=0; OS shutdowns=0");
    }

    private static void CheckUnreadableGlobalRestart(TaskQueueViewModel vm)
    {
        vm.Processor.InstanceConfiguration.SetValue(ConfigurationKeys.BeforeTask, "StartupScriptOnly");
        var before = ownerStarts;
        Complete(Start(vm));
        Require(ownerStarts == ++before, "consumed_receipt_allows_start_before_global_read_failure");
        GlobalConfiguration.SetValue(ConfigurationKeys.NoAutoStart, bool.TrueString);
        var saved = File.ReadAllBytes(GlobalConfiguration.ConfigPath);
        using (var locked = new FileStream(GlobalConfiguration.ConfigPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            Complete(Start(vm));
        Require(ownerStarts == before && GlobalConfiguration.HasFileAccessError,
            "unreadable_noautostart_never_falls_back_to_autostart");
        Require(File.ReadAllBytes(GlobalConfiguration.ConfigPath).SequenceEqual(saved), "unreadable_noautostart_keeps_global_settings");
    }

    private static void CheckLegacyUpdateRestart(TaskQueueViewModel vm, string root)
    {
        var config = vm.Processor.InstanceConfiguration;
        config.SetValue(ConfigurationKeys.BeforeTask, "StartupScriptOnly");
        var savedProfile = File.ReadAllBytes(config.GetConfigFilePath());
        var manifest = Path.Combine(root, "package-manifest.json");
        File.WriteAllText(manifest, "{\"update\":{\"revision\":74,\"version\":\"synthetic-fixed\"}}");
        var digest = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(manifest))).ToLowerInvariant();
        var maintenance = Path.Combine(root, ".runtime", "player-maintenance");
        var work = Path.Combine(maintenance, "update-" + new string('a', 32));
        var receipt = Path.Combine(work, "verification.json");
        var consumed = Path.Combine(maintenance, "startup-consumed.json");
        var before = ownerStarts;
        Complete(Start(vm));
        Require(ownerStarts == ++before, "new_install_without_receipt_keeps_autostart");
        Directory.CreateDirectory(work);
        var value = new JObject
        {
            ["status"] = "rolled_back", ["version"] = "synthetic-fixed", ["revision"] = 74,
            ["manifest_sha256"] = digest, ["changed_payload_count"] = 1, ["removed_program_files"] = 0,
            ["archive_kind"] = "delta", ["base_revision"] = 73, ["backup_path"] = Path.Combine(work, "before"),
            ["private_state_preserved"] = true, ["gui_started"] = false
        };
        File.WriteAllText(receipt, value.ToString());
        Complete(Start(vm));
        Require(ownerStarts == ++before, "unsuccessful_receipt_does_not_suppress");
        value["status"] = "updated";
        value["manifest_sha256"] = new string('b', 64);
        File.WriteAllText(receipt, value.ToString());
        Complete(Start(vm));
        Require(ownerStarts == ++before, "other_manifest_receipt_does_not_suppress");
        value["manifest_sha256"] = digest;
        File.WriteAllText(receipt, value.ToString());
        Complete(Start(vm));
        Require(ownerStarts == before && File.Exists(consumed), "r73_worker_receipt_suppresses_first_fixed_gui_start");
        Complete(Start(vm));
        Require(ownerStarts == ++before, "consumed_receipt_allows_next_normal_start");
        var repeatedWork = Path.Combine(maintenance, "update-" + new string('d', 32));
        Directory.CreateDirectory(repeatedWork);
        File.WriteAllText(Path.Combine(repeatedWork, "verification.json"), value.ToString());
        Complete(Start(vm));
        Require(ownerStarts == before, "same_manifest_new_update_receipt_suppresses_again");
        Complete(Start(vm));
        Require(ownerStarts == ++before, "all_matching_receipts_consumed_together");
        File.Delete(consumed);
        GlobalConfiguration.SetValue(ConfigurationKeys.NoAutoStart, bool.TrueString);
        Complete(Start(vm));
        Require(ownerStarts == before && GlobalConfiguration.GetValue(ConfigurationKeys.NoAutoStart, "") == bool.FalseString,
            "receipt_and_noautostart_consumed_together");
        Complete(Start(vm));
        Require(ownerStarts == ++before, "combined_markers_do_not_suppress_twice");
        File.Delete(consumed);
        using (var locked = new FileStream(receipt, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            Complete(Start(vm));
        Require(ownerStarts == before && !File.Exists(consumed), "unreadable_update_receipt_never_starts_tasks");
        File.WriteAllText(consumed, new JObject { ["manifest_sha256"] = new string('c', 64) }.ToString());
        using (var locked = new FileStream(consumed, FileMode.Open, FileAccess.Read, FileShare.Read))
            Complete(Start(vm));
        Require(ownerStarts == before, "failed_consumption_write_never_starts_tasks");
        File.Delete(consumed);
        Complete(Start(vm));
        Require(ownerStarts == before && File.Exists(consumed), "repaired_receipt_consumption_remains_one_time");
        Complete(Start(vm));
        Require(ownerStarts == ++before && File.ReadAllBytes(config.GetConfigFilePath()).SequenceEqual(savedProfile),
            "legacy_update_suppression_keeps_profile_and_recovers_autostart");
    }

    private static void CheckShortcut(TaskQueueViewModel vm, string root)
    {
        var work = Path.Combine(root, "中文 启动目录\U0001F600"); Directory.CreateDirectory(work);
        var shortcutPath = Path.Combine(root, "带 参数\U0001F600.lnk");
        var marker = Path.Combine(work, "shortcut-marker.txt");
        var arguments = $"{AssemblyArgument}\"{marker}\" --startup-launch-marker";
        var link = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046"))!)!;
        try
        {
            var contract = typeof(MaaProcessor).GetNestedType("IShellLinkW", BindingFlags.NonPublic)!;
            contract.GetMethod("SetPath")!.Invoke(link, [Environment.ProcessPath!]);
            contract.GetMethod("SetArguments")!.Invoke(link, [arguments]);
            contract.GetMethod("SetWorkingDirectory")!.Invoke(link, [work]);
            ((System.Runtime.InteropServices.ComTypes.IPersistFile)link).Save(shortcutPath, true);
        }
        finally { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(link); }
        var resolved = typeof(MaaProcessor).GetMethod("ResolveShortcut", Hidden)!.Invoke(null, [shortcutPath]);
        Require(resolved is ValueTuple<string,string,string> target
            && string.Equals(target.Item1, Environment.ProcessPath, StringComparison.OrdinalIgnoreCase)
            && target.Item2 == arguments && target.Item3 == work, "unicode_shortcut_target_arguments_working_directory");
        var config = vm.Processor.InstanceConfiguration;
        config.SetValue(ConfigurationKeys.SoftwarePath, shortcutPath);
        config.SetValue(ConfigurationKeys.EmulatorConfig, "");
        config.SetValue(ConfigurationKeys.WaitSoftwareTime, 0.0);
        Complete(vm.Processor.StartSoftware());
        var timer = Stopwatch.StartNew();
        while (!File.Exists(marker) && timer.ElapsedMilliseconds < 5000) Thread.Sleep(10);
        Require(File.Exists(marker), "shortcut_keeps_its_arguments");
        var child = (Process)typeof(MaaProcessor).GetField("_softwareProcess", Hidden)!.GetValue(null)!;
        Require(child.WaitForExit(5000) && child.ExitCode == 0, "shortcut_helper_exits_cleanly");
        var explicitMarker = Path.Combine(work, "explicit.txt");
        config.SetValue(ConfigurationKeys.EmulatorConfig, $"{AssemblyArgument}\"{explicitMarker}\" --startup-launch-marker");
        Complete(vm.Processor.StartSoftware());
        child = (Process)typeof(MaaProcessor).GetField("_softwareProcess", Hidden)!.GetValue(null)!;
        Require(child.WaitForExit(5000) && child.ExitCode == 0 && File.Exists(explicitMarker)
            && File.ReadAllLines(marker).Length == 1, "explicit_arguments_override_shortcut");
        using var cancelled = new CancellationTokenSource(); cancelled.Cancel();
        try { Complete(vm.Processor.StartSoftware(cancelled.Token)); throw new Exception("cancelled_shortcut_started"); }
        catch (OperationCanceledException) { checks++; }
        Require(File.ReadAllLines(explicitMarker).Length == 1, "cancelled_shortcut_does_not_launch");
    }

    private static TaskQueueViewModel Owner(MaaProcessorManager manager, string id)
    {
        var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        var vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        Set(processor, "<InstanceId>k__BackingField", id);
        Set(processor, "<InstanceConfiguration>k__BackingField", new InstanceConfiguration(id));
        New(processor, "<Config>k__BackingField");
        New(processor, "<LogItemViewModels>k__BackingField");
        New(processor, "<TaskQueue>k__BackingField");
        Set(vm, "_processorField", processor);
        var adbPath = Path.Combine(AppPaths.DataRoot, "adb.exe");
        File.WriteAllText(adbPath, "OFFLINE FIXTURE - MUST NEVER EXECUTE");
        Set(vm, "_currentController", MaaControllerTypes.Adb);
        Set(vm, "_currentDevice", new AdbDeviceInfo("synthetic startup target", adbPath, "127.0.0.1:16384",
            AdbScreencapMethods.Encode, AdbInputMethods.None, "{}"));
        New(vm, "_pcrDiscovery");
        New(vm, "_currentResources");
        var nodes = new Dictionary<string, JToken> { ["PCRAlphaEntry"] = new JObject { ["custom_action_param"] = new JObject { ["selection_mode"] = "pi_nodes_v1" } } };
        foreach (var name in new[] { "Schedule", "Event", "Abyss", "Deep", "Rewards", "VeryHard", "Hard", "Normal", "RewardsFinal" })
            nodes["PCRAlphaSelect" + name] = new JObject { ["attach"] = new JObject { ["selected"] = name == "Rewards" } };
        var item = (DragItemViewModel)RuntimeHelpers.GetUninitializedObject(typeof(DragItemViewModel));
        Set(item, "_interfaceItem", new MaaInterface.MaaInterfaceTask { Name = "PCRAlpha", Entry = "PCRAlphaEntry", PipelineOverride = nodes,
            Option = [new MaaInterface.MaaInterfaceSelectOption { Name = "AlphaModules", SelectedCases = ["rewards"] }] });
        Set(vm, "_taskItemViewModels", new ObservableCollection<DragItemViewModel> { item });
        Set(manager, "_instances", new Dictionary<string, MaaProcessor> { [id] = processor });
        Set(manager, "_viewModels", new Dictionary<string, TaskQueueViewModel> { [id] = vm });
        Set(manager, "_instanceOrder", new List<string> { id });
        Set(manager, "<Current>k__BackingField", processor);
        var tab = (InstanceTabViewModel)RuntimeHelpers.GetUninitializedObject(typeof(InstanceTabViewModel));
        Set(tab, "<InstanceId>k__BackingField", id);
        typeof(InstanceTabViewModel).GetField("Processor")!.SetValue(tab, processor);
        Set(Instances.InstanceTabBarViewModel, "_activeTab", tab);
        return vm;
    }

    private static void RootStartsOnce(TaskQueueViewModel vm)
    {
        var root = (RootView)RuntimeHelpers.GetUninitializedObject(typeof(RootView));
        var loaded = typeof(RootView).GetMethod("InitializeAfterLoadedAsync", Hidden)!;
        Complete((Task)loaded.Invoke(root, null)!);
        Complete((Task)loaded.Invoke(root, null)!);
        Require(!vm.IsRunning, "startup_returns_to_idle_after_inert_owner");
    }

    private static List<string> Effects(MaaProcessor processor)
    {
        var effects = new List<string>();
        typeof(MaaProcessor).GetMethod("HandleAfterTaskOperation", Hidden)!.Invoke(processor,
            new object[] { (Action)(() => effects.Add("application")), (Action)(() => effects.Add("emulator")),
                (Action)(() => effects.Add("shutdown")), (Action)(() => effects.Add("restart-application")), (Action)(() => effects.Add("restart-system")) });
        return effects;
    }

    private static void CheckSettingsView(string root)
    {
        var model = new StartSettingsUserControlModel();
        var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", Hidden)!.GetValue(null)!;
        cache[typeof(StartSettingsUserControlModel)] = new Lazy<object>(() => model);
        var view = new StartSettingsUserControl { Background = Brushes.White };
        var window = new Window { Content = view, Width = 960, Height = 720, ShowActivated = false,
            ShowInTaskbar = false, Position = new PixelPoint(-12000, -12000) };
        try
        {
            window.Show();
            Dispatcher.UIThread.RunJobs();
            var boxes = view.GetLogicalDescendants().OfType<ComboBox>().Take(2).ToArray();
            Require(boxes.Length == 2 && (string?)boxes[0].SelectedValue == "StartupSoftwareAndScript"
                && (string?)boxes[1].SelectedValue == "CloseEmulatorAndMFA", "restarted_real_settings_controls_show_saved_actions");
            Require(view.GetLogicalDescendants().OfType<TextBlock>().Any(text => text.Text == "启动游戏后等待（秒）"),
                "startup_wait_label_matches_owner_connection_order");
            boxes[0].SetCurrentValue(ComboBox.SelectedValueProperty, "StartupScriptOnly");
            boxes[1].SetCurrentValue(ComboBox.SelectedValueProperty, "None");
            Dispatcher.UIThread.RunJobs();
            var edited = new InstanceConfiguration("pcr-alpha-v1");
            Require(edited.GetValue(ConfigurationKeys.BeforeTask, "None") == "StartupScriptOnly"
                && edited.GetValue(ConfigurationKeys.AfterTask, "None") == "None", "real_settings_control_edits_are_persisted");
            boxes[0].SetCurrentValue(ComboBox.SelectedValueProperty, "StartupSoftwareAndScript");
            boxes[1].SetCurrentValue(ComboBox.SelectedValueProperty, "CloseEmulatorAndMFA");
            Dispatcher.UIThread.RunJobs();
            using var frame = new RenderTargetBitmap(new PixelSize(960, 720));
            frame.Render(view);
            frame.Save(Path.Combine(root, "restarted-start-settings.png"));
        }
        finally { window.Close(); Dispatcher.UIThread.RunJobs(); }
    }

    private static void CheckUpdateProxySettings(string root)
    {
        var view = new VersionUpdateSettingsUserControl { Background = Brushes.White };
        var window = new Window { Content = view, Width = 720, Height = 840, ShowActivated = false,
            ShowInTaskbar = false, Position = new PixelPoint(-12000, -12000) };
        try
        {
            window.Show();
            Dispatcher.UIThread.RunJobs();
            var mode = view.GetLogicalDescendants().OfType<ComboBox>().Single();
            var address = view.GetLogicalDescendants().OfType<TextBox>().Single();
            Require(mode.ItemCount == 3
                && (VersionUpdateSettingsUserControlModel.UpdateProxyMode?)mode.SelectedValue
                == VersionUpdateSettingsUserControlModel.UpdateProxyMode.FollowSystem,
                "update_proxy_defaults_to_follow_system");
            Require(!address.IsEnabled, "follow_system_disables_custom_address");
            mode.SetCurrentValue(ComboBox.SelectedValueProperty, VersionUpdateSettingsUserControlModel.UpdateProxyMode.Custom);
            Dispatcher.UIThread.RunJobs();
            Require(address.IsEnabled
                && ConfigurationManager.Current.GetValue(ConfigurationKeys.UpdateProxyMode, string.Empty)
                == nameof(VersionUpdateSettingsUserControlModel.UpdateProxyMode.Custom),
                "custom_proxy_enables_address_and_saves_mode");
            mode.SetCurrentValue(ComboBox.SelectedValueProperty, VersionUpdateSettingsUserControlModel.UpdateProxyMode.None);
            Dispatcher.UIThread.RunJobs();
            Require(!address.IsEnabled
                && ConfigurationManager.Current.GetValue(ConfigurationKeys.UpdateProxyMode, string.Empty)
                == nameof(VersionUpdateSettingsUserControlModel.UpdateProxyMode.None),
                "no_proxy_disables_custom_address_and_saves_mode");
            mode.SetCurrentValue(ComboBox.SelectedValueProperty, VersionUpdateSettingsUserControlModel.UpdateProxyMode.FollowSystem);
            Dispatcher.UIThread.RunJobs();
            using var frame = new RenderTargetBitmap(new PixelSize(720, 840));
            frame.Render(view);
            frame.Save(Path.Combine(root, "update-proxy-settings.png"));
        }
        finally { window.Close(); Dispatcher.UIThread.RunJobs(); }
    }

    private static void CheckLabyrinthCompletion(TaskQueueViewModel vm, string root)
    {
        var setup = new ProcessStartInfo(Environment.GetEnvironmentVariable("PCR_STARTUP_TEST_PYTHON") ?? "python")
            { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var arg in new[] { "-m", "venv", "--without-pip", Path.Combine(root, ".venv") }) setup.ArgumentList.Add(arg);
        using (var python = Process.Start(setup)!)
        {
            var errors = python.StandardError.ReadToEndAsync();
            if (!python.WaitForExit(30000)) { python.Kill(true); throw new TimeoutException("fixture_venv_timeout"); }
            Require(python.ExitCode == 0, "fixture_python:" + errors.GetAwaiter().GetResult());
        }
        var script = Path.Combine(root, "scripts", "start-labyrinth-owned-agent.py");
        Directory.CreateDirectory(Path.GetDirectoryName(script)!);
        vm.LabyrinthSettings.ExpectedPlayerId = "987654321";
        vm.LabyrinthSettings.Username = "synthetic-startup-user";
        vm.LabyrinthSettings.Password = "synthetic-startup-password";
        vm.Processor.InstanceConfiguration.SetValue(ConfigurationKeys.AfterTask, "CloseEmulator");
        Set(vm, "_currentController", MaaControllerTypes.Win32);
        using var noCommands = new CancellationTokenSource();
        noCommands.Cancel();
        Set(vm.Processor, "_commandThreadCts", noCommands);
        var processField = typeof(MaaProcessor).GetField("_softwareProcess", Hidden)!;
        var previous = processField.GetValue(null);
        try
        {
            foreach (var outcome in new[] { "complete", "stopped", "defeated", "malformed", "missing-outcome", "cancelled" })
            {
                File.WriteAllText(script, outcome == "cancelled" ? "import sys,json\njson.loads(sys.stdin.readline())\njson.loads(sys.stdin.readline())\n" :
                    "import sys,json\njson.loads(sys.stdin.readline())\nprint(" +
                    System.Text.Json.JsonSerializer.Serialize(outcome == "malformed" ? "invalid-json" :
                        outcome == "missing-outcome" ? "{\"kind\":\"result\"}" :
                        new JObject { ["kind"] = "result", ["outcome"] = outcome, ["batch_complete"] = outcome == "complete" }.ToString(Newtonsoft.Json.Formatting.None)) + ",flush=True)\n");
                var info = new ProcessStartInfo(Environment.ProcessPath!) { UseShellExecute = false, CreateNoWindow = true };
                if (IsDotnetHost) info.ArgumentList.Add(typeof(StartupActionsChecks).Assembly.Location);
                foreach (var arg in new[] { Path.Combine(root, "held-helper.txt"), "--startup-hold-marker" })
                    info.ArgumentList.Add(arg);
                using var helper = Process.Start(info)!;
                processField.SetValue(null, helper);
                try
                {
                    var request = (Task)typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthOwner", true)!
                        .GetMethod("RequestStartAsync", Hidden)!.Invoke(null, [vm])!;
                    if (outcome == "cancelled") vm.StopTask();
                    Complete(request);
                    if (outcome is "complete" or "defeated")
                        Require(helper.WaitForExit(3000), "labyrinth_completion_closes_only_owned_helper:" + outcome + ":" + vm.LabyrinthSettings.Status);
                    else Require(!helper.HasExited, "labyrinth_stop_or_invalid_result_keeps_helper:" + outcome);
                    Require(!vm.IsRunning && vm.CanEditPcrTarget, "labyrinth_cleanup_finishes_before_after_action:" + outcome);
                }
                finally { if (!helper.HasExited) { helper.Kill(true); helper.WaitForExit(3000); } }
            }
            // Reproduce a database log arriving while the human captcha layer is open.
            var overlay = new PcrCaptchaOverlay { IsVisible = true };
            var host = typeof(PcrCaptchaOverlay).Assembly.GetType("MFAAvalonia.Pcr.PcrCaptchaHost", true)!;
            host.GetMethod("Attach", Hidden)!.Invoke(null, [overlay]);
            File.WriteAllText(script, "import sys,json\njson.loads(sys.stdin.readline())\n"
                + "print(json.dumps({'kind':'log','text':'游戏数据库已更新，下次任务将使用新数据。'}),flush=True)\n"
                + "print(json.dumps({'kind':'control','state':'paused'}),flush=True)\n"
                + "sys.stdin.readline()\n");
            var pending = (Task)typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthOwner", true)!
                .GetMethod("RequestStartAsync", Hidden)!.Invoke(null, [vm])!;
            try
            {
                var timer = Stopwatch.StartNew();
                while (!vm.LabyrinthSettings.IsPaused && !pending.IsCompleted && timer.Elapsed < TimeSpan.FromSeconds(15))
                {
                    Dispatcher.UIThread.RunJobs();
                    Thread.Sleep(2);
                }
                Require(vm.LabyrinthSettings.IsPaused, "captcha_fixture_background_messages_received");
                Require(overlay.IsVisible, "background_log_and_pause_keep_captcha_visible");
            }
            finally
            {
                vm.StopTask();
                Complete(pending);
                host.GetMethod("Detach", Hidden)!.Invoke(null, [overlay]);
            }
            Require(!overlay.IsVisible && !vm.IsRunning, "stopping_owner_closes_captcha");
        }
        finally { processField.SetValue(null, previous); }
    }

    private static void CheckAfterActions(MaaProcessor processor)
    {
        var expected = new Dictionary<string, string[]>
        {
            ["None"] = [], ["CloseMFA"] = ["application"], ["CloseEmulator"] = ["emulator"],
            ["CloseEmulatorAndMFA"] = ["emulator", "application"], ["ShutDown"] = ["shutdown"], ["ShutDownOnce"] = ["shutdown"],
            ["CloseEmulatorAndRestartMFA"] = ["emulator", "restart-application"], ["RestartPC"] = ["restart-system"]
        };
        foreach (var (action, effects) in expected)
        {
            processor.InstanceConfiguration.SetValue(ConfigurationKeys.AfterTask, action);
            Require(Effects(processor).SequenceEqual(effects), "completion_effects:" + action);
            Require(new InstanceConfiguration(processor.InstanceId).GetValue(ConfigurationKeys.AfterTask, "None")
                == (action == "ShutDownOnce" ? "None" : action), "completion_persistence:" + action);
        }
        processor.InstanceConfiguration.SetValue(ConfigurationKeys.AfterTask, "ShutDownOnce");
        using var locked = new FileStream(processor.InstanceConfiguration.GetConfigFilePath(), FileMode.Open, FileAccess.Read, FileShare.Read);
        try { _ = Effects(processor); throw new Exception("shutdown_once_ignored_persistence_failure"); }
        catch (TargetInvocationException ex) when (ex.InnerException is IOException) { checks++; }
    }
}
