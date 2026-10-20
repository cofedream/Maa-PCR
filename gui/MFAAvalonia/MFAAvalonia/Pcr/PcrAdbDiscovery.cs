using System;
using System.Collections.Generic;
using System.IO;
using MFAAvalonia.Helper;
using System.Threading;
using System.Threading.Tasks;
using MaaFramework.Binding;
using MFAAvalonia.ViewModels.UsersControls;

namespace MFAAvalonia.Pcr;

/// <summary>Explicit, single-flight discovery. Cancellation discards results; native Find
/// cannot be interrupted and may query/connect ADB or start its server.</summary>
internal sealed class PcrAdbDiscovery
{
    private int _busy;
    private int _generation;
    public bool IsBusy => Volatile.Read(ref _busy) != 0;
    public void Cancel() => Interlocked.Increment(ref _generation);

    public async Task<IReadOnlyList<AdbDeviceInfo>?> SearchAsync(
        Func<IReadOnlyList<AdbDeviceInfo>> find, CancellationToken token = default)
    {
        if (Interlocked.CompareExchange(ref _busy, 1, 0) != 0) return null;
        var generation = Volatile.Read(ref _generation);
        try
        {
            token.ThrowIfCancellationRequested();
            var devices = await Task.Run(find, token);
            return token.IsCancellationRequested || generation != Volatile.Read(ref _generation)
                ? null : devices;
        }
        finally { Interlocked.Exchange(ref _busy, 0); }
    }

    public static bool SameTarget(AdbDeviceInfo? left, AdbDeviceInfo? right) =>
        left != null && right != null
        && string.Equals(left.AdbPath.Replace('/', '\\'), right.AdbPath.Replace('/', '\\'), StringComparison.OrdinalIgnoreCase)
        && string.Equals(left.AdbSerial, right.AdbSerial, StringComparison.Ordinal);

    public static AdbDeviceInfo? AutoSelect(IReadOnlyList<AdbDeviceInfo> devices, object? selected) =>
        selected == null && devices.Count == 1 && PcrAlphaPolicy.IsSupportedAdbTarget(devices[0])
            ? devices[0] : null;

    public static AdbDeviceInfo Describe(AdbDeviceInfo device)
    {
        var valid = AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(device.AdbPath, device.AdbSerial,
            out var path, out var serial);
        var mapped = PcrAlphaPolicy.TryMapFinderTarget(device, out var mappedPath);
        if (mapped) path = mappedPath;
        var supported = valid && (mapped || PcrAlphaPolicy.IsSupportedAdbTarget(device));
        var label = !valid ? "路径或地址无效"
            : mapped ? "MuMu" : supported ? "ADB" : "连接不支持";
        // Keep the raw Finder object unchanged and its source path in the label.
        // Standard Owner transport must never inherit Finder EmulatorExtras.
        return new AdbDeviceInfo($"【{label}】{device.Name}", valid ? path : device.AdbPath,
            valid ? serial : device.AdbSerial,
            supported ? AdbScreencapMethods.Encode : device.ScreencapMethods,
            supported ? AdbInputMethods.AdbShell : device.InputMethods, supported ? "{}" : device.Config);
    }

    // A preview uses Maa's native connection and screencap implementation. It
    // has no input capability and loads no business resources or Python Agent.
    public static MaaController CreatePreviewController(AdbDeviceInfo device)
    {
        if (!AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(device.AdbPath, device.AdbSerial,
                out var path, out var serial))
            throw new InvalidOperationException("请选择本机已有的 adb.exe 和有效的本机或局域网 IPv4:端口，或 emulator-串号。");
        return new MaaAdbController(path, serial, AdbScreencapMethods.Encode, AdbInputMethods.None,
            "{}", Path.Combine(AppPaths.InstallRoot, "libs", "MaaAgentBinary"),
            LinkOption.None, CheckStatusOption.None);
    }
}
