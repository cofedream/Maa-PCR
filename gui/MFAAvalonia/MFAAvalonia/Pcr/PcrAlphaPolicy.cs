using System;
using System.Collections.Generic;
using System.IO;
using MaaFramework.Binding;
using MFAAvalonia.Configuration;

namespace MFAAvalonia.Pcr;

/// <summary>Product policy, not a preference and never a saved execution grant.</summary>
public static class PcrAlphaPolicy
{
    public static bool Enabled => true;
    // The Finder can return nx_main/adb.exe. Resolve its standard shell ADB
    // within that same installation, never to another player's fixed path.
    internal static bool TryMapFinderTarget(AdbDeviceInfo device, out string path)
    {
        path = "";
        if (!MFAAvalonia.ViewModels.UsersControls.AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(
                device.AdbPath, device.AdbSerial, out var finder, out _)) return false;
        var directory = Path.GetDirectoryName(finder)!;
        if (!string.Equals(Path.GetFileName(directory), "nx_main", StringComparison.OrdinalIgnoreCase)) return false;
        var installation = Path.GetDirectoryName(directory)!;
        var candidate = Path.Combine(installation,"nx_device","12.0","shell","adb.exe");
        if (!File.Exists(candidate)) return false;
        path = Path.GetFullPath(candidate);
        return true;
    }

    // Saved/selected transport only. Owner still verifies the actual PCR display,
    // exact binding, this Start and pending intent before business input.
    public static bool IsSupportedAdbTarget(AdbDeviceInfo device) =>
        MFAAvalonia.ViewModels.UsersControls.AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(
            device.AdbPath, device.AdbSerial, out _, out _);

    public static readonly HashSet<string> Disabled = new()
    {
        ConfigurationKeys.HelpImproveSoftware, ConfigurationKeys.GlobalStartEnabled,
        ConfigurationKeys.EnableCheckVersion, ConfigurationKeys.EnableAutoUpdateResource,
        ConfigurationKeys.EnableAutoUpdateMFA, ConfigurationKeys.AutoConnectAfterRefresh,
        ConfigurationKeys.AutoDetectOnConnectionFailed, ConfigurationKeys.UseSeparateScreenshotTasker,
        ConfigurationKeys.ContinueRunningWhenError,
        ConfigurationKeys.AllowAdbRestart, ConfigurationKeys.AllowAdbHardRestart, ConfigurationKeys.RetryOnDisconnected,
        ConfigurationKeys.RetryOnDisconnectedWin32
    };
    public static string? Value(string key) => Disabled.Contains(key) ? bool.FalseString
        : key == ConfigurationKeys.NoAutoStart || key == ConfigurationKeys.AgentTcpMode ? bool.TrueString
        : key == ConfigurationKeys.BeforeTask || key == ConfigurationKeys.AfterTask ? "None"
        : key == ConfigurationKeys.Prescript || key == ConfigurationKeys.Postscript ? "" : null;
}
