using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.Controls.Templates;
using Avalonia.Data;
using Avalonia.Layout;
using Avalonia.Platform.Storage;
using MFAAvalonia.Extensions;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;
using SukiUI.Controls;

namespace MFAAvalonia.Pcr;

internal static class PcrFeedbackActions
{
    // Test seam: opening a compose window is not a submission.
    internal static Action<string> Open = value => Process.Start(new ProcessStartInfo(value) { UseShellExecute = true });
    internal static Func<IStorageProvider,FilePickerSaveOptions,Task<IStorageFile?>> PickFile = (provider,options) => provider.SaveFilePickerAsync(options);
    internal static Func<string,Task>? CopyText = null;
    internal static Action<Window>? WindowOpened = null;
}

public sealed class PcrLiveLogView : UserControl
{
    private readonly ScrollViewer scroll;
    private readonly Border body;
    public PcrLiveLogView()
    {
        var root = new StackPanel { Spacing = 6 };
        var tools = new WrapPanel { Orientation = Orientation.Horizontal };
        var toggle = new ToggleButton { Name = "PlayerLogToggle", Content = "实时日志", Padding = new Thickness(10,4) };
        tools.Children.Add(toggle);
        var clear = Tool("清空", () => { if (DataContext is TaskQueueViewModel vm) vm.LabyrinthLogRows.Clear(); });
        tools.Children.Add(clear);
        tools.Children.Add(Tool("到底部", () => ScrollViewerExtensions.ScrollToEndAndResumeAutoScroll(scroll!)));
        var export = Tool("导出本次日志", () => ShowExport(false));
        export.Bind(IsEnabledProperty, new Binding(nameof(TaskQueueViewModel.CanExportPlayerLog)));
        tools.Children.Add(export);
        var feedback = Tool("反馈问题", () => ShowExport(true));
        feedback.Bind(IsVisibleProperty, new Binding(nameof(TaskQueueViewModel.PcrHasStartError)));
        feedback.Bind(IsEnabledProperty, new Binding(nameof(TaskQueueViewModel.CanExportPlayerLog)));
        tools.Children.Add(feedback);
        root.Children.Add(tools);
        var rows = new ItemsControl();
        rows.Bind(ItemsControl.ItemsSourceProperty, new Binding(nameof(TaskQueueViewModel.LabyrinthLogRows)));
        rows.ItemTemplate = new FuncDataTemplate<TaskQueueViewModel.LabyrinthLogRow>((row, _) =>
        {
            var grid = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*"), ColumnSpacing = 6, Margin = new Thickness(0,2) };
            grid.Children.Add(new SelectableTextBlock { Text = row?.Time, FontSize = 12, MinWidth = 58 });
            var text = new SelectableTextBlock { Text = row?.Content, FontSize = 12, TextWrapping = Avalonia.Media.TextWrapping.WrapWithOverflow };
            Grid.SetColumn(text, 1); grid.Children.Add(text); return grid;
        });
        scroll = new ScrollViewer { Name = "PlayerLogScroll", Height = 170, Content = rows,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        ScrollViewerExtensions.SetAutoScroll(scroll, true);
        body = new Border { IsVisible = false, Padding = new Thickness(6,4), Child = scroll };
        toggle.IsCheckedChanged += (_, _) =>
        {
            body.IsVisible = toggle.IsChecked == true;
            if (body.IsVisible) Avalonia.Threading.Dispatcher.UIThread.Post(() => ScrollViewerExtensions.ScrollToEndAndResumeAutoScroll(scroll));
        };
        root.Children.Add(body); Content = root;
    }
    private static Button Tool(string text, Action action)
    {
        var button = new Button { Content = text, FontSize = 12, Padding = new Thickness(10,4), Margin = new Thickness(0,0,6,0) };
        Avalonia.Automation.AutomationProperties.SetName(button, text);
        button.Click += (_, _) => action(); return button;
    }
    internal void ShowExport(bool feedback)
    {
        if (DataContext is not TaskQueueViewModel vm || !vm.CanExportPlayerLog) return;
        PcrPlayerRunLog? log;
        try { log = vm.PlayerLog; } catch { log = null; }
        if (log == null) { vm.AddPcrLiveLog("没有本功能可导出的运行记录，请先运行一次任务。"); return; }
        var reason = vm.PcrStartError.Length > 0 ? vm.PcrStartError : vm.PcrRunSummary;
        var status = new TextBlock { TextWrapping = Avalonia.Media.TextWrapping.Wrap };
        var panel = new StackPanel { Spacing = 10, Margin = new Thickness(18) };
        panel.Children.Add(new TextBlock { Text = $"{log.Title} · 运行 {log.RunId}\n仅导出本功能、本次运行，ZIP 上限 16 MiB。", TextWrapping = Avalonia.Media.TextWrapping.Wrap });
        var channels = new WrapPanel();
        panel.Children.Add(channels);
        panel.Children.Add(new TextBlock { Text = "先保存日志包，再把 ZIP 拖到 GitHub 反馈页或添加到邮件。截图可能含昵称或玩家 ID，请检查勾选的图片。", TextWrapping = Avalonia.Media.TextWrapping.Wrap });
        var choices = log.Screenshots().Select(path => (Path: path, Box: new CheckBox { Content = Path.GetFileName(path), IsChecked = true })).ToArray();
        foreach (var choice in choices)
        {
            var line = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            line.Children.Add(choice.Box);
            line.Children.Add(Tool("预览截图", () => { try { PcrFeedbackActions.Open(choice.Path); } catch { status.Text = "截图暂时无法打开，可以取消勾选后继续导出。"; } }));
            panel.Children.Add(line);
        }
        if (choices.Length == 0) panel.Children.Add(new TextBlock { Text = "本次没有可分享的截图，原因见日志。" });
        string? savedPath = null;
        string[]? preparedPictures = null;
        var busy = false;
        async Task<bool> Prepare()
        {
            if (busy) return false;
            busy = true;
            try
            {
                var selected = choices.Where(c => c.Box.IsChecked == true).Select(c => c.Path).ToArray();
                if (savedPath != null && File.Exists(savedPath) && preparedPictures != null && selected.SequenceEqual(preparedPictures)) return true;
                var provider = TopLevel.GetTopLevel(this)?.StorageProvider ?? Instances.StorageProvider;
                if (provider == null) throw new IOException("文件保存窗口不可用。");
                var target = await PcrFeedbackActions.PickFile(provider, new FilePickerSaveOptions { Title = "保存本次功能日志",
                    SuggestedFileName = $"Maa-PCR-{log.Function}-{log.RunId[..8]}.zip", DefaultExtension = "zip" });
                if (target == null) return false;
                var bytes = await Task.Run(() => log.Export(selected));
                await using var output = await target.OpenWriteAsync();
                output.SetLength(0); await output.WriteAsync(bytes); await output.FlushAsync();
                savedPath = target.TryGetLocalPath();
                preparedPictures = selected;
                status.Text = "日志包已准备，尚未发送。请在 Issue 或邮件中附上 ZIP。";
                return true;
            }
            catch (Exception ex) { status.Text = "日志包准备失败：" + ex.Message; return false; }
            finally { busy = false; }
        }
        var actions = new WrapPanel { Orientation = Orientation.Horizontal };
        var save = Tool("保存日志包", () => { _ = Prepare(); }); actions.Children.Add(save);
        channels.Children.Add(Tool("打开 GitHub 反馈页", () => { _ = OpenFeedback(log.GitHubLink(reason)); }));
        channels.Children.Add(Tool("写反馈邮件", () => { _ = OpenFeedback(log.MailLink(reason)); }));
        channels.Children.Add(new SelectableTextBlock { Text = "反馈邮箱：" + PcrPlayerRunLog.FeedbackEmail,
            VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(4,0,8,0) });
        channels.Children.Add(Tool("复制反馈邮箱", () => { _ = Copy(PcrPlayerRunLog.FeedbackEmail); }));
        async Task OpenFeedback(string uri)
        {
            if (!await Prepare()) return;
            try { PcrFeedbackActions.Open(uri); }
            catch { status.Text = "未能打开浏览器或邮件客户端。请使用下面的复制按钮，并手动附上日志包。"; }
        }
        actions.Children.Add(Tool("复制反馈说明", () => { _ = Copy(log.FeedbackText(reason)); }));
        actions.Children.Add(Tool("打开日志包位置", () =>
        {
            if (savedPath == null) { status.Text = "请先保存日志包。"; return; }
            try { PcrFeedbackActions.Open(Path.GetDirectoryName(savedPath)!); } catch { status.Text = "无法打开文件夹，日志包仍保留在所选位置。"; }
        }));
        async Task Copy(string text)
        {
            try
            {
                if (PcrFeedbackActions.CopyText != null) { await PcrFeedbackActions.CopyText(text); return; }
                var clipboard = TopLevel.GetTopLevel(this)?.Clipboard ?? Instances.TopLevel?.Clipboard;
                if (clipboard != null) await clipboard.SetTextAsync(text);
                else status.Text = "剪贴板不可用，请手动复制邮箱：" + PcrPlayerRunLog.FeedbackEmail;
            }
            catch { status.Text = "复制未完成，请手动复制邮箱：" + PcrPlayerRunLog.FeedbackEmail; }
        }
        panel.Children.Add(actions); panel.Children.Add(status);
        var window = new SukiWindow { Title = feedback ? "准备报错反馈" : "导出本次功能日志", Width = 680, Height = 490,
            MinWidth = 520, MinHeight = 360, WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Content = new ScrollViewer { Content = panel } };
        if ((TopLevel.GetTopLevel(this) ?? Instances.TopLevel) is Window owner) window.Show(owner); else window.Show();
        PcrFeedbackActions.WindowOpened?.Invoke(window);
    }
}
