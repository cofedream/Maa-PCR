using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Threading;
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
    private readonly Func<PcrClanGuideDocument, string?, Func<bool>, Task>? loadCurrentAsync;
    private readonly SemaphoreSlim loadGate = new(1, 1);
    private int loadVersion;
    private Task pendingLoad = Task.CompletedTask;
    public Task PendingLoad => pendingLoad;
    public bool IsLoading => !pendingLoad.IsCompleted;
    private PcrClanDailyPlanDocument document = new();
    private string? loadedHash;
    private bool loaded;
    private readonly TextBlock[] slots = Enumerable.Range(1, 3).Select(_ => Note("")).ToArray();
    private readonly TextBlock warnings = Note("");
    private readonly TextBlock status = Note("");
    private const string ThreeReviewed = "三队原件、账号与期次引用已核对，未发现主刀自有角色重复。实际出刀数与补偿仍未知。";
    private readonly TextBlock candidateSummary = Note("");
    private readonly ComboBox lockSlot = new() { ItemsSource = new[] { "不锁定", "锁定第 1 刀", "锁定第 2 刀", "锁定第 3 刀" }, SelectedIndex = 0 };
    private readonly Button saveCandidate;
    private PcrClanDailyPlanDocument? candidate;
    public string StatusText => status.Text ?? "";
    public string[] SlotTexts => slots.Select(s => s.Text ?? "").ToArray();
    public PcrClanDailyPlanView(string repository, string statePath, string configDirectory, string guideStatePath, Func<PcrClanGuideDocument> capture, Func<bool> hasCurrentConfirmation, Action<PcrClanGuideDocument, string?>? loadCurrent = null, Func<PcrClanGuideDocument, string?, Func<bool>, Task>? loadCurrentAsync = null)
    {
        this.repository = Path.GetFullPath(repository); this.statePath = Path.GetFullPath(statePath); this.configDirectory = Path.GetFullPath(configDirectory);
        this.guideStatePath = Path.GetFullPath(guideStatePath); this.capture = capture; this.hasCurrentConfirmation = hasCurrentConfirmation; this.loadCurrent = loadCurrent; this.loadCurrentAsync = loadCurrentAsync;
        var panel = new StackPanel { Spacing = 8 };
        panel.Children.Add(Note("这里保存三刀目标，不会开始战斗。单次模拟只执行当前队伍；正式三刀需切换执行模式，单独核对三目标，并通过随包校准与现场检查。"));
        for (var n = 1; n <= 3; n++)
        {
            var slot = n;
            var row = new StackPanel { Spacing = 4 }; row.Children.Add(slots[n - 1]);
            var controls = new WrapPanel();
            controls.Children.Add(Button($"当前已核对队伍设为第 {n} 刀", () => _ = AssignCurrentAsync(slot)));
            controls.Children.Add(Button($"载入第 {n} 刀到当前模拟队", () => _ = LoadSlotAsync(slot)));
            controls.Children.Add(Button($"清除第 {n} 刀", () => _ = ClearSlotAsync(slot))); row.Children.Add(controls); panel.Children.Add(row);
        }
        panel.Children.Add(warnings);
        var recommendations = new WrapPanel();
        Avalonia.Automation.AutomationProperties.SetName(lockSlot, "配队时保留已有刀位");
        recommendations.Children.Add(lockSlot);
        recommendations.Children.Add(Button("帮我配三刀", () => _ = PreviewRecommendationAsync(lockSlot.SelectedIndex > 0 ? lockSlot.SelectedIndex : null)));
        saveCandidate = Button("保存推荐草案", () => _ = SaveRecommendationAsync());
        saveCandidate.IsEnabled = false;
        recommendations.Children.Add(saveCandidate);
        panel.Children.Add(recommendations);
        panel.Children.Add(candidateSummary);
        lockSlot.SelectionChanged += (_, _) => CancelPendingLoad();
        var actions = new WrapPanel(); actions.Children.Add(Button("核对完整三刀预设", () => _ = ReviewThreeAsync())); actions.Children.Add(Button("重读三刀预设", () => _ = ReloadAsync())); panel.Children.Add(actions);
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
        CancelPendingLoad();
        if (!hasCurrentConfirmation()) throw new InvalidOperationException("请先核对并保存当前五人和操作，再设定目标。");
        var value = Copy(); PcrClanDailyPlan.Assign(value, slot, capture(), guideStatePath, configDirectory, repository, DateTimeOffset.Now);
        Persist(value); status.Text = $"已保存第 {slot} 刀目标引用。未改变当前队伍与开始任务行为。";
    }
    public void ClearSlot(int slot)
    { CancelPendingLoad(); var value = Copy(); PcrClanDailyPlan.Clear(value, slot); Persist(value); status.Text = $"已清除第 {slot} 刀预设，原队伍作业保留。"; }
    public void LoadSlot(int slot)
    {
        CancelPendingLoad();
        if (!loaded || loadCurrent == null) throw new InvalidOperationException("当前预设尚不可载入，请重新读取。");
        if (PcrClanGuide.Stamp(statePath) != loadedHash) throw new IOException("三刀预设已被其他窗口修改，请重新读取；当前队伍未改变。");
        var value = PcrClanDailyPlan.Load(document, slot, guideStatePath, configDirectory, repository, DateTimeOffset.Now, out var guideHash);
        if (PcrClanGuide.Stamp(statePath) != loadedHash) throw new IOException("三刀预设在载入时发生变化；当前队伍未改变。");
        loadCurrent(value, guideHash);
        status.Text = $"已载入第 {slot} 刀，并保存为当前队伍。";
    }
    public void ReviewThree()
    {
        CancelPendingLoad();
        _ = PcrClanDailyPlan.Review(document, guideStatePath, configDirectory, repository, DateTimeOffset.Now, true); Refresh();
        status.Text = ThreeReviewed;
    }
    public void Reload()
    {
        CancelPendingLoad();
        loaded = false;
        try
        {
            document = PcrClanDailyPlan.Read(statePath, out loadedHash); Refresh(); loaded = true;
            status.Text = $"已读取 {document.Slots.Count}/3 个目标。可先保存部分预设，凑齐后核对完整三刀。";
        }
        catch (Exception ex) { status.Text = "载入失败，原文件未改：" + ex.Message; }
    }
    public Task LoadSlotAsync(int slot)
    {
        var version = BeginOperation(); status.Text = "正在载入本刀…";
        return pendingLoad = Complete();
        async Task Complete()
        {
            await loadGate.WaitAsync().ConfigureAwait(false);
            try
            {
                if (version != Volatile.Read(ref loadVersion)) return;
                if (!loaded || loadCurrentAsync == null) throw new InvalidOperationException("当前预设尚不可载入，请重新读取。");
                var current = Copy(); var expected = loadedHash;
                var result = await Task.Run(() =>
                {
                    if (PcrClanGuide.Stamp(statePath) != expected) throw new IOException("三刀预设已被其他窗口修改，请重新读取；当前队伍未改变。");
                    var value = PcrClanDailyPlan.Load(current, slot, guideStatePath, configDirectory, repository, DateTimeOffset.Now, out var guideHash);
                    if (PcrClanGuide.Stamp(statePath) != expected) throw new IOException("三刀预设在载入时发生变化；当前队伍未改变。");
                    return (value, guideHash);
                }).ConfigureAwait(false);
                if (version != Volatile.Read(ref loadVersion)) return;
                await Dispatcher.UIThread.InvokeAsync(() => loadCurrentAsync(result.value, result.guideHash, () => version == Volatile.Read(ref loadVersion)));
                await Dispatcher.UIThread.InvokeAsync(() => { if (version == loadVersion) status.Text = $"已载入第 {slot} 刀，并保存为当前队伍。"; });
            }
            catch (Exception ex) { await Dispatcher.UIThread.InvokeAsync(() => { if (version == loadVersion) status.Text = "未完成：" + ex.Message; }); }
            finally { loadGate.Release(); }
        }
    }
    public Task AssignCurrentAsync(int slot)
    {
        var guide = capture();
        if (guide.ConfirmedFor == null) { status.Text = "请先核对并保存当前五人和操作，再设定目标。"; return Task.CompletedTask; }
        return ChangeAsync(value => PcrClanDailyPlan.Assign(value, slot, guide, guideStatePath, configDirectory, repository, DateTimeOffset.Now), $"已保存第 {slot} 刀目标引用。未改变当前队伍与开始任务行为。");
    }
    public Task ClearSlotAsync(int slot) => ChangeAsync(value => PcrClanDailyPlan.Clear(value, slot), $"已清除第 {slot} 刀预设，原队伍作业保留。");
    private Task ChangeAsync(Action<PcrClanDailyPlanDocument> change, string message)
    {
        if (!loaded) { status.Text = "原预设尚未成功载入，禁止覆盖。请保留原文件并重读。"; return Task.CompletedTask; }
        var version = BeginOperation(); status.Text = "正在核对并保存三刀预设…";
        return pendingLoad = Complete();
        async Task Complete()
        {
            string? committed = null; PcrClanDailyPlanDocument? committedDocument = null;
            await loadGate.WaitAsync().ConfigureAwait(false);
            try
            {
                if (version != Volatile.Read(ref loadVersion)) return;
                var state = await Dispatcher.UIThread.InvokeAsync(() => (value: Copy(), expected: loadedHash));
                var result = await Task.Run(() =>
                {
                    change(state.value);
                    var stamp = PcrClanDailyPlan.Save(state.value, statePath, state.expected); committed = stamp; committedDocument = state.value;
                    var review = PcrClanDailyPlan.Review(state.value, guideStatePath, configDirectory, repository, DateTimeOffset.Now);
                    return (state.value, stamp, review);
                }).ConfigureAwait(false);
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    document = result.value; loadedHash = result.stamp;
                    if (version != loadVersion) return;
                    for (var index = 0; index < 3; index++) slots[index].Text = result.review.Slots[index];
                    warnings.Text = string.Join(Environment.NewLine, result.review.Warnings); status.Text = message;
                });
            }
            catch (Exception ex)
            {
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    if (committed != null) { loadedHash = committed; document = committedDocument!; loaded = false; }
                    if (version == loadVersion) status.Text = committed != null ? "三刀预设已保存，但写后核对未完成，请重读：" + ex.Message : "未完成：" + ex.Message;
                });
            }
            finally { loadGate.Release(); }
        }
    }
    private int BeginOperation()
    {
        candidate = null;
        candidateSummary.Text = "";
        saveCandidate.IsEnabled = false;
        return ++loadVersion;
    }
    internal void CancelPendingLoad() => BeginOperation();
    public Task PreviewRecommendationAsync(int? lockedSlot)
    {
        if (!loaded) { status.Text = "请先成功读取三刀预设。"; return Task.CompletedTask; }
        var guide = capture();
        return ReadReviewAsync(true, false, guide, lockedSlot);
    }
    public Task SaveRecommendationAsync()
    {
        var proposed = candidate;
        if (proposed == null) { status.Text = "请先点击“帮我配三刀”预览候选。"; return Task.CompletedTask; }
        return ChangeAsync(value =>
        {
            _ = PcrClanDailyPlan.Review(proposed, guideStatePath, configDirectory, repository, DateTimeOffset.Now, true);
            value.Slots = proposed.Slots;
        }, "已保存三刀推荐草案。正式挑战仍需单独核对和授权。");
    }
    public Task ReloadAsync() => ReadReviewAsync(false, true);
    public Task ReviewThreeAsync() => ReadReviewAsync(true, false);
    private Task ReadReviewAsync(bool requireThree, bool readFile, PcrClanGuideDocument? guide = null, int? lockedSlot = null)
    {
        var version = BeginOperation(); status.Text = guide == null ? "正在核对三刀预设…" : "正在从本期已核对的作业中配队…";
        return pendingLoad = Complete();
        async Task Complete()
        {
            await loadGate.WaitAsync().ConfigureAwait(false);
            try
            {
                if (version != Volatile.Read(ref loadVersion)) return;
                var state = await Dispatcher.UIThread.InvokeAsync(() => (current: Copy(), expected: loadedHash));
                var expected = state.expected;
                var result = await Task.Run(() =>
                {
                    var value = readFile ? PcrClanDailyPlan.Read(statePath, out expected) : state.current;
                    if (guide != null)
                    {
                        if (PcrClanGuide.Stamp(statePath) != expected) throw new IOException("三刀预设已变化，请重新读取。");
                        value = PcrClanDailyPlan.Recommend(value, guide, lockedSlot, guideStatePath, configDirectory, repository, DateTimeOffset.Now);
                    }
                    var review = PcrClanDailyPlan.Review(value, guideStatePath, configDirectory, repository, DateTimeOffset.Now, requireThree);
                    return (value, review);
                }).ConfigureAwait(false);
                await Dispatcher.UIThread.InvokeAsync(() =>
                {
                    if (version != loadVersion) return;
                    if (guide != null)
                    {
                        candidate = result.value;
                        candidateSummary.Text = "推荐草案（待保存）" + Environment.NewLine + string.Join(Environment.NewLine, result.review.Slots);
                        saveCandidate.IsEnabled = true;
                        status.Text = "已找到三队。核对候选后点击“保存推荐草案”写入三刀预设。";
                        return;
                    }
                    document = result.value; loadedHash = expected; loaded = true;
                    for (var index = 0; index < 3; index++) slots[index].Text = result.review.Slots[index];
                    warnings.Text = string.Join(Environment.NewLine, result.review.Warnings);
                    status.Text = requireThree ? ThreeReviewed : $"已读取 {document.Slots.Count}/3 个目标。可先保存部分预设，凑齐后核对完整三刀。";
                });
            }
            catch (Exception ex) { await Dispatcher.UIThread.InvokeAsync(() => { if (version == loadVersion) { if (readFile) loaded = false; status.Text = "载入失败，原文件未改：" + ex.Message; } }); }
            finally { loadGate.Release(); }
        }
    }
}
