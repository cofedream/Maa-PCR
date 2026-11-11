using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

/// <summary>
/// Offline checks for the fixed direct policy of the game-protocol children and
/// for the native environment block that carries it. No device, network or game
/// input is used; the child is a short-lived local interpreter probe.
/// </summary>
internal static class NetworkProxyChecks
{
    internal static int Run()
    {
        var count = 0;
        void Require(bool value, string test)
        {
            if (!value) throw new InvalidOperationException(test);
            count++;
        }

        var assembly = typeof(MFAAvalonia.Pcr.PcrAlphaOwner).Assembly;
        var policy = assembly.GetType("MFAAvalonia.Pcr.PcrNetworkProxy", true)!;
        var direct = policy.GetMethod("DirectEnvironment", BindingFlags.Static | BindingFlags.NonPublic)!;
        var overrides = (IReadOnlyDictionary<string, string?>)direct.Invoke(null, null)!;

        // The protocol children must never inherit a desktop proxy.
        Require(overrides["NO_PROXY"] == "*", "protocol_direct_forces_full_bypass");
        Require(overrides["HTTP_PROXY"] is null && overrides["HTTPS_PROXY"] is null && overrides["ALL_PROXY"] is null,
            "protocol_direct_removes_inherited_proxies");
        Require(overrides.Count == 4, "protocol_direct_is_the_complete_policy");

        // The native block must be a real Unicode environment list, not one string.
        var child = assembly.GetType("MFAAvalonia.Pcr.PcrWindowsChild", true)!;
        var build = child.GetMethod("BuildEnvironmentBlock", BindingFlags.Static | BindingFlags.NonPublic)!;
        Environment.SetEnvironmentVariable("HTTP_PROXY", "http://127.0.0.1:7890");
        IntPtr block = IntPtr.Zero;
        try
        {
            var blockOverrides = new Dictionary<string, string?>(overrides, StringComparer.OrdinalIgnoreCase)
            {
                ["MAA_PCR_TEST_VARIABLE"] = "value"
            };
            block = (IntPtr)build.Invoke(null, new object[] { blockOverrides })!;
            var entries = ReadBlock(block);
            Require(IsNonZero(block) && entries.Count > 1, "environment_block_is_unicode_list");
            Require(entries.Exists(e => e == "NO_PROXY=*"), "environment_block_keeps_direct_bypass");
            Require(entries.Exists(e => e == "MAA_PCR_TEST_VARIABLE=value"), "environment_block_adds_override");
            Require(!entries.Exists(e => e.StartsWith("HTTP_PROXY=", StringComparison.OrdinalIgnoreCase)),
                "environment_block_removes_inherited_proxy");
        }
        finally
        {
            if (IsNonZero(block)) Marshal.FreeHGlobal(block);
            Environment.SetEnvironmentVariable("HTTP_PROXY", null);
        }

        // End-to-end: the real CreateProcessW path must deliver the block to a child.
        // verify.py supplies its development interpreter; a manual run without it skips this.
        var python = Environment.GetEnvironmentVariable("PCR_STARTUP_TEST_PYTHON");
        if (OperatingSystem.IsWindows() && !string.IsNullOrWhiteSpace(python) && File.Exists(python))
        {
            var probe = Path.Combine(Path.GetTempPath(), "maa-np-probe-" + Guid.NewGuid().ToString("N") + ".txt");
            object? live = null;
            Environment.SetEnvironmentVariable("HTTP_PROXY", "http://127.0.0.1:7890");
            try
            {
                live = Activator.CreateInstance(child, true)!;
                var script = "import os,sys; open(sys.argv[1],'w',encoding='utf-8').write('|'.join(["
                    + "os.environ.get('MAA_PCR_PROBE',''),os.environ.get('NO_PROXY',''),os.environ.get('HTTP_PROXY','')])); sys.stdin.readline()";
                var childOverrides = new Dictionary<string, string?>(overrides, StringComparer.OrdinalIgnoreCase)
                {
                    ["MAA_PCR_PROBE"] = "inherited-into-child"
                };
                child.GetMethod("StartWithEnvironment", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(
                    live, new object?[] { python, Path.GetDirectoryName(python)!, childOverrides, new[] { "-c", script, probe } });
                string? content = null;
                var deadline = Stopwatch.StartNew();
                while (deadline.Elapsed < TimeSpan.FromSeconds(10))
                {
                    if (File.Exists(probe))
                    {
                        try { content = File.ReadAllText(probe); } catch (IOException) { content = null; }
                        if (!string.IsNullOrEmpty(content)) break;
                    }
                    Thread.Sleep(20);
                }
                Require(content is not null && content.StartsWith("inherited-into-child|*|", StringComparison.Ordinal),
                    "protocol_child_receives_direct_environment");
            }
            finally
            {
                if (live is IDisposable disposable) disposable.Dispose();
                try { File.Delete(probe); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                Environment.SetEnvironmentVariable("HTTP_PROXY", null);
            }
        }

        return count;
    }

    private static bool IsNonZero(IntPtr value) => value != IntPtr.Zero;

    private static List<string> ReadBlock(IntPtr block)
    {
        var entries = new List<string>();
        var current = new StringBuilder();
        for (var offset = 0; ; offset += 2)
        {
            var c = (char)Marshal.ReadInt16(block, offset);
            if (c != '\0')
            {
                current.Append(c);
                continue;
            }
            if (current.Length == 0) return entries;
            entries.Add(current.ToString());
            current.Clear();
        }
    }
}
