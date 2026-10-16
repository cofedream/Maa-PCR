using System;
using System.Collections.Concurrent;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using Avalonia;
using Avalonia.Controls;
using Avalonia.LogicalTree;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Configuration;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using MFAAvalonia.Views.UserControls.Settings;

internal static class UpdateViewChecks
{
    internal static void Run(string evidence)
    {
        // Load real controls with isolated settings, without the product App or a controller.
        AppBuilder.Configure<Application>().UsePlatformDetect().SetupWithoutStarting();
        Application.Current!.Styles.Add(new SukiUI.SukiTheme());
        var root = Path.Combine(evidence, "update-controls");
        Directory.CreateDirectory(root);
        const BindingFlags hidden = BindingFlags.Static | BindingFlags.NonPublic;
        typeof(AppPaths).GetField("_configDirectory", hidden)!.SetValue(null, Path.Combine(root, "config"));
        typeof(AppPaths).GetField("_initialized", hidden)!.SetValue(null, true);
        var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", hidden)!.GetValue(null)!;
        var model = (VersionUpdateSettingsUserControlModel)RuntimeHelpers.GetUninitializedObject(typeof(VersionUpdateSettingsUserControlModel));
        typeof(VersionUpdateSettingsUserControlModel).GetField("_pcrCheckStableUpdates", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(model, true);
        cache[typeof(VersionUpdateSettingsUserControlModel)] = new Lazy<object>(() => model);
        var view = new VersionUpdateSettingsUserControl();
        Dispatcher.UIThread.RunJobs();
        var buttons = view.GetLogicalDescendants().OfType<Button>().ToArray();
        Button Find(string label) => buttons.Single(button => button.Content as string == label);
        var count = 0;
        void Require(bool result, string name)
        {
            if (!result) throw new InvalidOperationException(name);
            count++;
        }
        Require(Find("检查更新").Command == model.CheckPcrUpdateCommand, "real_check_button_bound");
        Require(Find("下载并安装").Command == model.InstallPcrUpdateCommand, "real_online_install_button_bound");
        Require(Find("导入更新包").Command == model.UpdateResourceFromLocalPackageCommand, "real_local_import_button_bound");
        model.PcrUpdateAvailable = true;
        Dispatcher.UIThread.RunJobs();
        Require(Find("下载并安装").IsEnabled && Find("导入更新包").IsEnabled, "idle_update_controls_enabled");
        model.PcrUpdateChecking = true;
        Dispatcher.UIThread.RunJobs();
        Require(!Find("检查更新").IsEnabled && !Find("下载并安装").IsEnabled && !Find("导入更新包").IsEnabled,
            "inspection_disables_duplicate_operations");
        model.PcrUpdateChecking = false;
        model.PcrRecoveryRequired = true;
        Dispatcher.UIThread.RunJobs();
        Require(!Find("下载并安装").IsEnabled, "recovery_blocks_online_install");
        model.PcrRecoveryRequired = false;
        var toggle = view.GetLogicalDescendants().OfType<ToggleSwitch>().Single();
        Require(toggle.IsChecked == true, "startup_check_binding_loaded");
        toggle.SetCurrentValue(ToggleSwitch.IsCheckedProperty, false);
        Dispatcher.UIThread.RunJobs();
        Require(!model.PcrCheckStableUpdates && !ConfigurationManager.Current.GetValue(PcrSoftwareUpdateService.CheckSetting, true),
            "startup_check_switch_saves_isolated_setting");
        model.PcrUpdateStatus = "本地增量补丁已通过校验：v0.1.8 / R52 → v0.1.8-r53-candidate / R53。";
        var size = new Size(720, 840);
        var window = new Window { Content = view, Width = size.Width, Height = size.Height,
            Position = new PixelPoint(-10000, -10000), ShowInTaskbar = false,
            SystemDecorations = SystemDecorations.None, Background = Brushes.White };
        try
        {
            window.Show();
            Dispatcher.UIThread.RunJobs();
            window.Measure(size);
            window.Arrange(new Rect(size));
            using var image = new RenderTargetBitmap(new PixelSize(720, 840), new Vector(96, 96));
            image.Render(window);
            image.Save(Path.Combine(root, "update-settings.png"));
        }
        finally { window.Close(); }
        Require(PcrAlphaOwner.SessionCreations == 0 && PcrAlphaOwner.AgentCreations == 0 && PcrAlphaOwner.Grants == 0,
            "update_ui_creates_no_game_authority");
        Console.WriteLine($"PCR update control checks passed: {count}; offscreen fixture window closed; game inputs=0; isolated settings={root}");
    }
}
