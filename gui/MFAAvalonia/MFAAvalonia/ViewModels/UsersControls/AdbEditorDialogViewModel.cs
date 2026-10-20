using Avalonia;
using Avalonia.Controls;
using Avalonia.Data;
using Avalonia.Platform.Storage;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MaaFramework.Binding;
using MFAAvalonia.Extensions;
using MFAAvalonia.Helper;
using MFAAvalonia.Views.UserControls;
using MFAAvalonia.ViewModels.Pages;
using SukiUI.Dialogs;
using System;
using System.IO;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Threading.Tasks;

namespace MFAAvalonia.ViewModels.UsersControls;

public partial class AdbEditorDialogViewModel : ObservableObject
{
    [ObservableProperty] private string _adbName = LangKeys.Emulator.ToLocalization();
    [ObservableProperty] private string _adbPath = string.Empty;
    [ObservableProperty] private string _adbSerial = string.Empty;
    [ObservableProperty] private string _adbConfig = "{}";
    private readonly AdbScreencapMethods _screencapMethods;
    private readonly AdbInputMethods _adbInputMethods;
    private readonly TaskQueueViewModel? _targetViewModel;
    public bool IsPcrMode => MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled;
    public bool CanSaveTarget => !IsPcrMode || _targetViewModel?.CanEditPcrTarget == true;
    public ISukiDialog Dialog { get; set; }
    public AdbEditorDialogViewModel(AdbDeviceInfo? info, ISukiDialog dialog, TaskQueueViewModel? targetViewModel = null)
    {
        _targetViewModel = targetViewModel ?? Instances.InstanceTabBarViewModel.ActiveTab?.TaskQueueViewModel;
        if (_targetViewModel != null)
        {
            var weakEditor = new WeakReference<AdbEditorDialogViewModel>(this);
            System.ComponentModel.PropertyChangedEventHandler? handler = null;
            handler = (sender, args) =>
            {
                if (weakEditor.TryGetTarget(out var editor))
                {
                    if (args.PropertyName == nameof(TaskQueueViewModel.CanEditPcrTarget))
                        editor.OnPropertyChanged(nameof(CanSaveTarget));
                }
                else if (sender is TaskQueueViewModel owner) owner.PropertyChanged -= handler;
            };
            _targetViewModel.PropertyChanged += handler;
        }
        AdbName = info?.Name ?? AdbName;
        AdbPath = info?.AdbPath ?? AdbPath;
        AdbSerial = info?.AdbSerial ?? AdbSerial;
        AdbConfig = info?.Config ?? AdbConfig;
        _screencapMethods = info?.ScreencapMethods ?? AdbScreencapMethods.Default;
        _adbInputMethods = info?.InputMethods ?? AdbInputMethods.Maatouch;
        Dialog = dialog;
    }

    [RelayCommand]
    async private Task Load()
    {
        var storageProvider = Instances.StorageProvider;
        if (storageProvider == null)
        {
            ToastHelper.Warn(LangKeys.Warning.ToLocalization(), LangKeys.PlatformNotSupportedOperation.ToLocalization());
            return;
        }

        // 配置文件选择器选项
        var options = new FilePickerOpenOptions
        {
            Title = LangKeys.LoadFileTitle.ToLocalization(),
            FileTypeFilter =
            [
                new FilePickerFileType(LangKeys.AllFilter.ToLocalization())
                {
                    Patterns = ["*"] // 支持所有文件类型
                }
            ]
        };

        var result = await storageProvider.OpenFilePickerAsync(options);

        // 处理选择结果
        if (result is { Count: > 0 } && result[0].TryGetLocalPath() is { } path)
        {
            AdbPath = path;
        }
    }

    [RelayCommand]
    public void Save()
    {
        var vm = _targetViewModel;
        if (MFAAvalonia.Pcr.PcrAlphaPolicy.Enabled)
        {
            if (!TryNormalizePcrAdbTarget(AdbPath, AdbSerial, out var path, out var serial))
            {
                ToastHelper.Warn(LangKeys.Warning.ToLocalization(),
                    "请填写本机或局域网 IPv4:端口，或 emulator-串号，并选择本机已有 adb.exe 的完整路径。");
                return;
            }

            if (vm == null)
                return;

            AdbPath = path;
            AdbSerial = serial;
            if (!vm.SelectManualPcrAdbTarget(Output))
            {
                ToastHelper.Warn("目标未保存", "运行、连接或搜索中不能更换目标。");
                return;
            }
            Dialog.Dismiss();
            return;
        }

        if (vm != null)
        {
            vm.Devices = [Output];
            vm.CurrentDevice = Output;
        }

        Dialog.Dismiss();
    }

    public static bool TryNormalizePcrAdbTarget(string? path, string? serial,
        out string normalizedPath, out string normalizedSerial)
    {
        normalizedPath = string.Empty;
        normalizedSerial = string.Empty;
        var address = serial?.Trim();
        // Preserve the selected endpoint; LAN addresses do not prove local MuMu identity.
        var prefix = address?.StartsWith("emulator-", StringComparison.Ordinal) == true ? "emulator-" : null;
        if (prefix == null)
        {
            var separator = address?.LastIndexOf(':') ?? -1;
            if (separator < 1 || !IPAddress.TryParse(address![..separator], out var ip)
                || ip.AddressFamily != AddressFamily.InterNetwork || ip.ToString() != address[..separator])
                return false;
            var octets = ip.GetAddressBytes();
            if (address[..separator] != "127.0.0.1" && octets[0] != 10
                && !(octets[0] == 172 && octets[1] is >= 16 and <= 31)
                && !(octets[0] == 192 && octets[1] == 168))
                return false;
            prefix = address[..(separator + 1)];
        }
        if (!int.TryParse(address![prefix.Length..], NumberStyles.None,
                CultureInfo.InvariantCulture, out var port) || port is < 1 or > 65535
            || address != prefix + port.ToString(CultureInfo.InvariantCulture))
            return false;

        var candidate = path?.Trim().Replace('/', '\\');
        try
        {
            if (string.IsNullOrWhiteSpace(candidate)
                || !Path.IsPathFullyQualified(candidate)
                || candidate.Length < 3 || !char.IsLetter(candidate[0])
                || candidate[1] != ':' || candidate[2] != '\\'
                || !string.Equals(Path.GetFileName(candidate), "adb.exe", StringComparison.OrdinalIgnoreCase)
                || !File.Exists(candidate))
                return false;

            normalizedPath = Path.GetFullPath(candidate);
            normalizedSerial = address;
            return true;
        }
        catch (ArgumentException)
        {
            return false;
        }
    }

    public AdbDeviceInfo Output => new(AdbName, AdbPath, AdbSerial, _screencapMethods,
        _adbInputMethods, AdbConfig);
}
