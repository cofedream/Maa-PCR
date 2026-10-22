using System;
using System.Linq;
using Avalonia.Controls;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
namespace MFAAvalonia.Pcr;
public partial class PcrLabyrinthView : UserControl {
    public PcrLabyrinthView() { AvaloniaXamlLoader.Load(this); }

    public void RevealValidationField(string? message) {
        message ??= "";
        bool Has(params string[] words) => words.Any(word => message.Contains(word, StringComparison.Ordinal));
        var (section, field) = Has("玩家 ID", "账号", "凭据", "登录") ? ("AccountSettings", "ExpectedPlayerIdInput")
            : Has("难度") ? ("", "DifficultyInput")
            : Has("刷取次数") ? ("RerollSettings", "MaxAttemptsInput")
            : Has("刷取分钟") ? ("RerollSettings", "MaxMinutesInput")
            : Has("开局候选") ? ("OpeningSettings", "OpeningSettings")
            : Has("路线", "Boss ID", "价值容差") ? ("RouteSettings", "RouteSettings")
            : Has("层数", "弱体", "遗物印记", "商店", "胜率") ? ("RelicSettings", "RelicSettings")
            : Has("权重", "评分", "扣分", "加分", "Boss 编组", "重试次数") ? ("TeamSettings", "TeamSettings")
            : ("", "GoalsInput");
        if (section.Length > 0 && this.FindControl<Expander>(section) is { } expander) expander.IsExpanded = true;
        Dispatcher.UIThread.Post(() => {
            if (this.FindControl<Control>(field) is not { } control) return;
            control.BringIntoView();
            control.Focus();
        }, DispatcherPriority.Loaded);
    }
}
