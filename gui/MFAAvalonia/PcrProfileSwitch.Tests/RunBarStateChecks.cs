using System;
using System.Collections.Concurrent;
using System.Collections.ObjectModel;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.LogicalTree;
using Avalonia.Threading;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;

internal static class RunBarStateChecks
{
    private const BindingFlags Hidden = BindingFlags.Static | BindingFlags.Instance | BindingFlags.NonPublic;

    internal static void Run()
    {
        var count = 0;
        void Check(bool value, string name)
        {
            if (!value) throw new InvalidOperationException("run_bar:" + name);
            count++;
        }

        // The view is created without the product App; supply only the services the
        // XAML asks for, exactly like the other page checks.
        var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", Hidden)!.GetValue(null)!;
        var rootType = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.ViewModels.Windows.RootViewModel", true)!;
        var hadRoot = cache.TryGetValue(rootType, out var previousRoot);
        cache[rootType] = new Lazy<object>(() => RuntimeHelpers.GetUninitializedObject(rootType));
        var resources = Application.Current!.Resources;
        var hadPlugins = resources.TryGetValue("MdXamlPlugin", out var previousPlugins);
        resources["MdXamlPlugin"] = new Markdown.Avalonia.MdAvPlugins();
        TaskQueueView view;
        try { view = new TaskQueueView(); }
        finally
        {
            if (hadRoot) cache[rootType] = previousRoot!; else cache.TryRemove(rootType, out _);
            if (hadPlugins) resources["MdXamlPlugin"] = previousPlugins!; else resources.Remove("MdXamlPlugin");
        }

        var button = view.FindControl<Button>("PcrStartStopButton")!;
        ((Panel)button.Parent!).Children.Remove(button);
        var vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        typeof(MaaProcessor).GetField("<InstanceId>k__BackingField", Hidden)!.SetValue(processor, "pcr-alpha-v1");
        typeof(TaskQueueViewModel).GetField("_processorField", Hidden)!.SetValue(vm, processor);
        typeof(TaskQueueViewModel).GetField("_taskItemViewModels", Hidden)!
            .SetValue(vm, new ObservableCollection<DragItemViewModel>());
        var discovery = typeof(TaskQueueViewModel).Assembly.GetType("MFAAvalonia.Pcr.PcrAdbDiscovery", true)!;
        typeof(TaskQueueViewModel).GetField("_pcrDiscovery", Hidden)!.SetValue(vm, Activator.CreateInstance(discovery, true));
        vm.ToggleEnable = true;

        var window = new Window
        {
            Width = 320, Height = 120, ShowActivated = false, ShowInTaskbar = false,
            RequestedThemeVariant = Avalonia.Styling.ThemeVariant.Light,
            WindowStartupLocation = WindowStartupLocation.Manual,
            Position = new PixelPoint(-32000, -32000), Content = button, DataContext = vm
        };
        try
        {
            window.Show();
            Dispatcher.UIThread.RunJobs();
            window.UpdateLayout();
            string[] VisibleLabels() => button.GetLogicalDescendants().OfType<TextBlock>()
                .Where(text => text.IsVisible).Select(text => text.Text ?? "").ToArray();

            Check(!vm.IsRunning && !vm.PcrCleanupPending, "starts_idle");
            Check(VisibleLabels().SequenceEqual(new[] { "开始任务" }) && button.IsEnabled, "idle_shows_start");

            vm.IsRunning = true;
            Dispatcher.UIThread.RunJobs();
            Check(VisibleLabels().SequenceEqual(new[] { "停止任务" }) && button.IsEnabled, "running_shows_stop");

            // A stop request is accepted at once, but the owner keeps the run busy
            // until the owned process is released.
            vm.PcrCleanupPending = true;
            Dispatcher.UIThread.RunJobs();
            Check(VisibleLabels().SequenceEqual(new[] { "处理中…" }), "cleanup_shows_processing_without_stop");
            Check(!button.IsEnabled, "cleanup_disables_the_button");

            vm.IsRunning = false;
            vm.PcrCleanupPending = false;
            Dispatcher.UIThread.RunJobs();
            Check(VisibleLabels().SequenceEqual(new[] { "开始任务" }) && button.IsEnabled, "released_shows_start_again");
        }
        finally
        {
            window.Close();
            Dispatcher.UIThread.RunJobs();
        }

        Console.WriteLine($"PCR run bar state checks passed: {count}");
    }
}
