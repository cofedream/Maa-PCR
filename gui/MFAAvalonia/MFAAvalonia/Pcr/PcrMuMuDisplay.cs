using System;
using System.IO;
using System.Linq;
using System.Globalization;
using System.Text.RegularExpressions;
using MaaFramework.Binding;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

/// <summary>Session-only standard ADB mapping and a separate legacy read-only
/// Extras candidate. Frame dimensions alone never authorize business input.</summary>
internal static class PcrMuMuDisplay
{
    internal sealed record Mapping(int Logical, string Physical, string Basis = "viewport_verified");
    // Only layout compatibility: target identity is verified independently.
    // Maa scales this landscape frame to 1280x720 and maps inputs back natively.
    internal static void RequireFrameSize(int width, int height)
    {
        if (width <= 0 || height <= 0 || (long)width * 9 != (long)height * 16)
            throw new InvalidOperationException("PCR 需要 16:9 横屏画面，请调整模拟器分辨率后重新开始。");
    }

    internal static void RequireTransport(JObject binding)
    {
        var controller = binding["controller"];
        if ((string?)controller?["kind"] == "adb")
            _ = ComponentPattern((string?)controller["app_package"] ?? "",
                (string?)controller["app_activity"] ?? ".MainActivity");
        if (!MFAAvalonia.ViewModels.UsersControls.AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(
                (string?)binding["connection"]?["adb_path"], (string?)binding["connection"]?["serial"], out _, out _)
            || ((string?)binding["mumu"]?["app_package"] != Package
                && (string?)controller?["kind"] != "adb"))
            throw new InvalidOperationException("Selected local ADB transport/package invalid");
    }

    internal static string ComponentPattern(string package, string activity)
    {
        const string identifier = @"[A-Za-z_][A-Za-z0-9_]*";
        const string className = @"[A-Za-z_$][A-Za-z0-9_$]*";
        if (package.Length > 255 || !Regex.IsMatch(package, @"\A" + identifier + @"(?:\." + identifier + @")+\z")
            || activity.Length > 255 || !Regex.IsMatch(activity, @"\A(?:\." + className + @"(?:\." + className
                + @")*|" + className + @"(?:\." + className + @")+)\z"))
            throw new InvalidOperationException("渠道配置无效：请填写完整包名及相对或完整的运行 Activity。");
        var full = activity.StartsWith('.') ? package + activity : activity;
        var names = full.StartsWith(package + ".", StringComparison.Ordinal)
            ? @"(?:" + Regex.Escape(full[package.Length..]) + "|" + Regex.Escape(full) + ")"
            : Regex.Escape(full);
        return Regex.Escape(package) + "/" + names;
    }

    // Parse only the current resumed/focused records, not Hist/last-paused or
    // the global ResumedActivity summary printed after the last Display #.
    internal static Mapping ParseMapping(string activity, string input, bool validatePhysicalViewport = true,
        string package = Package, string appActivity = ".MainActivity")
    {
        var component = ComponentPattern(package, appActivity);
        Match[] Matches(string text, string pattern) => Regex.Matches(text, pattern, RegexOptions.Multiline,
            TimeSpan.FromSeconds(1)).Cast<Match>().ToArray();
        int Id(string text) => int.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out var id)
            && id >= 0 && id <= 255 && text == id.ToString(CultureInfo.InvariantCulture) ? id : throw new InvalidOperationException("Invalid logical display ID");
        if (activity.Length > 2_000_000 || input.Length > 2_000_000)
            throw new InvalidOperationException("Display readback too large");
        if (!Regex.IsMatch(activity, @"^\s*topResumedActivity=", RegexOptions.Multiline))
            return ParseAndroid9Default(activity, input, validatePhysicalViewport, component);
        var active = Matches(activity, @"^Display #([^\s]+) \(activities from top to bottom\):([\s\S]*?)(?=^Display #|^ActivityTaskSupervisor state:|\z)")
            .SelectMany(block => Matches(block.Groups[2].Value, @"^\s*topResumedActivity=ActivityRecord\{(\S+) u\d+ " + component + @" t\d+\}")
                .Select(record => (Logical: Id(block.Groups[1].Value), Record: record.Groups[1].Value))).ToArray();
        if (active.Length != 1) throw new InvalidOperationException("PCR resumed target missing or ambiguous");
        var logical = active[0].Logical;
        var focused = Matches(activity, @"^  Display: mDisplayId=(\S+) (?:rootTasks=\d+|\(organized\))([\s\S]*?)(?=^  Display:|\z)")
            .Where(block => Regex.IsMatch(block.Groups[2].Value, @"mFocusedApp=ActivityRecord\{\S+ u\d+ " + component + @" t\d+\}"))
            .ToArray();
        if (focused.Length != 1 || Id(focused[0].Groups[1].Value) != logical
            || Matches(focused[0].Groups[2].Value, @"^\s*mCurrentFocus=").Length != 1
            || Matches(focused[0].Groups[2].Value, @"^\s*mFocusedApp=").Length != 1
            || !Regex.IsMatch(focused[0].Groups[2].Value, @"mFocusedApp=ActivityRecord\{" + Regex.Escape(active[0].Record) + @" u\d+ " + component + @" t\d+\}")
            || Matches(focused[0].Groups[2].Value, @"^\s*mCurrentFocus=Window\{\S+ u\d+ " + component + @"\}").Length != 1)
            throw new InvalidOperationException("PCR activity focus mismatch");
        var focusId = Matches(input, @"^\s*FocusedDisplayId: (\S+)\s*$");
        var apps = Matches(input, @"^  FocusedApplications:\r?\n([\s\S]*?)(?=^  \S|\z)");
        var windows = Matches(input, @"^  FocusedWindows:\r?\n([\s\S]*?)(?=^  \S|\z)");
        var appTargets = apps.Length == 1 ? Matches(apps[0].Groups[1].Value,
            @"^\s*displayId=(\S+), name='ActivityRecord\{(\S+) u\d+ " + component + @" t\d+\}'") : [];
        var windowTargets = windows.Length == 1 ? Matches(windows[0].Groups[1].Value,
            @"^\s*displayId=(\S+), name='\S+ " + component + @"'\s*$") : [];
        if (focusId.Length != 1 || Id(focusId[0].Groups[1].Value) != logical
            || appTargets.Length != 1 || Id(appTargets[0].Groups[1].Value) != logical
            || appTargets[0].Groups[2].Value != active[0].Record
            || Matches(apps[0].Groups[1].Value, @"^\s*displayId=" + logical + @",").Length != 1
            || windowTargets.Length != 1 || Id(windowTargets[0].Groups[1].Value) != logical
            || Matches(windows[0].Groups[1].Value, @"^\s*displayId=").Length != 1)
            throw new InvalidOperationException("PCR input focus mismatch");
        if (!validatePhysicalViewport)
        {
            if (logical != 0)
                throw new InvalidOperationException("PCR 位于非默认显示屏，请在高级设置启用“多显示屏映射校验”后重新开始。");
            // Select Maa's native default screencap/input commands. This is not
            // an assertion that a physical viewport uniqueId was observed.
            return new Mapping(0, "0", "logical_default_verified");
        }
        var viewports = Matches(input, @"^\s*Viewport (?:INTERNAL|EXTERNAL): displayId=([^,]+), uniqueId=([^,]+),[^\r\n]*isActive=\[([^\]]+)\]\s*$");
        if (viewports.Length != Matches(input, @"^\s*Viewport (?:INTERNAL|EXTERNAL):").Length
            || viewports.Any(v => !int.TryParse(v.Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out var id)
                || id < 0 || id > 255 || id.ToString(CultureInfo.InvariantCulture) != v.Groups[1].Value))
            throw new InvalidOperationException("Malformed viewport ID");
        var targets = viewports.Where(v => v.Groups[1].Value == logical.ToString(CultureInfo.InvariantCulture)).ToArray();
        var physical = targets.Select(v => v.Groups[2].Value).Distinct().ToArray();
        if (targets.Length == 0 || targets.Any(v => v.Groups[3].Value != "1") || physical.Length != 1
            || !Regex.IsMatch(physical[0], @"^local:(?:0|[1-9][0-9]{0,19})$")
            || physical[0] == "local:0" && logical != 0
            || !ulong.TryParse(physical[0][6..], NumberStyles.None, CultureInfo.InvariantCulture, out _)
            || viewports.Any(v => v.Groups[2].Value == physical[0] && v.Groups[1].Value != logical.ToString(CultureInfo.InvariantCulture)))
            throw new InvalidOperationException("Active physical viewport missing, invalid or conflicting");
        return new Mapping(logical, physical[0][6..]);
    }

    // Android 9 has single global input focus. Accept only its uniquely verified
    // default display. Formats follow AOSP android-9.0.0_r61 AM/InputDispatcher/
    // InputReader; frame dimensions and transport ports never choose the target.
    private static Mapping ParseAndroid9Default(string activity, string input, bool validatePhysicalViewport, string component)
    {
        Match[] Matches(string text, string pattern) => Regex.Matches(text, pattern, RegexOptions.Multiline,
            TimeSpan.FromSeconds(1)).Cast<Match>().ToArray();
        var resumed = Matches(activity, @"^Display #([^\s]+) \(activities from top to bottom\):([\s\S]*?)(?=^Display #|^ActivityStackSupervisor state:|\z)")
            .SelectMany(block => Matches(block.Groups[2].Value, @"^\s*mResumedActivity: ActivityRecord\{(\S+) u0 " + component + @" t\d+\}")
                .Select(record => (Display: block.Groups[1].Value, Record: record.Groups[1].Value))).ToArray();
        if (resumed.Length != 1 || resumed[0].Display != "0")
            throw new InvalidOperationException("PCR resumed target missing or ambiguous");
        var apps = Matches(input, @"^\s*FocusedApplication: name='AppWindowToken\{\S+ token=Token\{\S+ ActivityRecord\{(\S+) u0 " + component + @" t\d+\}\}\}', dispatchingTimeout=\S+\s*$");
        var focused = Matches(input, @"^\s*FocusedWindow: name='(Window\{\S+ u0 " + component + @"\})'\s*$");
        var windows = Matches(input, @"^  Windows:\r?\n([\s\S]*?)(?=^  \S|\z)");
        var focusedRows = windows.Length == 1 ? Matches(windows[0].Groups[1].Value,
            @"^\s*\d+: name='([^']+)', displayId=([^,]+), ([^\r\n]+hasFocus=true[^\r\n]*)\r?$") : [];
        if (Matches(input, @"^\s*FocusedApplication:").Length != 1
            || Matches(input, @"^\s*FocusedWindow:").Length != 1
            || apps.Length != 1 || apps[0].Groups[1].Value != resumed[0].Record || focused.Length != 1
            || focusedRows.Length != 1 || focusedRows[0].Groups[1].Value != focused[0].Groups[1].Value
            || focusedRows[0].Groups[2].Value != "0"
            || !Regex.IsMatch(focusedRows[0].Groups[3].Value, @"^paused=false, hasFocus=true, hasWallpaper=(?:true|false), visible=true, canReceiveKeys=true,"))
            throw new InvalidOperationException("PCR input focus mismatch");
        // Focus independently proves the game's logical default display.
        // Ordinary single-screen ADB does not require vendor viewport text.
        if (!validatePhysicalViewport) return new Mapping(0, "0", "logical_default_verified");
        var internalViewport = Matches(input, @"^\s*ViewportInternal:\r?\n\s*Viewport: displayId=0, orientation=[0-3], uniqueId='local:0', logicalFrame=\[[^\r\n]+\r?$");
        var externalViewport = Matches(input, @"^\s*ViewportExternal:\r?\n\s*Viewport: displayId=-1, [^\r\n]+\r?$");
        var virtualViewport = Matches(input, @"^( *)ViewportVirtual:\r?\n([\s\S]*?)(?=^\1\S|\z)");
        if (Matches(input, @"^\s*ViewportInternal:").Length != 1 || internalViewport.Length != 1
            || Matches(input, @"^\s*ViewportExternal:").Length != 1 || externalViewport.Length != 1
            || Matches(input, @"^\s*ViewportVirtual:").Length != 1 || virtualViewport.Length != 1
            || Regex.IsMatch(virtualViewport[0].Groups[2].Value, @"Viewport: displayId=(?:0|[1-9][0-9]*),"))
            throw new InvalidOperationException("Active physical viewport missing, invalid or conflicting");
        return new Mapping(0, "0");
    }

    internal static string StandardOptions(Mapping mapping)
    {
        if (mapping.Logical < 0 || mapping.Logical > 255 || !Regex.IsMatch(mapping.Physical, @"^(?:0|[1-9][0-9]{0,19})$")
            || mapping.Physical == "0" && mapping.Logical != 0
            || !ulong.TryParse(mapping.Physical, out _)) throw new InvalidOperationException("Invalid display mapping");
        JArray Cmd(params string[] tail) => new(new[] {"{ADB}", "-s", "{ADB_SERIAL}"}.Concat(tail));
        var commands = new JObject();
        foreach (var name in new[] {"Shell", "ClickKey", "InputText", "StartApp", "StartActivity", "StopApp", "Devices"})
            commands[name] = Cmd("shell", "false");
        foreach (var name in new[] {"Connect", "KillServer", "TestConnection"}) commands[name] = Cmd("get-state");
        commands["Monitor"] = Cmd("shell", "cat"); commands["UUID"] = Cmd("get-serialno");
        commands["ScreencapEncode"] = Cmd("exec-out", mapping.Physical == "0" ? "screencap -p" : $"screencap -p -d {mapping.Physical}");
        commands["ScreencapRawWithGzip"] = Cmd("exec-out", mapping.Physical == "0" ? "screencap | gzip -1" : $"screencap -d {mapping.Physical} | gzip -1");
        // Android 9 input lacks -d. Its independently verified default is 0.
        var input = mapping.Logical == 0 ? "input" : $"input -d {mapping.Logical}";
        commands["Click"] = Cmd("shell", $"{input} tap {{X}} {{Y}}");
        commands["Swipe"] = Cmd("shell", $"{input} swipe {{X1}} {{Y1}} {{X2}} {{Y2}} {{DURATION}}");
        return new JObject { ["command"] = commands }.ToString(Formatting.None);
    }

    internal static MaaController CreateStandardController(JObject binding, string agentPath, Mapping mapping,
        Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? offlineFactory)
    {
        if (offlineFactory == null) RequireTransport(binding);
        var adb = (string)binding["connection"]!["adb_path"]!;
        var serial = (string)binding["connection"]!["serial"]!;
        var options = StandardOptions(mapping);
        const AdbScreencapMethods capture = AdbScreencapMethods.Encode | AdbScreencapMethods.RawWithGzip;
        return offlineFactory != null
            ? offlineFactory(adb, serial, capture, AdbInputMethods.AdbShell, options)
                ?? throw new InvalidOperationException("Offline factory returned null")
            : new MaaAdbController(adb, serial, capture, AdbInputMethods.AdbShell, options,
                agentPath, LinkOption.None, CheckStatusOption.None);
    }
    internal const string Package = "com.bilibili.priconne";
    internal const string Blocked = "MuMu Start BLOCKED: SDK display resolution success, fresh package/display identity and input target are not attestable by the current binding; raw dimensions are insufficient";

    internal static void RequireProductionQualification() => throw new InvalidOperationException(Blocked);

    // Data-only construction, also used by the offline contract test. SDK file
    // presence is necessary, not proof that it loads or resolves this package.
    internal static string Options(string installRoot, string sdk, int instance, int clone, string package)
    {
        if (package != Package) throw new InvalidOperationException("MuMu package mismatch");
        if (instance < 0 || clone < 0) throw new InvalidOperationException("MuMu instance/clone missing");
        if (string.IsNullOrWhiteSpace(installRoot) || !Path.IsPathFullyQualified(installRoot) || !Directory.Exists(installRoot))
            throw new InvalidOperationException("MuMu installation root missing");
        if (string.IsNullOrWhiteSpace(sdk) || !Path.IsPathFullyQualified(sdk) || Path.GetFileName(sdk) != "external_renderer_ipc.dll" || !File.Exists(sdk))
            throw new InvalidOperationException("MuMu SDK missing");
        JArray Cmd(params string[] tail) => new(new[] {"{ADB}", "-s", "{ADB_SERIAL}"}.Concat(tail));
        var commands = new JObject();
        foreach (var name in new[] {"Shell", "Click", "Swipe", "ClickKey", "InputText", "StartApp", "StartActivity", "StopApp", "Devices", "ScreencapEncode"})
            commands[name] = Cmd("shell", "false");
        foreach (var name in new[] {"Connect", "KillServer", "TestConnection"}) commands[name] = Cmd("get-state");
        commands["Monitor"] = Cmd("shell", "cat"); commands["UUID"] = Cmd("get-serialno");
        return new JObject { ["command"] = commands, ["extras"] = new JObject { ["mumu"] = new JObject {
            ["enable"] = true, ["path"] = installRoot, ["lib"] = sdk, ["index"] = instance,
            ["app_package"] = Package, ["app_cloned_index"] = clone } } }.ToString(Formatting.None);
    }

    internal static MaaController CreateController(JObject binding, string agentPath)
    {
        // Deliberately before configuration access/native allocation. There is no
        // config, environment or screenshot override for this production block.
        RequireProductionQualification();
        return Construct(binding, agentPath, null);
    }

    // The formal Owner may prepare this candidate, but it can never be armed:
    // native input is disabled, and the business factory above remains gated.
    internal static MaaController CreatePreparationController(JObject binding, string agentPath,
        Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? offlineFactory)
        => Construct(binding, agentPath, offlineFactory, AdbInputMethods.None);

    // Offline proof of the same connection/options path, never production input
    // qualification. No configuration flag or UI entry can install this factory.
    internal static MaaCustomController CreateOfflineController(JObject binding,
        Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController> factory)
    {
        ArgumentNullException.ThrowIfNull(factory);
        return (MaaCustomController)Construct(binding, "", factory);
    }

    private static MaaController Construct(JObject binding, string agentPath,
        Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? offlineFactory,
        AdbInputMethods input = AdbInputMethods.EmulatorExtras)
    {
        var mumu = binding["mumu"] ?? throw new InvalidOperationException("MuMu binding missing");
        var options = Options((string)mumu["path"]!, (string)mumu["lib"]!, (int)mumu["index"]!,
            (int)mumu["app_cloned_index"]!, (string)mumu["app_package"]!);
        var adb = (string)binding["connection"]!["adb_path"]!;
        var serial = (string)binding["connection"]!["serial"]!;
        const AdbScreencapMethods screencap = AdbScreencapMethods.EmulatorExtras;
        return offlineFactory != null
            ? offlineFactory(adb, serial, screencap, input, options)
                ?? throw new InvalidOperationException("Offline MuMu factory returned null")
            // Construction must not auto-connect under the Owner lock. The
            // Owner explicitly posts the only connection after revocation checks.
            : new MaaAdbController(adb, serial, screencap, input, options, agentPath,
                LinkOption.None, CheckStatusOption.None);
    }
}
