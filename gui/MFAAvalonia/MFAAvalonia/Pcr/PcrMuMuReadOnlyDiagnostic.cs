using System;
using System.IO;
using System.Linq;
using System.Threading;
using MaaFramework.Binding;
using MaaFramework.Binding.Buffers;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

/// <summary>Explicit, single-use diagnostic. No Tasker, Agent, input or production
/// qualification. A frame is an observation, not proof of package/display identity.</summary>
internal sealed class PcrMuMuReadOnlyDiagnostic
{
    private readonly string adb, serial, options;
    private readonly Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? offlineFactory;
    private int used, invalidated;

    internal PcrMuMuReadOnlyDiagnostic(JObject data,
        Func<string, string, AdbScreencapMethods, AdbInputMethods, string, MaaCustomController>? offlineFactory)
    {
        // This separate schema never reads or rewrites the business configuration.
        // Copy stable values now; subsequent edits of the supplied JObject cannot
        // retarget a prepared session. No persistent logical/physical display ID.
        Fields(data, "connection", "mumu");
        var connection = data["connection"] as JObject ?? throw new InvalidOperationException("connection missing");
        var mumu = data["mumu"] as JObject ?? throw new InvalidOperationException("mumu missing");
        Fields(connection, "adb_path", "serial");
        Fields(mumu, "path", "lib", "index", "app_package", "app_cloned_index");
        adb = Text(connection, "adb_path"); serial = Text(connection, "serial");
        if (!Path.IsPathFullyQualified(adb) || !File.Exists(adb) ||
            !string.Equals(Path.GetFileName(adb), "adb.exe", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Explicit local adb.exe missing");
        const string prefix = "127.0.0.1:";
        var port = serial.StartsWith(prefix, StringComparison.Ordinal) ? serial[prefix.Length..] : "";
        if (port.Length == 0 || port.Any(c => c < '0' || c > '9') ||
            !int.TryParse(port, out var number) || number < 1 || number > 65535 || number.ToString() != port)
            throw new InvalidOperationException("Expected a canonical local MuMu 127.0.0.1:port address");
        options = PcrMuMuDisplay.Options(Text(mumu, "path"), Text(mumu, "lib"),
            Index(mumu, "index"), Index(mumu, "app_cloned_index"), Text(mumu, "app_package"));
        this.offlineFactory = offlineFactory;
    }

    private static void Fields(JObject obj, params string[] names)
    {
        if (obj.Properties().Any(p => !names.Contains(p.Name)) || names.Any(n => obj[n] == null))
            throw new InvalidOperationException("Missing/unknown diagnostic configuration field");
    }
    private static string Text(JObject obj, string key) => obj[key]?.Type == JTokenType.String &&
        !string.IsNullOrWhiteSpace((string?)obj[key]) ? (string)obj[key]! : throw new InvalidOperationException(key + " missing");
    private static int Index(JObject obj, string key)
    {
        if (obj[key]?.Type != JTokenType.Integer || !int.TryParse(obj[key]!.ToString(), out var value) || value < 0)
            throw new InvalidOperationException(key + " invalid");
        return value;
    }

    // The future controlled caller must invalidate on a known reconnect or display
    // relocation. This is not automatic detection of vendor cache/focus changes.
    internal void Invalidate() => Interlocked.Exchange(ref invalidated, 1);
    private void RequireValid()
    {
        if (Volatile.Read(ref invalidated) != 0) throw new InvalidOperationException("Diagnostic session invalidated; no reconnect/retry");
    }

    // Caller owns the returned image; controller is always closed before return.
    internal MaaImageBuffer Capture()
    {
        if (Interlocked.Exchange(ref used, 1) != 0) throw new InvalidOperationException("Diagnostic is single-use; no reconnect/retry");
        RequireValid();
        // In this installed .NET binding native Null (0) is named None.
        using MaaController controller = offlineFactory != null
            ? offlineFactory(adb, serial, AdbScreencapMethods.EmulatorExtras, AdbInputMethods.None, options)
                ?? throw new InvalidOperationException("Offline factory returned null")
            : new MaaAdbController(adb, serial, AdbScreencapMethods.EmulatorExtras,
                AdbInputMethods.None, options, Path.Combine(AppContext.BaseDirectory, "libs", "MaaAgentBinary"));
        if (!controller.SetOption_ScreenshotUseRawSize(true)) throw new InvalidOperationException("Raw screenshot option failed");
        RequireValid();
        if (controller.LinkStart().Wait() != MaaJobStatus.Succeeded || !controller.IsConnected)
            throw new InvalidOperationException("Read-only connection failed");
        RequireValid();
        if (controller.Screencap().Wait() != MaaJobStatus.Succeeded || !controller.IsConnected)
            throw new InvalidOperationException("Read-only screenshot failed/disconnected");
        RequireValid();
        var image = new MaaImageBuffer();
        try
        {
            if (!controller.GetCachedImage(image) || image.IsEmpty || image.Width <= 0 || image.Height <= 0)
                throw new InvalidOperationException("Read-only raw frame unavailable");
            RequireValid();
            return image;
        }
        catch { image.Dispose(); throw; }
    }
}
