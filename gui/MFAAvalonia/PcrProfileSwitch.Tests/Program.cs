using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.Reflection;
using System.Runtime.CompilerServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Data;
using Avalonia.Threading;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Controls;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Other;
using MFAAvalonia.ViewModels.Pages;

try
{
Console.OutputEncoding = System.Text.Encoding.UTF8;
if (args.Length != 3 && !(args.Length == 4 && args[3] is "--guide-only" or "--guide-owner" or "--clan-only" or "--clan-daily-only" or "--clan-workflow-only" or "--clan-workflow-window" or "--clan-package-fixture" or "--clan-formal-only" or "--clan-postsave-only" or "--profile-registration-only"))
    throw new ArgumentException("Usage: PcrProfileSwitch.Tests <source-root> <isolated-fixture-directory> <development-python> [--guide-only|--guide-owner|--clan-only|--clan-daily-only|--clan-workflow-only|--clan-workflow-window|--clan-package-fixture|--clan-formal-only|--clan-postsave-only|--profile-registration-only]");
// Initialize rendering services for log themes without creating a window or the product App.
AppBuilder.Configure<Application>().UsePlatformDetect().SetupWithoutStarting();
Application.Current!.Styles.Add(new Avalonia.Themes.Fluent.FluentTheme());
Application.Current!.Styles.Add(new SukiUI.SukiTheme());
if (args.Length == 4 && args[3] == "--clan-package-fixture") { ClanWorkflowChecks.SeedPackage(args[0], args[1]); return 0; }
if (args.Length == 4 && args[3] == "--clan-formal-only") { ClanFormalChecks.Run(args[0], args[1]); return 0; }
if (args.Length == 4 && args[3] == "--clan-postsave-only") { ClanPostSaveFailureChecks.Run(args[0], args[1]); return 0; }
if (args.Length == 4 && args[3] == "--clan-workflow-only") { ClanWorkflowChecks.Run(args[0], args[1]); return 0; }
if (args.Length == 4 && args[3] == "--clan-workflow-window") { ClanWorkflowChecks.ShowWindow(args[0], args[1]); return 0; }
if (args.Length == 4 && args[3] == "--clan-daily-only") { ClanDailyPlanChecks.Run(args[0], args[1]); return 0; }
if (args.Length == 4 && args[3] == "--profile-registration-only") { AgentProfileRegistrationChecks.Run(args[1]); return 0; }
ClanUiFlowChecks.Run(args[1]);
ClanGuideChecks.Run(args[1]);
if (args.Length == 4 && args[3] == "--clan-only") return 0;
GuideReviewChecks.Run(args[1]);
GuideDataChecks.Run(args[0], args[1]);
GuideDraftChecks.Run(args[1]);
GuideAccountChecks.Run(args[1]);
GuideOpeningChecks.Run(args[0], args[1]);
GuideMinimumChecks.Run(args[1]);
GuideSourceChecks.Run(args[0], args[1]);
GuideProgressChecks.Run(args[0], args[1], args[2]);
if (args.Length == 4 && args[3] == "--guide-only") return 0;
var guideOwnerOnly = args.Length == 4 && args[3] == "--guide-owner";
// Exercise the real selector and Owner using isolated fixtures without allocating
// a controller or reading the player's installed settings.
var viewModel = (InstanceTabBarViewModel)RuntimeHelpers.GetUninitializedObject(typeof(InstanceTabBarViewModel));
var labyrinth = (InstanceTabViewModel)RuntimeHelpers.GetUninitializedObject(typeof(InstanceTabViewModel));
var daily = (InstanceTabViewModel)RuntimeHelpers.GetUninitializedObject(typeof(InstanceTabViewModel));
var agent = (InstanceTabViewModel)RuntimeHelpers.GetUninitializedObject(typeof(InstanceTabViewModel));
var type = typeof(InstanceTabBarViewModel);
var active = type.GetField("_activeTab", BindingFlags.Instance | BindingFlags.NonPublic)!;
var gate = type.Assembly.GetType("MFAAvalonia.Pcr.PcrProfileRunGate", true)!;
var updatePending = gate.GetField("UpdatePending", BindingFlags.Static | BindingFlags.NonPublic)!;
var owner = type.Assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthOwner", true)!;
var current = owner.GetField("current", BindingFlags.Static | BindingFlags.NonPublic)!;
var prompting = typeof(PcrAlphaOwner).GetField("prompting", BindingFlags.Static | BindingFlags.NonPublic)!;
var notify = gate.GetMethod("Notify", BindingFlags.Static | BindingFlags.NonPublic)!;
var managerType = typeof(MaaProcessorManager);
var manager = (MaaProcessorManager)RuntimeHelpers.GetUninitializedObject(managerType);
var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
typeof(MaaProcessor).GetField("<InstanceId>k__BackingField", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(processor, "pcr-labyrinth-v1");
managerType.GetField("<Current>k__BackingField", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(manager, processor);
managerType.GetField("_lock", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(manager, new object());
managerType.GetField("_instanceOrder", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(manager, new List<string> { processor.InstanceId });
managerType.GetField("_instances", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(manager, new Dictionary<string, MaaProcessor> { [processor.InstanceId] = processor });
var lazyManager = (Lazy<MaaProcessorManager>)managerType.GetField("LazyInstance", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
typeof(Lazy<MaaProcessorManager>).GetField("_factory", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(lazyManager, new Func<MaaProcessorManager>(() => manager));
var taskViewModel = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
var timer = new System.Timers.Timer();
var taskType = typeof(TaskQueueViewModel);
taskType.GetField("_processorField", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(taskViewModel, processor);
taskType.GetField("_liveViewTimer", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(taskViewModel, timer);
taskType.GetField("_liveViewRefreshRate", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(taskViewModel, 30.0);
var viewModels = new Dictionary<string, TaskQueueViewModel> { [processor.InstanceId] = taskViewModel };
managerType.GetField("_viewModels", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(manager, viewModels);
foreach (var tab in new[] { labyrinth, daily, agent })
{
    var id = "switch-fixture-" + Guid.NewGuid().ToString("N");
    typeof(InstanceTabViewModel).GetField("Processor")!.SetValue(tab, processor);
    typeof(InstanceTabViewModel).GetField("<InstanceId>k__BackingField", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(tab, id);
    viewModels[id] = taskViewModel;
}
active.SetValue(viewModel, labyrinth);
var notifications = 0;
var count = 0;
viewModel.PropertyChanged += (_, e) =>
{
    if (e.PropertyName != nameof(InstanceTabBarViewModel.ActiveTab)) return;
    if (++notifications > 12)
        throw new InvalidOperationException("profile_selection_oscillates_while_busy: 13 ActiveTab notifications");
};
try
{
    var tabs = new InstanceTabsControl { ItemsSource = new[] { labyrinth, daily, agent }, DataContext = viewModel };
    using var tabBinding = tabs.Bind(SelectingItemsControl.SelectedItemProperty, new Binding(nameof(InstanceTabBarViewModel.ActiveTab)) { Mode = BindingMode.TwoWay });
    using var tabEnabledBinding = tabs.Bind(Control.IsEnabledProperty, new Binding("ActiveTab.TaskQueueViewModel.CanEditPcrTarget"));
    var dropdown = new Button { DataContext = viewModel };
    using var dropdownEnabledBinding = dropdown.Bind(Control.IsEnabledProperty, new Binding("ActiveTab.TaskQueueViewModel.CanEditPcrTarget"));
    var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
    cache[typeof(InstanceTabBarViewModel)] = new Lazy<object>(() => viewModel);
    var settings = (SettingsViewModel)RuntimeHelpers.GetUninitializedObject(typeof(SettingsViewModel));
    typeof(SettingsViewModel).GetField("_currentConfiguration", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(settings, labyrinth);
    var settingsHandler = (PropertyChangedEventHandler)typeof(SettingsViewModel).GetMethod("OnInstanceTabBarViewModelPropertyChanged", BindingFlags.Instance | BindingFlags.NonPublic)!.CreateDelegate(typeof(PropertyChangedEventHandler), settings);
    viewModel.PropertyChanged += settingsHandler;
    var settingsSelector = new ComboBox { ItemsSource = new[] { labyrinth, daily, agent }, DataContext = settings };
    using var settingsBinding = settingsSelector.Bind(ComboBox.SelectedItemProperty, new Binding(nameof(SettingsViewModel.CurrentConfiguration)) { Mode = BindingMode.TwoWay });
    using var settingsEnabledBinding = settingsSelector.Bind(Control.IsEnabledProperty, new Binding("ActiveTab.TaskQueueViewModel.CanEditPcrTarget") { Source = viewModel });
    Require(ReferenceEquals(tabs.SelectedItem, labyrinth) && ReferenceEquals(settingsSelector.SelectedItem, labyrinth), "initial_bound_selection");

    void CheckBlocked(string boundary)
    {
        notify.Invoke(null, null);
        Require(!tabs.IsEnabled && !dropdown.IsEnabled && !settingsSelector.IsEnabled, boundary + "_disables_all_profile_entries");
        foreach (var surface in new SelectingItemsControl[] { tabs, settingsSelector })
        foreach (var target in new[] { daily, agent })
        {
            notifications = 0;
            surface.SetCurrentValue(SelectingItemsControl.SelectedItemProperty, target);
            Dispatcher.UIThread.RunJobs();
            Require(ReferenceEquals(viewModel.ActiveTab, labyrinth), boundary + "_retains_original_profile");
            string Selected(object? item) => ReferenceEquals(item, labyrinth) ? "labyrinth" : ReferenceEquals(item, daily) ? "daily" : "null";
            Require(ReferenceEquals(tabs.SelectedItem, labyrinth) && ReferenceEquals(settingsSelector.SelectedItem, labyrinth), boundary + "_restores_all_selectors: tabs=" + Selected(tabs.SelectedItem) + ", settings=" + Selected(settingsSelector.SelectedItem));
            Require(notifications <= 2, boundary + "_has_bounded_notifications");
            var settled = notifications;
            Dispatcher.UIThread.RunJobs();
            Require(notifications == settled, boundary + "_does_not_requeue_itself");
            if (surface is InstanceTabsControl)
            {
                surface.SetCurrentValue(SelectingItemsControl.SelectedItemProperty, null);
                Dispatcher.UIThread.RunJobs();
                Require(ReferenceEquals(viewModel.ActiveTab, labyrinth) && ReferenceEquals(surface.SelectedItem, labyrinth), boundary + "_transient_null_retains_profile");
            }
        }
    }

    updatePending.SetValue(null, true);
    CheckBlocked("update");
    updatePending.SetValue(null, false);
    current.SetValue(null, RuntimeHelpers.GetUninitializedObject(owner.GetNestedType("Run", BindingFlags.NonPublic)!));
    taskType.GetField("_isRunning", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(taskViewModel, true);
    CheckBlocked("labyrinth_preparing");
    current.SetValue(null, null);
    taskType.GetField("_isRunning", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(taskViewModel, false);
    prompting.SetValue(null, 1);
    CheckBlocked("daily_preparing");
    prompting.SetValue(null, 0);
    notify.Invoke(null, null);
    Require(tabs.IsEnabled && dropdown.IsEnabled && settingsSelector.IsEnabled, "gate_release_reenables_all_profile_entries");
    labyrinth.IsActive = true;
    notifications = 0;
    tabs.SetCurrentValue(SelectingItemsControl.SelectedItemProperty, daily);
    Dispatcher.UIThread.RunJobs();
    Require(ReferenceEquals(viewModel.ActiveTab, daily) && ReferenceEquals(tabs.SelectedItem, daily) && ReferenceEquals(settingsSelector.SelectedItem, daily), "gate_release_allows_selection");
    Require(daily.IsActive && !labyrinth.IsActive, "normal_selection_updates_active_flags");
    Require(notifications == 1, "normal_selection_notifies_once");
    settingsSelector.SetCurrentValue(ComboBox.SelectedItemProperty, labyrinth);
    Dispatcher.UIThread.RunJobs();
    Require(ReferenceEquals(viewModel.ActiveTab, labyrinth) && labyrinth.IsActive && !daily.IsActive, "normal_selection_can_return_to_original");
    tabs.SetCurrentValue(SelectingItemsControl.SelectedItemProperty, agent);
    Dispatcher.UIThread.RunJobs();
    Require(ReferenceEquals(viewModel.ActiveTab, agent) && agent.IsActive && !labyrinth.IsActive, "normal_selection_can_select_agent");
    settingsSelector.SetCurrentValue(ComboBox.SelectedItemProperty, labyrinth);
    Dispatcher.UIThread.RunJobs();
    Require(ReferenceEquals(viewModel.ActiveTab, labyrinth) && labyrinth.IsActive && !agent.IsActive, "normal_selection_can_return_from_agent");
    Require(PcrAlphaOwner.ControllerCreations == 0 && PcrAlphaOwner.AgentCreations == 0 && PcrAlphaOwner.Grants == 0 && PcrAlphaOwner.SessionCreations == 0, "no_device_or_run_created");
    if (!guideOwnerOnly) {
        ChannelConfigChecks.Run(args.Length > 1 ? args[1] : System.IO.Path.GetTempPath());
        LogExportChecks.Run(args[1]);
    }
    AlphaOwnerChecks.Run();
    AgentProfileChecks.Run(processor, taskViewModel, manager);
    AgentShutdownChecks.Run(args);
    if (!guideOwnerOnly) ModuleOrderChecks.Run(taskViewModel, args[1]);
    AlphaOwnerAsyncChecks.Run(processor, taskViewModel, manager, CheckBlocked, args);
    if (!guideOwnerOnly) {
        LiveViewChecks.Run();
        PreviewConnectionChecks.Run(manager, args[1]);
        RunFeedbackChecks.Run();
    }
    Console.WriteLine($"PCR profile switch checks passed: {count}; final ActiveTab notifications={notifications}; controller/agent/grant starts=0");
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine("PCR profile switch checks failed: " + error);
    return 1;
}
finally
{
    updatePending.SetValue(null, false);
    current.SetValue(null, null);
    prompting.SetValue(null, 0);
    timer.Dispose();
}

void Require(bool value, string check)
{
    if (!value) throw new InvalidOperationException(check);
    count++;
}
}
catch (Exception error)
{
    Console.Error.WriteLine("PCR profile switch fixture failed: " + error);
    return 1;
}
