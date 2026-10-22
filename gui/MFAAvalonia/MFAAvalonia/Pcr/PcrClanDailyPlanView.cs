using System;
using System.IO;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using Newtonsoft.Json;

namespace MFAAvalonia.Pcr;

public sealed class PcrClanDailyPlanView : UserControl
{
    private readonly string repository, statePath, configDirectory, guideStatePath;
    private readonly Func<PcrClanGuideDocument> capture;
    private readonly Func<bool> hasCurrentConfirmation;
    private readonly Action<PcrClanGuideDocument, string?>? loadCurrent;
    private PcrClanDailyPlanDocument document = new();
    private string? loadedHash;
    private bool loaded;
    private readonly TextBlock[] slots = Enumerable.Range(1, 3).Select(_ => Note("")).ToArray();
    private readonly TextBlock warnings = Note("");
    private readonly TextBlock status = Note("");
    public string StatusText => status.Text ?? "";
    public string[] SlotTexts => slots.Select(s => s.Text ?? "").ToArray();
    public PcrClanDailyPlanView(string repository, string statePath, string configDirectory, string guideStatePath, Func<PcrClanGuideDocument> capture, Func<bool> hasCurrentConfirmation, Action<PcrClanGuideDocument, string?>? loadCurrent = null)
    {
        this.repository = Path.GetFullPath(repository); this.statePath = Path.GetFullPath(statePath); this.configDirectory = Path.GetFullPath(configDirectory);
        this.guideStatePath = Path.GetFullPath(guideStatePath); this.capture = capture; this.hasCurrentConfirmation = hasCurrentConfirmation; this.loadCurrent = loadCurrent;
        var panel = new StackPanel { Spacing = 8 };
        panel.Children.Add(Note("仅离线预设。开始任务仍只模拟当前队伍一局；实际出刀数与补偿状态未知。三刀不会自动连打。"));
        for (var n = 1; n <= 3; n++)
        {
            var slot = n;
            var row = new StackPanel { Spacing = 4 }; row.Children.Add(slots[n - 1]);
            var controls = new WrapPanel();
            controls.Children.Add(Button($"当前已核对队伍设为第 {n} 刀", () => AssignCurrent(slot)));
            controls.Children.Add(Button($"载入第 {n} 刀到当前模拟队", () => LoadSlot(slot)));
            controls.Children.Add(Button($"清除第 {n} 刀", () => ClearSlot(slot))); row.Children.Add(controls); panel.Children.Add(row);
        }
        panel.Children.Add(warnings);
        var actions = new WrapPanel(); actions.Children.Add(Button("核对完整三刀预设", ReviewThree)); actions.Children.Add(Button("重读三刀预设", Reload)); panel.Children.Add(actions);
        panel.Children.Add(Note("补偿预设策略：游戏实际显示补偿可用，且同号 BOSS 已刷新时，才考虑继续同号 BOSS；同号目标尚未出现则保留。只有游戏实际确认 3 刀主刀均已结算后，才提醒仍有未用补偿。BOSS 死亡不等于已获得补偿，此处不生成出刀或补偿记录。"));
        panel.Children.Add(status); Content = panel;
    }
    private static TextBlock Note(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap };
    private Button Button(string label, Action action)
    {
        var button = new Button { Content = label, Margin = new Thickness(0, 0, 8, 3) };
        Avalonia.Automation.AutomationProperties.SetName(button, label);
        button.Click += (_, _) => { try { action(); } catch (Exception ex) { status.Text = "未完成：" + ex.Message; } }; return button;
    }
    private PcrClanDailyPlanDocument Copy() => JsonConvert.DeserializeObject<PcrClanDailyPlanDocument>(JsonConvert.SerializeObject(document))!;
    private void Refresh()
    {
        var review = PcrClanDailyPlan.Review(document, guideStatePath, configDirectory, repository, DateTimeOffset.Now);
        for (var index = 0; index < 3; index++) slots[index].Text = review.Slots[index];
        warnings.Text = string.Join(Environment.NewLine, review.Warnings);
    }
    private void Persist(PcrClanDailyPlanDocument value)
    {
        if (!loaded) throw new InvalidOperationException("原预设尚未成功载入，禁止覆盖。请保留原文件并重读。");
        loadedHash = PcrClanDailyPlan.Save(value, statePath, loadedHash); document = value; Refresh();
    }
    public void AssignCurrent(int slot)
    {
        if (!hasCurrentConfirmation()) throw new InvalidOperationException("请先核对并保存当前五人和操作，再设定目标。");
        var value = Copy(); PcrClanDailyPlan.Assign(value, slot, capture(), guideStatePath, configDirectory, repository, DateTimeOffset.Now);
        Persist(value); status.Text = $"已保存第 {slot} 刀目标引用。未改变当前队伍与开始任务行为。";
    }
    public void ClearSlot(int slot)
    { var value = Copy(); PcrClanDailyPlan.Clear(value, slot); Persist(value); status.Text = $"已清除第 {slot} 刀预设，原队伍作业保留。"; }
    public void LoadSlot(int slot)
    {
        if (!loaded || loadCurrent == null) throw new InvalidOperationException("当前预设尚不可载入，请重新读取。");
        if (PcrClanGuide.Stamp(statePath) != loadedHash) throw new IOException("三刀预设已被其他窗口修改，请重新读取；当前队伍未改变。");
        var value = PcrClanDailyPlan.Load(document, slot, guideStatePath, configDirectory, repository, DateTimeOffset.Now, out var guideHash);
        if (PcrClanGuide.Stamp(statePath) != loadedHash) throw new IOException("三刀预设在载入时发生变化；当前队伍未改变。");
        loadCurrent(value, guideHash);
        status.Text = $"已载入第 {slot} 刀，并保存为当前模拟队。开始任务仍只模拟这一队一局，不连打三刀。";
    }
    public void ReviewThree()
    {
        _ = PcrClanDailyPlan.Review(document, guideStatePath, configDirectory, repository, DateTimeOffset.Now, true); Refresh();
        status.Text = "三队原件、账号与期次引用已核对，未发现主刀自有角色重复。实际出刀数与补偿仍未知，开始任务仍只模拟当前队伍一局。";
    }
    public void Reload()
    {
        loaded = false;
        try
        {
            document = PcrClanDailyPlan.Read(statePath, out loadedHash); Refresh(); loaded = true;
            status.Text = $"已读取 {document.Slots.Count}/3 个目标。可先保存部分预设，凑齐后核对完整三刀。";
        }
        catch (Exception ex) { status.Text = "载入失败，原文件未改：" + ex.Message; }
    }
}
