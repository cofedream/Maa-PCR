using System;
using System.IO;
using System.Reflection;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class CapturePerformanceChecks
{
    internal static void Run(string fixtureDirectory)
    {
        var type = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrCapturePerformance")!;
        const BindingFlags hidden = BindingFlags.Static | BindingFlags.NonPublic;
        var selected = type.GetMethod("SelectedMethod", hidden)!;
        var count = 0;
        void Check(bool value, string name) { if (!value) throw new InvalidOperationException(name); count++; }
        const int pid = 1234;
        string Create(int process = pid, int requested = 6) => $"[2026-10-16 02:42:49.958][DBG][Px{process}][Tx1][MaaAdbController.cpp][L25][MaaAdbControllerCreate] [address=fixture] [screencap_methods={requested}] [input_methods=1] [config={{}}] | enter \n";
        string Selection(string method, int process = pid) => $"[2026-10-16 02:42:50.958][INF][Px{process}][Tx1][ScreencapAgent.cpp][L251][MaaNS::CtrlUnitNs::ScreencapAgent::speed_test] The fastest method is {method} [cost=445ms] \n";
        int? Parse(string text, string serial = "fixture") => (int?)selected.Invoke(null, [text, pid, serial]);
        Check(Parse(Create() + Selection("Encode")) == 2, "observed Encode");
        Check(Parse(Create() + Selection("RawWithGzip")) == 4, "observed gzip");
        Check(Parse(Create() + Selection("6")) == null, "request mask is not a backend");
        Check(Parse(Create(requested: 2) + Selection("Encode")) == null, "unexpected construction unknown");
        Check(Parse(Create() + Selection("Encode", pid + 1)) == null, "different process unknown");
        Check(Parse(Create() + Selection("Encode"), "other") == null, "different device unknown");
        Check(Parse(Create() + Create() + Selection("Encode")) == null, "multiple controllers unknown");
        Check(Parse(Create() + Selection("Encode") + Selection("RawWithGzip")) == null, "multiple selections unknown");
        Check(Parse(Create() + Selection("Encode").Replace("ScreencapAgent.cpp", "Other.cpp")) == null, "different source unknown");
        Check(Parse(Selection("Encode").Replace("02:42:50", "02:42:48") + Create()) == null, "old selection unknown");
        Check(Parse(Selection("RawWithGzip") + Create()) == 4, "rotated segments ordered by timestamps");

        var directory = Path.Combine(fixtureDirectory, "capture-performance");
        Directory.CreateDirectory(directory);
        var display = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrMuMuDisplay+Mapping")!;
        object Mapping(int logical, string physical, string basis = "viewport_verified") =>
            Activator.CreateInstance(display, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                null, [logical, physical, basis], null)!;
        var mapping = Mapping(0, "0", "logical_default_verified");
        var displayType = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrMuMuDisplay")!;
        var options = displayType.GetMethod("StandardOptions", hidden)!;
        JObject Commands(object value) => (JObject)JObject.Parse((string)options.Invoke(null, [value])!)["command"]!;
        var defaults = Commands(mapping);
        Check((string?)defaults["ScreencapEncode"]?.Last == "screencap -p", "default encoded screenshot uses Android compatible command");
        Check((string?)defaults["ScreencapRawWithGzip"]?.Last == "screencap | gzip -1", "default gzip screenshot uses same default display");
        Check((string?)defaults["Click"]?.Last == "input tap {X} {Y}", "default logical input omits display flag");
        var mapped = Commands(Mapping(2, "42"));
        Check((string?)mapped["ScreencapEncode"]?.Last == "screencap -p -d 42", "encoded screenshot binds physical display");
        Check((string?)mapped["ScreencapRawWithGzip"]?.Last == "screencap -d 42 | gzip -1", "gzip screenshot binds same physical display");
        Check((string?)mapped["Click"]?.Last == "input -d 2 tap {X} {Y}", "input binds independent logical display");
        try { Commands(Mapping(2, "0")); throw new Exception("invalid screenshot mapping accepted"); }
        catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException) { count++; }
        var begin = type.GetMethod("Begin", hidden)!;
        var write = type.GetMethod("Write", BindingFlags.Instance | BindingFlags.NonPublic)!;
        foreach (var rotate in new[] { false, true })
        {
            var root = Path.Combine(directory, rotate ? "rotated" : "append");
            Directory.CreateDirectory(Path.Combine(root, "debug"));
            var log = Path.Combine(root, "debug", "maafw.log");
            File.WriteAllText(log, Create(Environment.ProcessId) + Selection("Encode", Environment.ProcessId));
            var invocation = Guid.NewGuid().ToString("N");
            var boundary = begin.Invoke(null, [root, invocation, "{}", mapping, "fixture"])!;
            File.AppendAllText(log, Create(Environment.ProcessId));
            if (rotate)
            {
                File.Move(log, Path.Combine(root, "debug", "maafw.bak.2026.10.16-02.42.50.958.log"));
                File.WriteAllText(log, "");
            }
            File.AppendAllText(log, Selection("RawWithGzip", Environment.ProcessId));
            write.Invoke(boundary, null);
            var report = JObject.Parse(File.ReadAllText(Path.Combine(root, ".runtime", "capture-performance", invocation + ".json")));
            Check((int?)report["evidence_version"] == 2 && (int?)report["actual_screencap_method"] == 4,
                "startup boundary excludes old selection across rotation");
            Check((string?)report["display_binding_basis"] == "logical_default_verified"
                && (string?)report["source_manifest_sha256"] == null
                && (string?)report["observation_window"] == "connection_only", "connection scope and missing manifest stay explicit");
            Check(report["native_log_segments"] is JArray segments && segments.Count == (rotate ? 2 : 1)
                && segments[0]?["sha256"]?.Value<string>()?.Length == 64, "native segment fingerprints");
            write.Invoke(boundary, null);
            Check(Directory.GetFiles(Path.Combine(root, ".runtime", "capture-performance")).Length == 1, "write is idempotent");
        }
        Console.WriteLine($"PCR capture performance checks passed: {count}; real device inputs=0");
    }
}
