using System;
using System.Linq;
using Avalonia.Controls;
using Avalonia.Interactivity;
using MFAAvalonia.ViewModels.Pages;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
namespace MFAAvalonia.Pcr;
public partial class PcrLabyrinthView : UserControl {
    public PcrLabyrinthView() { AvaloniaXamlLoader.Load(this); }
    private async void PauseClick(object? sender,RoutedEventArgs e) {
        if (DataContext is not TaskQueueViewModel vm) return;
        try { await PcrLabyrinthOwner.TogglePauseAsync(vm); }
        catch (Exception) { vm.LabyrinthSettings.Progress="暂停状态未确认，请查看实时日志或停止本次任务。"; }
    }
    private async void RatingsClick(object? sender,RoutedEventArgs e) {
        if (DataContext is not TaskQueueViewModel vm || !vm.CanEditPcrTarget || TopLevel.GetTopLevel(this) is not Window window) return;
        try { await new PcrLabyrinthRatingsView(vm.LabyrinthSettings.GetPersonalScores(),vm.LabyrinthSettings.ApplyPersonalScores).ShowDialog(window); }
        catch (Exception ex) { vm.LabyrinthSettings.Status="个人评分编辑器无法打开："+ex.Message; }
    }

    public void RevealValidationField(string? message) {
        message ??= "";
        bool Has(params string[] words) => words.Any(word => message.Contains(word, StringComparison.Ordinal));
        var (section, field) = Has("服务器", "渠道") ? ("AccountSettings", "ServerSelector")
            : Has("玩家 ID", "账号", "凭据", "登录") ? ("AccountSettings", "ExpectedPlayerIdInput")
            : Has("难度") ? ("", "DifficultyInput")
            : Has("刷取次数") ? ("RerollSettings", "MaxAttemptsInput")
            : Has("刷取分钟") ? ("RerollSettings", "MaxMinutesInput")
            : Has("响应间隔", "数据库") ? ("RerollSettings", "RerollSettings")
            : Has("开局候选") ? ("OpeningSettings", "OpeningSettings")
            : Has("路线", "Boss ID", "价值容差") ? ("RouteSettings", "RouteSettings")
            : Has("层数", "弱体", "遗物印记", "商店") ? ("RelicSettings", "RelicSettings")
            : Has("普通战胜率") ? ("EventSettings", "NormalWinRateInput")
            : Has("EX战胜率") ? ("EventSettings", "ExtremeWinRateInput")
            : Has("连续事件") ? ("EventSettings", "EventChainPolicySelector")
            : Has("胜率") ? ("EventSettings", "EventSettings")
            : Has("权重", "评分", "扣分", "加分", "Boss 编组", "重试次数", "特别装备") ? ("TeamSettings", "TeamSettings")
            : ("", "GoalsInput");
        if (section.Length > 0 && this.FindControl<Expander>(section) is { } expander) expander.IsExpanded = true;
        Dispatcher.UIThread.Post(() => {
            if (this.FindControl<Control>(field) is not { } control) return;
            control.BringIntoView();
            control.Focus();
        }, DispatcherPriority.Loaded);
    }
}
