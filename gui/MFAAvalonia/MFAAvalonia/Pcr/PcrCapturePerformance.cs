using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// Independent diagnostics only. A missing report never changes business authority.
internal sealed class PcrCapturePerformance : IDisposable
{
    private sealed record Log(FileStream Stream, string Identity, string Path, long Start);
    private readonly Dictionary<string, Log> logs = new();
    private readonly string root, invocation, optionsHash, basis, serial;
    private readonly string[] directories;
    private readonly int logical;
    private readonly string physical;
    private bool failed, written;
    private const int MaxBytes = 32 * 1024 * 1024;
    private static readonly Regex LogName = new(@"^(?:maafw\.log|maafw\.bak\.\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}\.\d{1,3}\.log)$", RegexOptions.IgnoreCase);

    private PcrCapturePerformance(string root, string invocation, string options, PcrMuMuDisplay.Mapping mapping, string serial)
    {
        this.root = root; this.invocation = invocation; this.serial = serial;
        optionsHash = Hash(Encoding.UTF8.GetBytes(options));
        basis = mapping.Basis; logical = mapping.Logical; physical = mapping.Physical;
        directories = new[] { Path.Combine(root, "debug"), Path.Combine(AppContext.BaseDirectory, "debug"),
            Path.Combine(Helper.AppPaths.DataRoot, "debug"), Path.Combine(Helper.AppPaths.LogsDirectory, "log_screencap") }
            .Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        try { Discover(true); } catch { failed = true; }
    }

    internal static PcrCapturePerformance? Begin(string root, string invocation, string options, PcrMuMuDisplay.Mapping mapping, string serial)
    {
        try { return new(root, invocation, options, mapping, serial); }
        catch { return null; }
    }

    private static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    [StructLayout(LayoutKind.Sequential)]
    private struct FileInformation
    {
        public uint Attributes, CreationLow, CreationHigh, AccessLow, AccessHigh, WriteLow, WriteHigh,
            Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileInformation information);

    private void Discover(bool snapshot)
    {
        foreach (var directory in directories)
        {
            if (!Directory.Exists(directory)) continue;
            foreach (var path in Directory.EnumerateFiles(directory).Where(p => LogName.IsMatch(Path.GetFileName(p))))
            {
                if (!PcrLogFiles.IsRegularPath(directory, path)) { failed = true; continue; }
                var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                if (!GetFileInformationByHandle(stream.SafeFileHandle, out var id)) { stream.Dispose(); failed = true; continue; }
                var identity = $"{id.Volume:x8}:{id.IndexHigh:x8}{id.IndexLow:x8}";
                if (logs.ContainsKey(identity)) { stream.Dispose(); continue; }
                logs.Add(identity, new(stream, identity, path, snapshot ? stream.Length : 0));
            }
        }
    }

    // Maa 5.13 emits this exact selection after connection speed testing.
    // Require one controller creation in this process and this startup window.
    internal static int? SelectedMethod(string text, int processId, string expectedSerial)
    {
        var prefix = @"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]\[[A-Z]+\]\[Px" + processId + @"\][^\r\n]*";
        Match[] Lines(string tail) => Regex.Matches(text, prefix + tail, RegexOptions.Multiline,
            TimeSpan.FromSeconds(1)).Cast<Match>().ToArray();
        var created = Lines(@"\[MaaAdbControllerCreate\][^\r\n]*\| enter[^\S\r\n]*$");
        if (created.Length != 1 || !Regex.IsMatch(created[0].Value, @"\[address=" + Regex.Escape(expectedSerial)
            + @"\] \[screencap_methods=6\] \[input_methods=1\]", RegexOptions.None, TimeSpan.FromSeconds(1))) return null;
        var selected = Lines(@"\[ScreencapAgent\.cpp\]\[L\d+\]\[MaaNS::CtrlUnitNs::ScreencapAgent::speed_test\] The fastest method is [^\r\n]*$");
        if (selected.Length != 1 || string.CompareOrdinal(selected[0].Value[..25], created[0].Value[..25]) < 0) return null;
        var method = Regex.Match(selected[0].Value,
            @" The fastest method is (Encode|RawWithGzip) \[cost=\d+ms\][^\S\r\n]*$", RegexOptions.None, TimeSpan.FromSeconds(1));
        return method.Success ? method.Groups[1].Value == "Encode" ? 2 : 4 : null;
    }

    internal void Write()
    {
        if (written) return;
        written = true;
        try
        {
            Discover(false);
            var segments = new JArray(); var text = new StringBuilder();
            foreach (var log in logs.Values)
            {
                var length = log.Stream.Length - log.Start;
                if (length < 0 || length > MaxBytes || text.Length + length > MaxBytes) { failed = true; continue; }
                if (length == 0) continue;
                log.Stream.Position = log.Start;
                var bytes = new byte[(int)length]; log.Stream.ReadExactly(bytes);
                // Only complete native records can identify the selected backend.
                var complete = Array.LastIndexOf(bytes, (byte)'\n') + 1;
                if (complete != bytes.Length) failed = true;
                text.Append(Encoding.UTF8.GetString(bytes, 0, complete));
                segments.Add(new JObject { ["file"] = Path.GetFileName(log.Path), ["file_identity"] = log.Identity,
                    ["start_offset"] = log.Start, ["end_offset"] = log.Start + length, ["sha256"] = Hash(bytes) });
            }
            var actual = failed ? null : SelectedMethod(text.ToString(), Environment.ProcessId, serial);
            var manifest = Path.Combine(root, "package-manifest.json");
            var report = new JObject { ["evidence_version"] = 2, ["purpose"] = "capture_performance_only",
                ["run_id"] = invocation, ["invocation_id"] = invocation, ["process_id"] = Environment.ProcessId,
                ["requested_screencap_methods"] = 6, ["input_methods"] = 1, ["scope"] = "formal",
                ["actual_screencap_method"] = actual, ["actual_backend"] = actual == 2 ? "Encode" : actual == 4 ? "RawWithGzip" : null,
                ["observation"] = actual == null ? "unknown" : "observed", ["observation_window"] = "connection_only",
                ["options_sha256"] = optionsHash, ["options_encoding"] = "utf8_json",
                ["display_binding_basis"] = basis, ["logical_input_display"] = logical, ["physical_screenshot_display"] = physical,
                ["source_manifest_sha256"] = File.Exists(manifest) ? Hash(File.ReadAllBytes(manifest)) : null,
                ["native_log_segments"] = segments };
            var directory = Path.Combine(root, ".runtime", "capture-performance");
            var parent = Directory.Exists(directory) ? directory : Directory.Exists(Path.GetDirectoryName(directory))
                ? Path.GetDirectoryName(directory)! : root;
            if (!PcrLogFiles.IsRegularPath(root, parent)) return;
            Directory.CreateDirectory(directory);
            if (!PcrLogFiles.IsRegularPath(root, directory)) return;
            var target = Path.Combine(directory, invocation + ".json");
            var temporary = target + ".tmp";
            File.WriteAllText(temporary, report.ToString(Formatting.Indented), new UTF8Encoding(false));
            File.Move(temporary, target, false);
        }
        catch { /* Performance evidence must not affect the business result. */ }
        finally { Dispose(); }
    }

    public void Dispose()
    {
        foreach (var log in logs.Values) { try { log.Stream.Dispose(); } catch { } }
        logs.Clear();
    }
}
