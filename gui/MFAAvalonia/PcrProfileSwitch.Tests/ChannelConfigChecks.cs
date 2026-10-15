using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class ChannelConfigChecks
{
    internal static void Run(string fixtureDirectory)
    {
        var count = 0;
        void Check(bool ok, string name) { if (!ok) throw new InvalidOperationException(name); count++; }
        var type = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrMuMuDisplay")!;
        const BindingFlags hidden = BindingFlags.Static | BindingFlags.NonPublic;
        var pattern = type.GetMethod("ComponentPattern", hidden)!;
        string Pattern(string package, string activity) => (string)pattern.Invoke(null, [package, activity])!;
        var parse = type.GetMethod("ParseMapping", hidden)!;
        const string package = "example.channel.client";
        const string activity = "example.sdk.GameActivity";
        var component = package + "/" + activity;
        var record = "ActivityRecord{fixture u0 " + component + " t1}";
        var window = "Window{window u0 " + component + "}";
        var modernActivity = $"""
Display #0 (activities from top to bottom):
  topResumedActivity={record}
ActivityTaskSupervisor state:
  Display: mDisplayId=0 rootTasks=1
    mFocusedApp={record}
    mCurrentFocus={window}
""";
        var modernInput = $"""
  FocusedDisplayId: 0
  FocusedApplications:
    displayId=0, name='{record}'
  FocusedWindows:
    displayId=0, name='window {component}'
""";
        void Mapping(string a, string i, string p, string c, bool expected)
        {
            try { var result = parse.Invoke(null, [a, i, false, p, c]); Check(expected && result != null, "channel mapping accepted"); }
            catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException)
            { Check(!expected, "channel mapping rejected"); }
        }
        Mapping(modernActivity, modernInput, package, activity, true);
        Mapping(modernActivity, modernInput, "com.bilibili.priconne", ".MainActivity", false);
        Mapping(modernActivity, modernInput, package, ".OtherActivity", false);
        Mapping(modernActivity, modernInput.Replace("FocusedDisplayId: 0", "FocusedDisplayId: 1"), package, activity, false);
        Mapping(modernActivity, modernInput.Replace("window " + component, "window example.other/.MainActivity"), package, activity, false);
        var a9 = $"""
Display #0 (activities from top to bottom):
  mResumedActivity: {record}
ActivityStackSupervisor state:
""";
        var application = "AppWindowToken{token token=Token{token " + record + "}}";
        var i9 = $"""
  FocusedApplication: name='{application}', dispatchingTimeout=5000000000
  FocusedWindow: name='{window}'
  Windows:
    0: name='{window}', displayId=0, paused=false, hasFocus=true, hasWallpaper=false, visible=true, canReceiveKeys=true,
""";
        Mapping(a9, i9, package, activity, true);
        Mapping(a9, i9, package, ".OtherActivity", false);
        Check(Regex.IsMatch("com.bilibili.priconne/.MainActivity", "^" + Pattern("com.bilibili.priconne", ".MainActivity") + "$"), "legacy short component");
        Check(Regex.IsMatch("com.bilibili.priconne/com.bilibili.priconne.MainActivity", "^" + Pattern("com.bilibili.priconne", ".MainActivity") + "$"), "legacy full component");
        Check(!Regex.IsMatch("comXbilibiliXpriconne/.MainActivity", "^" + Pattern("com.bilibili.priconne", ".MainActivity") + "$"), "package dots escaped");
        Check(PcrChannelCatalog.All.Select(row => row.Id).SequenceEqual(new[] { "bilibili", "xiaomi", "tencent", "jiuyou", "huawei", "vivo", "mumu" }), "only evidenced presets shipped");
        foreach (var preset in PcrChannelCatalog.All) {
            var full = preset.Activity.StartsWith('.') ? preset.Package + preset.Activity : preset.Activity;
            var observed = preset.Package + "/" + full;
            Mapping(modernActivity.Replace(component, observed), modernInput.Replace(component, observed), preset.Package, preset.Activity, true);
        }
        var xiaomi = PcrChannelCatalog.All.Single(row => row.Id == "xiaomi");
        var splash = xiaomi.Package + "/com.bilibili.priconne.bili.SplashActivity";
        Mapping(modernActivity.Replace(component, splash), modernInput.Replace(component, splash), xiaomi.Package, xiaomi.Activity, false);
        foreach (var invalid in new[] { "example;reboot", "example.client\n", "example/other", "example.*" })
        {
            try { Pattern(invalid, ".MainActivity"); throw new Exception("invalid package accepted"); }
            catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException) { count++; }
        }
        foreach (var invalid in new[] { "", "MainActivity", ".MainActivity\n", ".Main.*", ".MainActivity;reboot" })
        {
            try { Pattern(package, invalid); throw new Exception("invalid activity accepted"); }
            catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException) { count++; }
        }
        // Exercise the real saved draft serializer without creating a device or account.
        var settings = (PcrPlayerSettings)RuntimeHelpers.GetUninitializedObject(typeof(PcrPlayerSettings));
        const BindingFlags instance = BindingFlags.NonPublic | BindingFlags.Instance;
        typeof(PcrPlayerSettings).GetField("<Schedule>k__BackingField", instance)!.SetValue(settings,
            new System.Collections.ObjectModel.ObservableCollection<PcrScheduleChoice>());
        var load = typeof(PcrPlayerSettings).GetMethod("Load", instance)!;
        var payload = typeof(PcrPlayerSettings).GetMethod("Payload", instance)!;
        load.Invoke(settings, [new JObject()]);
        Check(settings.GamePackage == "com.bilibili.priconne" && settings.GameActivity == ".MainActivity", "old draft defaults");
        load.Invoke(settings, [new JObject { ["gamePackage"] = package, ["gameActivity"] = activity }]);
        var saved = (JObject)payload.Invoke(settings, null)!;
        Check((string?)saved["gamePackage"] == package && (string?)saved["gameActivity"] == activity, "channel draft roundtrip");
        Check(settings.SelectedChannel == null, "unlisted draft remains unselected");
        try {
            typeof(PcrPlayerSettings).GetMethod("Config", instance)!.Invoke(settings, [Array.Empty<string>()]);
            throw new Exception("unlisted draft executed");
        } catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException) { count++; }
        settings.SelectedChannel = xiaomi;
        Check(settings.GamePackage == xiaomi.Package && settings.GameActivity == xiaomi.Activity, "preset selection supplies exact component");
        var oldIdentity = new JObject { ["app_package"] = "com.bilibili.priconne" };
        typeof(PcrPlayerSettings).GetField("managerIdentity", instance)!.SetValue(settings, oldIdentity);
        typeof(PcrPlayerSettings).GetField("managerConnection", instance)!.SetValue(settings, new JObject());
        typeof(PcrPlayerSettings).GetField("ledgerIdentity", instance)!.SetValue(settings, oldIdentity);
        typeof(PcrPlayerSettings).GetField("identityConnection", instance)!.SetValue(settings, new JObject());
        var exported = (JObject)typeof(PcrPlayerSettings).GetMethod("Config", instance)!.Invoke(settings, [Array.Empty<string>()])!;
        Check((string?)exported["controller"]?["app_package"] == xiaomi.Package
            && (string?)exported["controller"]?["app_activity"] == xiaomi.Activity, "channel export identity");
        Check(exported["controller"]?["manager_identity"] == null && exported["controller"]?["ledger_identity"] == null,
            "channel cannot inherit B-server ledger identity");
        saved = (JObject)payload.Invoke(settings, null)!;
        Check(JToken.DeepEquals(saved["managerIdentity"], oldIdentity) && JToken.DeepEquals(saved["ledgerIdentity"], oldIdentity),
            "old identities preserved in draft");
        var cache = (System.Collections.Concurrent.ConcurrentDictionary<Type, Lazy<object>>)
            typeof(MFAAvalonia.Helper.Instances).GetField("ServiceCache", hidden)!.GetValue(null)!;
        var rootType = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.ViewModels.Windows.RootViewModel")!;
        var hadRoot = cache.TryGetValue(rootType, out var previousRoot);
        cache[rootType] = new Lazy<object>(() => RuntimeHelpers.GetUninitializedObject(rootType));
        var resources = Avalonia.Application.Current!.Resources;
        var hadPlugins = resources.TryGetValue("MdXamlPlugin", out var previousPlugins);
        resources["MdXamlPlugin"] = new Markdown.Avalonia.MdAvPlugins();
        TaskQueueView view;
        try { view = new TaskQueueView(); }
        finally {
            if (hadRoot) cache[rootType] = previousRoot!; else cache.TryRemove(rootType, out _);
            if (hadPlugins) resources["MdXamlPlugin"] = previousPlugins!; else resources.Remove("MdXamlPlugin");
        }
        var channel = view.FindControl<Expander>("PcrChannelSettings")!;
        var selector = view.FindControl<ComboBox>("PcrChannelSelector")!;
        Check(view.FindControl<TextBox>("PcrGamePackage") == null && view.FindControl<TextBox>("PcrGameActivity") == null,
            "players do not fill package or Activity");
        ((Panel)channel.Parent!).Children.Remove(channel);
        var model = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        typeof(TaskQueueViewModel).GetField("_pcrSettings", instance)!.SetValue(model, settings);
        channel.DataContext = model; channel.IsExpanded = true;
        var windowView = new Window { Width = 600, Height = 400, ShowActivated = false, ShowInTaskbar = false,
            RequestedThemeVariant = Avalonia.Styling.ThemeVariant.Light,
            WindowStartupLocation = WindowStartupLocation.Manual, Position = new Avalonia.PixelPoint(-32000, -32000),
            Content = new Border { Padding = new Avalonia.Thickness(18), Background = Avalonia.Media.Brushes.White, Child = channel } };
        windowView.Show();
        Dispatcher.UIThread.RunJobs(); windowView.UpdateLayout();
        Check(ReferenceEquals(selector.SelectedItem, xiaomi) && selector.ItemCount == 7, "actual selector loads saved channel");
        foreach (var preset in PcrChannelCatalog.All) {
            selector.SelectedItem = preset;
            Dispatcher.UIThread.RunJobs();
            Check(settings.SelectedChannel == preset && settings.GamePackage == preset.Package
                && settings.GameActivity == preset.Activity, "actual selector updates exact component");
            exported = (JObject)typeof(PcrPlayerSettings).GetMethod("Config", instance)!.Invoke(settings, [Array.Empty<string>()])!;
            Check((string?)exported["controller"]?["app_package"] == preset.Package
                && ((string?)exported["controller"]?["app_activity"] ?? ".MainActivity") == preset.Activity, "each preset exports exact component");
            saved = (JObject)payload.Invoke(settings, null)!;
            load.Invoke(settings, [saved]);
            Check(settings.SelectedChannel == preset, "selected preset survives draft reload");
        }
        Directory.CreateDirectory(Path.Combine(fixtureDirectory, "channel-config"));
        System.Threading.Thread.Sleep(250); Dispatcher.UIThread.RunJobs();
        windowView.UpdateLayout();
        using (var frame = new RenderTargetBitmap(new Avalonia.PixelSize(600, 400)))
        {
            frame.Render((Avalonia.Visual)windowView.Content!);
            frame.Save(Path.Combine(fixtureDirectory, "channel-config", "settings.png"));
        }
        windowView.Close();
        Console.WriteLine($"PCR channel config checks passed: {count}; real device inputs=0");
    }
}
