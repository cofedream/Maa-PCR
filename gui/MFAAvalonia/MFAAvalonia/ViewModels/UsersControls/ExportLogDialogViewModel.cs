using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MFAAvalonia.Extensions;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels;
using SukiUI.Dialogs;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace MFAAvalonia.ViewModels.UsersControls;

public sealed partial class ExportLogDialogViewModel : ViewModelBase
{
    private readonly ISukiDialog _dialog;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(StartExportCommand))]
    private bool _includeMaaLog = true;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(StartExportCommand))]
    private bool _includeGuiLog = true;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(StartExportCommand))]
    private bool _includeCustomLog = true;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(StartExportCommand))]
    private bool _includeLabyrinthLog = true;

    [ObservableProperty] private ExportLogTimeRangeOption _selectedLabyrinthLogTimeRange;
    [ObservableProperty] private string _storageUsage = "正在统计日志占用…";

    public string LogRetentionDescription => "启动时清理超过 30 天的普通日志。GUI 日志还受原有大小与文件数上限约束。黎明界账本、检查点及独立运行证据保留。";
    public string LabyrinthLogDescription => "运行记录、账本和检查点完整附带；下方时间范围仅筛选原生日志。记录可能含玩家 ID、设备标识及游戏识别信息，请按需分享。";

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(StartExportCommand))]
    private bool _includeOnErrorImages = true;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(StartExportCommand))]
    private bool _includeVisionImages;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(StartExportCommand))]
    private bool _includeOtherImages = true;

    [ObservableProperty] private ExportLogTimeRangeOption _selectedOnErrorImageTimeRange;
    [ObservableProperty] private ExportLogTimeRangeOption _selectedVisionImageTimeRange;
    [ObservableProperty] private ExportLogTimeRangeOption _selectedOtherImageTimeRange;
    [ObservableProperty] private bool _isExporting;

    public IReadOnlyList<ExportLogTimeRangeOption> ImageTimeRangeOptions { get; }

    public string MaaLogLabel => "maa.log";
    public string GuiLogLabel => "gui.log";
    public string CustomLogLabel => "custom.log";
    public string OnErrorFolderLabel => "on_error";
    public string VisionFolderLabel => "vision";
    public string StartExportLabel => IsExporting
        ? LangKeys.ExportLogInProgress.ToLocalization()
        : LangKeys.ExportLogDialogStart.ToLocalization();

    public bool CanStartExport =>
        !IsExporting && (IncludeMaaLog || IncludeGuiLog || IncludeCustomLog || IncludeLabyrinthLog || IncludeOnErrorImages || IncludeVisionImages || IncludeOtherImages);

    public ExportLogDialogViewModel(ISukiDialog dialog)
    {
        _dialog = dialog;

        ImageTimeRangeOptions =
        [
            new ExportLogTimeRangeOption(ExportLogTimeRange.All, LangKeys.AllFilter.ToLocalization()),
            new ExportLogTimeRangeOption(ExportLogTimeRange.Last24Hours, LangKeys.ExportLogTimeRangeLast24Hours.ToLocalization()),
            new ExportLogTimeRangeOption(ExportLogTimeRange.Last3Days, LangKeys.ExportLogTimeRangeLast3Days.ToLocalization()),
            new ExportLogTimeRangeOption(ExportLogTimeRange.Last7Days, LangKeys.ExportLogTimeRangeLast7Days.ToLocalization())
        ];

        _selectedOnErrorImageTimeRange = ImageTimeRangeOptions[0];
        _selectedVisionImageTimeRange = ImageTimeRangeOptions[0];
        _selectedOtherImageTimeRange = ImageTimeRangeOptions[0];
        _selectedLabyrinthLogTimeRange = ImageTimeRangeOptions[3];
        _ = RefreshStorageUsage();
    }

    private async Task RefreshStorageUsage()
    {
        try
        {
            var usage = await Task.Run(() => PcrLogFiles.Usage(AppPaths.DataRoot));
            StorageUsage = $"普通日志：{usage.Ordinary / 1048576d:F1} MiB；黎明界独立记录：{usage.Labyrinth / 1048576d:F1} MiB（压缩前，不含截图）。";
        }
        catch { StorageUsage = "日志占用统计未完成，部分文件无法访问。"; }
    }

    partial void OnIsExportingChanged(bool value) => OnPropertyChanged(nameof(StartExportLabel));

    [RelayCommand(CanExecute = nameof(CanStartExport))]
    private async Task StartExport()
    {
        if (IsExporting)
            return;

        IsExporting = true;
        StartExportCommand.NotifyCanExecuteChanged();

        try
        {
            var result = await FileLogExporter.CompressRecentLogs(Instances.StorageProvider, BuildOptions());
            if (result == ExportLogResult.Success)
            {
                _dialog.Dismiss();
            }
        }
        finally
        {
            IsExporting = false;
            StartExportCommand.NotifyCanExecuteChanged();
        }
    }

    [RelayCommand]
    private void Close()
    {
        if (!IsExporting)
            _dialog.Dismiss();
    }

    private ExportLogPackageOptions BuildOptions()
    {
        return new ExportLogPackageOptions
        {
            IncludeMaaLog = IncludeMaaLog,
            IncludeGuiLog = IncludeGuiLog,
            IncludeCustomLog = IncludeCustomLog,
            IncludeLabyrinthLog = IncludeLabyrinthLog,
            LabyrinthLogTimeRange = SelectedLabyrinthLogTimeRange.Value,
            IncludeOnErrorImages = IncludeOnErrorImages,
            IncludeVisionImages = IncludeVisionImages,
            IncludeOtherImages = IncludeOtherImages,
            OnErrorImageTimeRange = SelectedOnErrorImageTimeRange.Value,
            VisionImageTimeRange = SelectedVisionImageTimeRange.Value,
            OtherImageTimeRange = SelectedOtherImageTimeRange.Value
        };
    }
}

public sealed class ExportLogTimeRangeOption(ExportLogTimeRange value, string displayName)
{
    public ExportLogTimeRange Value { get; } = value;
    public string DisplayName { get; } = displayName;
}
