using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Threading;
using MaaFramework.Binding;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// Opt-in diagnostic of the real App/RootView/Toggle route. No execution fallback.
public static class PcrFormalPreflight
{
    public static bool Enabled { get; private set; }
    public static bool SettingsOnly { get; private set; }
    internal static bool Editing { get; private set; }
    public static string Output { get; private set; } = "";
    private static string adb = "";
    private static JObject? isolatedInput;
    private static readonly string[] HistoricalModules = { "schedule_onekey", "deep", "rewards", "very_hard", "hard" };
    public static bool HistoricalCase => isolatedInput != null;
    private static readonly object LogGate = new();
    private static readonly Stopwatch Watch = Stopwatch.StartNew();
    private static DispatcherTimer? heartbeat;
    private const string Flag = "--pcr-preflight-diagnostic";
    private const string SettingsFlag = "--pcr-settings-diagnostic";

    // Called before Install/AppRuntime/App/configuration initialization. A prepared
    // directory cannot become an ordinary app by dropping/misspelling the flag.
    public static bool Configure(string[] args, string root)
    {
        var home = Path.GetFullPath(AppContext.BaseDirectory).TrimEnd(Path.DirectorySeparatorChar);
        var marker = Path.Combine(home, "formal-preflight.ready");
        var settingsMarker = Path.Combine(home, "player-settings.ready");
        if (File.Exists(settingsMarker) || args.Length == 1 && args[0] == SettingsFlag)
        {
            if (args.Length != 1 || args[0] != SettingsFlag || File.ReadAllText(settingsMarker) != "PCR PLAYER SETTINGS COUNT RESTORE WIN32 ONLY\n")
                throw new InvalidOperationException("settings_diagnostic_explicit_flag_required");
            var settingsHome = Path.Combine(Path.GetFullPath(root), ".runtime", "player-win32-20260929", "count-restore-fix", "formal-settings", "app");
            if (!string.Equals(home, settingsHome, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("settings_diagnostic_isolated_app_required");
            for (var p = new DirectoryInfo(home); p != null; p = p.Parent)
                if ((p.Attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("settings_diagnostic_reparse_forbidden");
            var settingsDirectories = new System.Collections.Generic.Stack<string>(); settingsDirectories.Push(home);
            while (settingsDirectories.Count != 0)
                foreach (var p in Directory.EnumerateFileSystemEntries(settingsDirectories.Pop())) {
                    var attributes = File.GetAttributes(p);
                    if ((attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("settings_diagnostic_reparse_forbidden");
                    if ((attributes & FileAttributes.Directory) != 0) settingsDirectories.Push(p);
                }
            SettingsOnly = true;
            Editing = true; // only draft persistence; device routes have independent guards
            Enabled = true;
            Output = Path.Combine(home, "diagnostic");
            Directory.CreateDirectory(Output);
            PcrAlphaOwner.Install(root, Path.Combine(Output, "unused-config.json"));
            Trace("settings_bootstrap", new JObject { ["pid"] = Environment.ProcessId, ["base_directory"] = home });
            return true;
        }
        if (args.Length == 0 && !File.Exists(marker)) return false;
        if (Enabled || args.Length != 1 || args[0] != Flag)
            throw new InvalidOperationException("diagnostic_explicit_flag_required");
        var allowed = Path.Combine(Path.GetFullPath(root), ".runtime", "gui-start-debug-20260928",
            "formal-diff", "sprint-product-start-20260927") + Path.DirectorySeparatorChar;
        var bounded = Path.Combine(Path.GetFullPath(root), ".runtime", "gui-start-debug-20260928",
            "bounded-compare", "sprint-product-start-20260927") + Path.DirectorySeparatorChar;
        if (!(home.StartsWith(allowed, StringComparison.OrdinalIgnoreCase) || home.StartsWith(bounded, StringComparison.OrdinalIgnoreCase)) || Path.GetFileName(home) != "app"
            || !Path.GetFileName(Path.GetDirectoryName(home)!).StartsWith("formal-", StringComparison.Ordinal)
            || !File.Exists(marker)) throw new InvalidOperationException("diagnostic_isolated_app_required");
        for (var p = new DirectoryInfo(home); p != null; p = p.Parent)
            if ((p.Attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("diagnostic_reparse_forbidden");
        var directories = new System.Collections.Generic.Stack<string>(); directories.Push(home);
        while (directories.Count != 0)
            foreach (var p in Directory.EnumerateFileSystemEntries(directories.Pop())) {
                var attributes = File.GetAttributes(p);
                if ((attributes & FileAttributes.ReparsePoint) != 0) throw new InvalidOperationException("diagnostic_reparse_forbidden");
                if ((attributes & FileAttributes.Directory) != 0) directories.Push(p);
            }
        // Only existence/metadata, never read a preexisting configuration.
        if (new[] { "pcr-alpha-v1", "config", "appsettings.json", "diagnostic", ".runtime", "formal-preflight.used" }
            .Any(p => File.Exists(Path.Combine(home,p)) || Directory.Exists(Path.Combine(home,p))))
            throw new InvalidOperationException("diagnostic_fresh_directory_required");
        if (File.ReadAllText(marker) != "PCR FORMAL PREFLIGHT ONLY\n")
            throw new InvalidOperationException("diagnostic_marker_invalid");
        using (new FileStream(Path.Combine(home,"formal-preflight.used"),FileMode.CreateNew,FileAccess.Write,FileShare.None)) { }
        Output = Path.Combine(home,"diagnostic");
        Directory.CreateDirectory(Output);
        adb = Path.Combine(Output,"synthetic-install","adb.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(adb)!);
        File.WriteAllText(adb,"SYNTHETIC INVALID BINARY - NEVER EXECUTE");
        var caseFile = Path.Combine(home,"formal-case.json");
        if (File.Exists(caseFile)) {
            if (!home.StartsWith(bounded, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("diagnostic_case_scope_invalid");
            var spec = JObject.Parse(File.ReadAllText(caseFile));
            var input = File.ReadAllBytes(Path.Combine(home,"formal-input.json"));
            if (spec.Properties().Count()!=2 || (string?)spec["case"]!="historical"
                || !string.Equals((string?)spec["input_sha256"],Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(input)),StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("diagnostic_case_hash_invalid");
            isolatedInput=JObject.Parse(System.Text.Encoding.UTF8.GetString(input));
            // Preserve all business fields; replace only the transport data. No real binary access.
            isolatedInput["connection"]=new JObject { ["adb_path"]=adb, ["serial"]="127.0.0.1:65530" };
            if ((int?)isolatedInput["version"]!=4 || isolatedInput["controller"] is not JObject controller
                || controller.Properties().Count()!=2 || (string?)controller["kind"]!="adb")
                throw new InvalidOperationException("diagnostic_case_plain_adb_required");
        }
        Enabled = true;
        PcrAlphaOwner.Install(root,Path.Combine(Output,"unused-config.json"));
        Trace("bootstrap",new JObject { ["cwd"]=Environment.CurrentDirectory, ["base_directory"]=home,
            ["stdin_redirected"]=Console.IsInputRedirected, ["stdout_redirected"]=Console.IsOutputRedirected,
            ["python_environment_present"]=new JObject(new[]{"PYTHONHOME","PYTHONPATH","PYTHONINSPECT","PYTHONSTARTUP","PYTHONUTF8","PYTHONIOENCODING"}
                .Select(k=>new JProperty(k,Environment.GetEnvironmentVariable(k)!=null))),
            ["core_sha256"]=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(typeof(PcrAlphaOwner).Assembly.Location))) });
        return true;
    }

    public static void Attach(Window window)
    {
        if (!Enabled) return;
        if (SettingsOnly)
        {
            window.Title += " — PCR 玩家设置隔离验收（零设备）";
            void Ready()
            {
                try {
                    var vm = MaaProcessorManager.Instance.Current.ViewModel!;
                    var status = vm.PcrSettings.Status;
                    var configRoot = MFAAvalonia.Helper.AppPaths.ConfigDirectory;
                    if (!Path.GetFullPath(configRoot).StartsWith(AppContext.BaseDirectory, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("settings_config_root_outside_app");
                    var handle = window.TryGetPlatformHandle()?.HandleDescriptor;
                    if (handle != "HWND") throw new InvalidOperationException("settings_win32_handle_required");
                    File.WriteAllText(Path.Combine(Output, "ready-" + Environment.ProcessId + ".json"), new JObject {
                        ["pid"] = Environment.ProcessId, ["window_type"] = window.GetType().FullName,
                        ["native_handle_kind"] = handle, ["config_root"] = configRoot,
                        ["settings_status"] = status }.ToString());
                    Trace("settings_window_ready", new JObject { ["pid"] = Environment.ProcessId });
                } catch (Exception ex) { Trace("settings_bootstrap_failed", new JObject { ["error"] = ex.Message }); window.Close(); }
            }
            window.Opened += (_, _) => Dispatcher.UIThread.Post(Ready);
            if (window.IsVisible) Dispatcher.UIThread.Post(Ready);
            window.Closed += (_, _) => Trace("settings_window_closed", new JObject { ["pid"] = Environment.ProcessId });
            return;
        }
        window.Title += " — PCR 仅预检诊断（零设备）";
        bool started = false;
        async void Prepare()
        {
            if (started) return;
            started = true;
            try {
                var wait = Stopwatch.StartNew();
                var vm = MaaProcessorManager.Instance.Current.ViewModel!;
                while (!vm.TaskItemViewModels.Any(x=>!x.IsResourceOptionItem)) {
                    if(wait.Elapsed.TotalSeconds>5) throw new InvalidOperationException("diagnostic_tasks_not_ready");
                    await Task.Delay(25);
                }
                Edit(() => {
                    if(!vm.SelectManualPcrAdbTarget(new AdbDeviceInfo("Synthetic preflight only",adb,"127.0.0.1:65530",
                        AdbScreencapMethods.Encode,AdbInputMethods.AdbShell,"{}","")))
                        throw new InvalidOperationException("diagnostic_target_setup_failed");
                    var task=vm.TaskItemViewModels.Single(x=>!x.IsResourceOptionItem);
                    task.IsChecked=true;
                    task.InterfaceItem!.Option!.Single(x=>x.Name=="AlphaModules").SelectedCases=HistoricalCase ? new(HistoricalModules) : new(){"rewards"};
                    task.InterfaceItem.Option.Single(x=>x.Name=="AlphaRewardsFinal").SelectedCases=new(){HistoricalCase ? "Yes" : "No"};
                    return true;
                });
                vm.NotifyPcrDiagnosticBoundary();
                vm.PcrDiscoveryStatus="仅预检诊断：合成设备，搜索/连接/导入禁用；点开始后预检并硬停止。";
                heartbeat = new DispatcherTimer { Interval=TimeSpan.FromMilliseconds(250) };
                heartbeat.Tick += (_,_)=>Trace("ui_heartbeat"); heartbeat.Start();
                File.WriteAllText(Path.Combine(Output,"ready.json"),new JObject { ["ready"]=true,
                    ["window_type"]=window.GetType().FullName, ["native_handle_kind"]=window.TryGetPlatformHandle()?.HandleDescriptor,
                    ["config_root"]=MFAAvalonia.Helper.AppPaths.ConfigDirectory }.ToString());
                Trace("window_ready");
            } catch(Exception ex) { Trace("bootstrap_failed",new JObject{["error"]=ex.Message}); window.Close(); }
        }
        // RootView may already be visible when view-model binding creates it.
        // Use the existing window and Dispatcher; never construct another window.
        window.Opened += (_,_) => Dispatcher.UIThread.Post(Prepare);
        if (window.IsVisible) Dispatcher.UIThread.Post(Prepare);
        window.Closed += (_,_)=> { heartbeat?.Stop(); PcrAlphaOwner.Revoke(); Trace("window_closed"); };
    }

    private static T Edit<T>(Func<T> action)
    {
        if (!Enabled || !Dispatcher.UIThread.CheckAccess() || Editing)
            throw new InvalidOperationException("diagnostic_edit_scope_invalid");
        Editing=true;
        try { return action(); } finally { Editing=false; }
    }
    internal static string Export(TaskQueueViewModel vm, JObject selection)
    {
        try {
            if (isolatedInput != null) {
                var folder=Path.Combine(Output,".runtime","alpha-agent","settings");
                Directory.CreateDirectory(folder);
                var path=Path.Combine(folder,Guid.NewGuid().ToString("N")+".json");
                File.WriteAllText(path,isolatedInput.ToString());
                Trace("isolated_case_export",new JObject { ["case"]="historical", ["path"]=path,
                    ["selection"]=selection.DeepClone(), ["history_read"]="not_in_config_preflight" });
                return path;
            }
            return Edit(()=>vm.PcrSettings.ExportForStart(Output,selection));
        }
        finally { vm.NotifyPcrDiagnosticBoundary(); }
    }
    internal static void RequireSelection(TaskQueueViewModel vm, JObject selection)
    {
        if (SettingsOnly) throw new InvalidOperationException("settings_only_start_forbidden");
        if (!Enabled) return;
        Trace("owner_entry");
        if (!selection["selected_modules"]!.Values<string>().SequenceEqual(HistoricalCase ? HistoricalModules : new[]{"rewards"}) || (bool)selection["rewards_final"]! != HistoricalCase)
            throw new InvalidOperationException("diagnostic_selection_forbidden");
        if (vm.CurrentDevice is not AdbDeviceInfo target || target.AdbPath!=adb || target.AdbSerial!="127.0.0.1:65530")
            throw new InvalidOperationException("diagnostic_synthetic_target_required");
    }
    internal static void Trace(string phase, JObject? fields = null)
    {
        if (!Enabled) return;
        lock(LogGate) {
            var row=fields??new JObject(); row["phase"]=phase; row["elapsed_ms"]=Watch.ElapsedMilliseconds;
            row["thread"]=Environment.CurrentManagedThreadId; row["ui_context"]=SynchronizationContext.Current?.GetType().Name;
            row["session_creations"]=PcrAlphaOwner.SessionCreations; row["controller_creations"]=PcrAlphaOwner.ControllerCreations;
            row["agent_creations"]=PcrAlphaOwner.AgentCreations; row["grants"]=PcrAlphaOwner.Grants;
            // Diagnostic IO must never prevent the original cancellation/reap.
            try { File.AppendAllText(Path.Combine(Output,"formal.jsonl"),row.ToString(Newtonsoft.Json.Formatting.None)+"\n"); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }
}
