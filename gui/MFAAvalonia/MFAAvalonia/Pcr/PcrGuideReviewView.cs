using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Data;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Markup.Xaml.Styling;
using Avalonia.Platform.Storage;

namespace MFAAvalonia.Pcr;

public sealed class PcrGuideReviewView : UserControl
{
    private readonly PcrGuideStore store;
    private readonly PcrGuideAccountStore accounts;
    private readonly PcrGuideLibrary library;
    private readonly ComboBox savedTeams = new() { MinWidth = 320, MaxWidth = 760, PlaceholderText = "选择已保存队伍" };
    private readonly TextBlock teamSummary = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Func<string?>? configuredAccount;
    private readonly Dictionary<string, TextBox> fields = new();
    private readonly CheckBox sourceConfirmed = new() { Content = "已逐项核对文字材料与来源一致" };
    private readonly CheckBox coverageConfirmed = new() { Content = "已完整整理本队角色、练度、SET/AUTO 和账号强化条件" };
    private readonly TextBox report = new() { IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, VerticalContentAlignment = VerticalAlignment.Top };
    private readonly TextBox minimum = new() { IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, VerticalContentAlignment = VerticalAlignment.Top };
    private readonly TextBlock status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly DataGrid conditions = new() { AutoGenerateColumns = false, MinHeight = 180, MaxHeight = 320, FontSize = 14 };
    private readonly DataGrid facts = new() { AutoGenerateColumns = false, MinHeight = 180, MaxHeight = 400, FontSize = 14 };
    private readonly TabControl tabs = new();
    private readonly ComboBox rolePicker = new() { MinWidth = 260, MaxWidth = 480, PlaceholderText = "选择完整角色名称" };
    private readonly TextBox roleSearch = new() { Watermark = "按完整名称或编号搜索" };
    private readonly TextBlock catalogStatus = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock snapshotStatus = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBox teamTable = new() { AcceptsReturn = true, MinHeight = 130, TextWrapping = TextWrapping.NoWrap,
        Watermark = "角色\t星级\t等级\tRank\tSET标记\n粘贴五行完整角色名称或编号；列之间用制表符分隔。" };
    private readonly CheckBox snapshotConfirmed = new() { Content = "已核对导入文件的来源、账号编号和采集时间，适用于本次目标" };
    private PcrGuideCatalog? catalog;
    private PcrGuideSnapshot? snapshot;
    private PcrGuideIdentity? identity;
    private bool requiresImageReview;
    private readonly StackPanel identityPanel = new() { Spacing = 6 };
    private readonly StackPanel openingPanel = new() { Spacing = 8, Orientation = Orientation.Horizontal };
    private readonly CheckBox openingConfirmed = new() { Content = "已逐人核对 SET 原标记与特殊说明；AUTO 保持开启，不需战中时间操作" };
    private Dictionary<int,bool?>? openingSets;
    private readonly List<Bitmap> identityImages = new();
    private ObservableCollection<PcrGuideCondition> conditionRows = new();
    private ObservableCollection<PcrGuideFact> factRows = new();
    private PcrGuideReport? currentReport;
    private bool loading;
    private bool dirty;
    private readonly Func<PcrGuideInput, CancellationToken, Task<byte[]>>? readSnapshot;
    private CancellationTokenSource? syncCancel;
    public bool HasCurrentReport => currentReport != null;
    public string ReportText => report.Text ?? "";
    public string MinimumText => minimum.Text ?? "";
    public string StatusText => status.Text ?? "";
    public void ShowAccountPage() => tabs.SelectedIndex = 1;
    public string TeamSummaryText => teamSummary.Text ?? "";
    public IReadOnlyList<PcrGuideLibraryEntry> SavedTeams => library.Entries;

    public PcrGuideReviewView(string path, Func<PcrGuideInput, CancellationToken, Task<byte[]>>? readSnapshot = null, Func<string?>? configuredAccount = null)
    {
        store = new(path);
        accounts = new(Path.GetDirectoryName(Path.GetFullPath(path))!);
        library = new(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(path))!, "guide-library"));
        this.configuredAccount = configuredAccount;
        this.readSnapshot = readSnapshot;
        DetachedFromVisualTree += (_, _) => syncCancel?.Cancel();
        Styles.Add(new StyleInclude(new Uri("avares://MFAAvalonia.Core/")) { Source = new Uri("avares://Avalonia.Controls.DataGrid/Themes/Fluent.xaml") });
        var guide = new StackPanel { Spacing = 8 };
        guide.Children.Add(Note("选一份作业，对上五人，设置 SET 后一次保存。AUTO 保持开启；练度、EX 和强化差异只提示。随后从原“开始任务”试打一局，战败即停并通知。"));
        var importTools = new StackPanel { Spacing = 8 };
        Avalonia.Automation.AutomationProperties.SetName(savedTeams, "本机攻略库队伍");
        guide.Children.Add(savedTeams);
        guide.Children.Add(Buttons(Button("载入所选队伍", () =>
        {
            if (savedTeams.SelectedItem is PcrGuideLibraryEntry entry) LoadSavedTeam(entry.Key);
            else status.Text = "请先选择已保存队伍。";
        })));
        importTools.Children.Add(Buttons(Button("导入攻略草稿", PickDraft), Button("导出攻略草稿", SaveDraft)));
        importTools.Children.Add(Buttons(Button("导入静态角色表", () => PickData(false))));
        importTools.Children.Add(catalogStatus);
        var batch = new StackPanel { Spacing = 6 };
        batch.Children.Add(Note("首列“角色”填写完整名称或编号；可选列：星级、等级、Rank、装备件数、突破、专武1等级、专武2等级、UB等级、技能1等级、技能2等级、EX技能等级、EX装备1、EX装备2、EX装备3、SET标记。O／⚪ 默认解释为开 SET，X 为 AUTO 开、SET 关；保留原标记，须结合特殊说明确认。"));
        Avalonia.Automation.AutomationProperties.SetName(teamTable,"五人攻略表格");
        batch.Children.Add(teamTable);
        batch.Children.Add(Button("生成五人条件", () => ApplyTeamTable(teamTable.Text ?? "")));
        batch.Children.Add(Note("替换当前五人的条件，保留账号强化条件和仍对应的本机资料；角色展示值按参考处理。生成后核对五人与 SET，再一次保存。"));
        importTools.Children.Add(new Expander { Header = "从五人表格批量整理", Content = batch });
        importTools.Children.Add(Buttons(Button("核对所选关卡原图", LoadIdentity), Button("导入原图核对文件", PickIdentity), Button("按原图生成五人", ApplySourceTeam)));
        guide.Children.Add(identityPanel);
        guide.Children.Add(Note("开场设置：先确认 AUTO 开启，再按实际角色位置核对 SET；已符合的角色不点击。"));
        guide.Children.Add(openingPanel);
        AddField(guide, "Title", "作业名称");
        AddPair(guide, "GuideServer", "攻略区服", "GuideStage", "攻略关卡（包含属性）");
        guide.Children.Add(new Expander { Header = "导入或更换作业材料", Content = importTools });
        var details = new StackPanel { Spacing = 8 };
        details.Children.Add(Note("以下是可选的来源和练度整理，缺项不会拦住已核对五人的试打。"));
        details.Children.Add(openingConfirmed);
        AddField(details, "SourceUrl", "来源链接");
        details.Children.Add(Button("打开来源", async () =>
        {
            var url = fields["SourceUrl"].Text;
            if (!PcrGuideReview.IsWebSource(url)) { status.Text = "请填写有效的 http / https 来源链接。"; return; }
            var top = TopLevel.GetTopLevel(this);
            try { if (top != null) await top.Launcher.LaunchUriAsync(new Uri(url!)); }
            catch (Exception ex) { status.Text = "无法打开来源：" + ex.Message; }
        }));
        AddPair(details, "RetrievedOn", "实际取得日期", "ValidThrough", "人工复核有效至（留空表示待复核）");
        AddPair(details, "GuideVersion", "攻略适用版本", "Mode", "操作方式：AUTO 或 SET/AUTO");
        AddField(details, "Material", "文字材料与已核实说明", multiline: true);
        details.Children.Add(sourceConfirmed);
        details.Children.Add(Buttons(Button("关联选中条件的角色", BindSelectedRole)));
        details.Children.Add(roleSearch); details.Children.Add(rolePicker);
        details.Children.Add(Note("先选择条件行，再从角色表选择完整名称并关联。找不到角色时保留手工条件，不按昵称自动猜形态。"));
        details.Children.Add(Note("比较填 =、>=、<= 或“参考”。练度差异只作提示；文字依据按实际材料保留，未知不补造。角色填全名和形态，账号强化填“账号”。"));
        Columns(conditions, ("角色 / 账号", "Character", 110), ("形态", "Form", 90), ("字段", "Field", 100),
            ("比较", "Comparison", 65), ("要求 / 参考值", "Expected", 100), ("文字依据", "Evidence", 240));
        details.Children.Add(conditions);
        details.Children.Add(Buttons(Button("增加条件", () => { conditionRows.Add(new()); Invalidate(); }),
            Button("删除选中条件", () => { if (conditions.SelectedItem is PcrGuideCondition c) { conditionRows.Remove(c); Invalidate(); } })));
        details.Children.Add(coverageConfirmed);
        details.Children.Add(Buttons(Button("载入国服待补样例", () => LoadExample()), Button("载入日服文字样例", () => LoadExample(true))));
        guide.Children.Add(new Expander { Header = "来源与练度明细（可选）", Content = details });

        var account = new StackPanel { Spacing = 8 };
        account.Children.Add(teamSummary);
        account.Children.Add(Note("只填与此作业相关的资料。这里的账号标签独立于日常执行选集；切换游戏账号后请更新。没有资料就留空，缺一行不代表未持有。"));
        account.Children.Add(Buttons(Button("导入账号快照", () => PickData(true)), Button("清空账号资料", ClearAccount)));
        account.Children.Add(Note("协议读取复用黎明界账号，一次保存全部持有角色、五种属性等级和各职能槽位；换队只读本机资料。登录可能使模拟器断线，仅在空闲且未战斗时刷新，结束后可重新登录游戏。"));
        account.Children.Add(Buttons(Button("读取全部持有角色", ReadSnapshot), Button("复用本机账号资料", ReuseAccount), Button("停止读取", () => syncCancel?.Cancel())));
        account.Children.Add(snapshotStatus); account.Children.Add(snapshotConfirmed);
        account.Children.Add(Note("属性等级的形态栏可填“火”“水”等；留空时只对应本队唯一属性。职能强化须填“攻击型/槽1”等具体范围。作者仅写“3-2”时保留待确认，不套用到所有职能。旧快照缺项保持未知，复用不更新采集时间。"));
        AddPair(account, "TargetServer", "本次目标区服", "TargetStage", "本次目标关卡");
        AddField(account, "TargetVersion", "本次游戏版本");
        AddPair(account, "AccountLabel", "账号标签（无需账号密码）", "AccountObservedOn", "实际观察日期");
        AddPair(account, "AccountServer", "资料所属区服", "AccountVersion", "资料观察时版本");
        account.Children.Add(Note("持有填“是”或“否”；其他字段填实际值。依据可写“角色页截图日期 / 文件名”等本机定位；每项核实后勾选确认。手动填写不读取登录信息。"));
        Columns(facts, ("角色 / 账号", "Character", 110), ("形态", "Form", 90), ("字段", "Field", 100),
            ("实际值", "Value", 85), ("账号依据", "Source", 230));
        facts.Columns.Add(new DataGridCheckBoxColumn { Header = "已确认", Binding = new Binding("Confirmed") { Mode = BindingMode.TwoWay } });
        account.Children.Add(facts);
        account.Children.Add(Buttons(Button("从条件建立待核对项", AddFactsFromConditions),
            Button("增加资料", () => { factRows.Add(new()); Invalidate(); }),
            Button("删除选中资料", () => { if (facts.SelectedItem is PcrGuideFact f) { factRows.Remove(f); Invalidate(); } })));
        account.Children.Add(Note("导入只替换本面板的账号资料，保存前可放弃重开。仅接受规范化快照 v1，不读取账号密码；超过 24 小时需刷新。版本使用 content_version，不自动等同应用版本。解放星级不代替战斗选用星级。"));

        tabs.ItemsSource = new[] {
            new TabItem { Header = "1 · 五人与开场", Content = Scroll(guide) },
            new TabItem { Header = "2 · 本队账号资料", Content = Scroll(account) },
            new TabItem { Header = "3 · 核对结果", Content = report },
            new TabItem { Header = "4 · 补齐参考（可选）", Content = minimum }
        };
        var footer = new StackPanel { Spacing = 6 };
        footer.Children.Add(Buttons(Button("确认五人和开场设置并保存", ConfirmTeamAndSave),
            Button("暂存草稿", Save), Button("查看详细对照", Check), Button("放弃编辑并重开", Reload)));
        footer.Children.Add(status);
        var layout = new Grid { RowDefinitions = new RowDefinitions("Auto,*,Auto"), Margin = new Thickness(16), RowSpacing = 10 };
        layout.Children.Add(Note("攻略检查 · 不执行培养或战斗；只有点击协议读取时才会登录账号。"));
        Grid.SetRow(tabs, 1); layout.Children.Add(tabs);
        Grid.SetRow(footer, 2); layout.Children.Add(footer);
        Content = layout;
        conditions.CellEditEnded += (_, e) =>
        {
            if (e.Column.DisplayIndex is 0 or 1 && e.Row.DataContext is PcrGuideCondition c) c.UnitId = null;
            coverageConfirmed.IsChecked = false; Invalidate();
            ResetOpening();
        };
        facts.CellEditEnded += (_, e) =>
        {
            if (e.Column is not DataGridCheckBoxColumn && e.Row.DataContext is PcrGuideFact fact)
            { fact.Confirmed = false; if (e.Column.DisplayIndex is 0 or 1) fact.UnitId = null; }
            Invalidate();
        };
        sourceConfirmed.IsCheckedChanged += (_, _) => Invalidate();
        coverageConfirmed.IsCheckedChanged += (_, _) => Invalidate();
        snapshotConfirmed.IsCheckedChanged += (_, _) => Invalidate();
        openingConfirmed.IsCheckedChanged += (_, _) => Invalidate();
        roleSearch.PropertyChanged += (_, e) => { if (e.Property == TextBox.TextProperty) FilterRoles(); };
        Avalonia.Automation.AutomationProperties.SetName(roleSearch, "搜索静态角色表");
        Avalonia.Automation.AutomationProperties.SetName(rolePicker, "静态角色完整名称与编号");
        Reload();
    }

    private static TextBlock Note(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap };
    private static ScrollViewer Scroll(Control content) => new() { Content = content, HorizontalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Disabled };
    private static WrapPanel Buttons(params Button[] buttons)
    {
        var panel = new WrapPanel();
        foreach (var button in buttons) { button.Margin = new Thickness(0, 0, 8, 4); panel.Children.Add(button); }
        return panel;
    }
    private Button Button(string text, Action action)
    {
        var button = new Button { Content = text };
        button.Click += (_, _) => { try { action(); } catch (Exception ex) { status.Text = "操作未完成：" + ex.Message; } };
        return button;
    }
    private void AddPair(StackPanel parent, string key1, string label1, string key2, string label2)
    {
        var grid = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*"), ColumnSpacing = 12 };
        var left = new StackPanel(); var right = new StackPanel();
        AddField(left, key1, label1); AddField(right, key2, label2);
        grid.Children.Add(left); Grid.SetColumn(right, 1); grid.Children.Add(right); parent.Children.Add(grid);
    }
    private void AddField(StackPanel parent, string key, string label, bool multiline = false)
    {
        parent.Children.Add(Note(label));
        var box = new TextBox { AcceptsReturn = multiline, MinHeight = multiline ? 130 : 32, TextWrapping = TextWrapping.Wrap,
            VerticalContentAlignment = multiline ? VerticalAlignment.Top : VerticalAlignment.Center };
        Avalonia.Automation.AutomationProperties.SetName(box, label);
        fields.Add(key, box);
        box.PropertyChanged += (_, e) =>
        {
            if (e.Property != TextBox.TextProperty) return;
            if (!loading)
            {
                if (key is "SourceUrl" or "Material" or "GuideServer" or "GuideStage" or "GuideVersion" or "Mode" or "RetrievedOn")
                    sourceConfirmed.IsChecked = false;
                if (key == "Material") coverageConfirmed.IsChecked = false;
                if (key is "SourceUrl" or "Material" or "GuideServer" or "GuideStage")
                { identity = null; openingConfirmed.IsChecked = false; ShowIdentity(); }
                if (key is "AccountLabel" or "AccountServer" or "AccountVersion" or "AccountObservedOn" or "TargetServer" or "TargetVersion")
                    snapshotConfirmed.IsChecked = false;
            }
            Invalidate();
        };
        parent.Children.Add(box);
    }
    private static void Columns(DataGrid grid, params (string Label, string Property, int Width)[] columns)
    {
        foreach (var c in columns) grid.Columns.Add(new DataGridTextColumn { Header = c.Label, FontSize = 14,
            Binding = new Binding(c.Property) { Mode = BindingMode.TwoWay }, Width = new DataGridLength(c.Width) });
    }
    private void Commit()
    {
        conditions.CommitEdit(DataGridEditingUnit.Row, true);
        facts.CommitEdit(DataGridEditingUnit.Row, true);
    }
    private void Invalidate()
    {
        if (loading) return;
        currentReport = null; dirty = true;
        ShowIdentity();
        ShowTeamSummary();
        report.Text = "资料已改变，旧结论已失效。请重新检查。";
        minimum.Text = "资料已改变，补齐建议已失效。请重新检查。";
        status.Text = "有未保存的修改。";
    }
    public PcrGuideInput Capture()
    {
        Commit();
        var input = new PcrGuideInput { SourceConfirmed = sourceConfirmed.IsChecked == true,
            CoverageConfirmed = coverageConfirmed.IsChecked == true,
            Catalog = catalog, Snapshot = snapshot, SnapshotConfirmed = snapshotConfirmed.IsChecked == true, Identity = identity,
            RequiresImageReview = requiresImageReview,
            OpeningSets=openingSets, OpeningConfirmed=openingConfirmed.IsChecked==true,
            Conditions = conditionRows.ToList(), Facts = factRows.ToList() };
        foreach (var (key, box) in fields) typeof(PcrGuideInput).GetProperty(key)!.SetValue(input, box.Text ?? "");
        return input;
    }
    public void Display(PcrGuideInput input, PcrGuideReport? savedReport = null)
    {
        loading = true;
        try
        {
            foreach (var (key, box) in fields) box.Text = (string?)typeof(PcrGuideInput).GetProperty(key)!.GetValue(input) ?? "";
            sourceConfirmed.IsChecked = input.SourceConfirmed; coverageConfirmed.IsChecked = input.CoverageConfirmed;
            catalog = input.Catalog; snapshot = input.Snapshot; snapshotConfirmed.IsChecked = input.SnapshotConfirmed;
            identity = input.Identity;
            requiresImageReview = input.RequiresImageReview || identity?.ImportedSource != null;
            openingSets=input.OpeningSets == null ? null : new(input.OpeningSets); openingConfirmed.IsChecked=input.OpeningConfirmed;
            catalogStatus.Text = catalog == null ? "尚未导入静态角色表。" : $"已载入 {catalog.Units.Count} 个角色；内容版本 {catalog.ContentVersion}。静态表不能证明本人持有。";
            snapshotStatus.Text = accounts.Describe(input, DateTimeOffset.Now);
            FilterRoles();
            conditionRows = new(input.Conditions); factRows = new(input.Facts);
            ShowIdentity();
            ShowOpening();
            ShowTeamSummary();
            conditions.ItemsSource = conditionRows; facts.ItemsSource = factRows;
            currentReport = savedReport?.InputHash == PcrGuideReview.Fingerprint(input) ? savedReport : null;
            report.Text = currentReport?.Text ?? "尚无有效报告。补充材料后点击“检查适用性”。";
            minimum.Text = currentReport == null ? "检查并确认所选一队的来源与账号后，查看明确数值缺口、参考差异和未知。"
                : PcrGuideMinimum.Check(input, currentReport, DateTimeOffset.Now, configuredAccount?.Invoke()).Text;
            dirty = false;
        }
        finally { loading = false; }
    }
    public void Check()
    {
        var input = Capture(); var now = DateTimeOffset.Now;
        currentReport = PcrGuideReview.Check(input, now);
        minimum.Text = PcrGuideMinimum.Check(input, currentReport, now, configuredAccount?.Invoke()).Text;
        ShowIdentity();
        report.Text = currentReport.Text; tabs.SelectedIndex = 2; dirty = true;
        status.Text = "已核对；请保存以便重开查看。";
    }
    public void Save()
    {
        var input = Capture();
        store.Save(new() { Input = input, Report = currentReport });
        dirty = false; status.Text = "材料、来源、确认状态和有效报告已保存到本机。";
        try
        {
            if (library.Save(input))
            {
                savedTeams.ItemsSource = library.Entries;
                status.Text += " 五人草稿已加入本机攻略库，切队时重新确认。";
            }
        }
        catch (Exception ex) { status.Text += " 攻略库未更新：" + ex.Message; }
    }
    public void ConfirmTeamAndSave()
    {
        // Work on a copy so a rejected confirmation cannot alter the current evidence.
        var input = PcrGuideReview.Parse(PcrGuideReview.Serialize(new PcrGuideDocument { Input = Capture() })).Input;
        var accountId = configuredAccount?.Invoke();
        if (string.IsNullOrEmpty(accountId)) throw new InvalidOperationException("请先在原账号配置中选择账号。");
        input = accounts.Reuse(input, accountId, DateTimeOffset.Now);
        if (input.Catalog == null || input.Snapshot?.AccountId != accountId
            || accounts.Stamp(input.Catalog, accountId) != input.Snapshot.Sha256)
            throw new InvalidOperationException("缺少当前账号的本机快照；请在账号页复用或读取资料。");
        input.SnapshotConfirmed = true;
        if (input.Identity?.Reviewable() != true) throw new InvalidOperationException("请先载入这份作业的原图并对上五人。");
        if (input.Identity.Members.Any(m => !File.Exists(Path.Combine(PcrAlphaOwner.Repository, "assets", "labyrinth", "character-pack", "icons", "characters", $"icon_unit_{m.IconUnitId}.png"))))
            throw new InvalidOperationException("角色目录头像缺失，暂不能确认五人。");
        var ids = input.Identity.Members.Select(m => m.UnitId).Order().ToArray();
        input.OpeningSets ??= PcrGuideTrial.SuggestedSets(input);
        if (input.OpeningSets == null || !input.OpeningSets.Keys.Order().SequenceEqual(ids) || input.OpeningSets.Values.Any(v => !v.HasValue))
            throw new InvalidOperationException("请明确五人的 SET 开关；AUTO 保持开启。");
        for (var index = 0; index < input.Identity.Members.Count; index++)
            if (input.Identity.NeedsConfirmation(input.Identity.Members[index])) input.Identity.Confirm(index, input);
        if (!input.Identity.Matches(input)) throw new InvalidOperationException("原图、目标关卡与五人编号未对应，请核对后保存。");
        input.OpeningConfirmed = true;
        var checkedReport = PcrGuideReview.Check(input, DateTimeOffset.Now);
        if (!checkedReport.Summary.StartsWith("可以试打") && !checkedReport.Summary.StartsWith("离线对照已完成"))
            throw new InvalidOperationException(checkedReport.Summary + " 请查看详细对照中的账号、五人与开场状态。");
        if (input.Identity.Executable()) _ = PcrGuideTrial.CreatePlan(input);
        Display(input, checkedReport); dirty = true; Save();
        status.Text = "已确认并保存五人和开场设置，账号资料已从本机复用。\n" + checkedReport.Summary;
    }
    public void Reload()
    {
        try
        {
            var doc = store.Load(); Display(doc.Input, doc.Report);
            library.Reload(); savedTeams.ItemsSource = library.Entries; savedTeams.SelectedIndex = -1;
            status.Text = "已读取本机资料；保存的报告已按当前日期重新核对。";
        }
        catch (Exception ex) { currentReport = null; report.Text = "未能读取，禁止覆盖原文件。"; status.Text = "读取失败：" + ex.Message; }
    }
    public void LoadExample(bool textExample = false)
    {
        if (dirty) { status.Text = "请先保存，或选择“放弃编辑并重开”，再载入样例。"; return; }
        Display(textExample ? PcrGuideReview.TextExample() : PcrGuideReview.RealExample()); Invalidate(); tabs.SelectedIndex = 0;
        status.Text = textExample ? "已载入日服文字样例；与国服目标有区服冲突，账号和未整理条件继续未知。" :
            "已载入真实来源的部分材料；缺少视频细节与账号资料，不能据此判断适用。";
    }
    public void AddFactsFromConditions()
    {
        Commit();
        foreach (var c in conditionRows)
            if (!factRows.Any(f => f.Character == c.Character && f.Form == c.Form && f.Field == c.Field))
                factRows.Add(new() { UnitId = c.UnitId, Character = c.Character, Form = c.Form, Field = c.Field });
        Invalidate();
    }
    private void FilterRoles()
    {
        var query = roleSearch.Text?.Trim() ?? "";
        rolePicker.ItemsSource = catalog?.Units.Where(u => u.Name.Contains(query, StringComparison.OrdinalIgnoreCase)
            || u.UnitId.ToString().Contains(query, StringComparison.Ordinal)).Take(80).ToList();
        rolePicker.SelectedItem = null;
    }
    public void BindSelectedRole()
    {
        Commit();
        if (conditions.SelectedItem is not PcrGuideCondition condition || rolePicker.SelectedItem is not PcrGuideUnit unit)
        { status.Text = "请先选择一行条件及完整角色名称，再关联。"; return; }
        PcrGuideData.Bind(condition, unit);
        conditions.ItemsSource = null; conditions.ItemsSource = conditionRows; conditions.SelectedItem = condition;
        coverageConfirmed.IsChecked = false; Invalidate();
    }
    public void ImportCatalog(byte[] bytes, string fileName)
    {
        var imported = PcrGuideData.Catalog(bytes, fileName);
        var input = Capture(); input.Catalog = imported; input.SnapshotConfirmed = false;
        PcrGuideReview.Validate(input); Display(input); Invalidate();
        status.Text = $"已读取 {imported.Units.Count} 个角色。请关联条件中的角色；来源文件没有改动。";
    }
    public void ImportSnapshot(byte[] bytes, string fileName)
    {
        var current = Capture();
        var id = current.Snapshot?.AccountId ?? configuredAccount?.Invoke() ?? Newtonsoft.Json.Linq.JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes))["account_id"]?.ToString() ?? "";
        PublishSnapshot(bytes, fileName, id, current.Catalog == null ? null : accounts.Stamp(current.Catalog, id));
    }
    private void PublishSnapshot(byte[] bytes, string fileName, string accountId, string? expectedHash)
    {
        var current = Capture();
        var now = DateTimeOffset.Now;
        var catalog = current.Catalog ?? throw new InvalidDataException("请先导入静态角色表。");
        _ = PcrGuideData.AccountSnapshot(bytes, catalog, accountId, now);
        var input = PcrGuideData.Import(current, bytes, fileName, now);
        accounts.Save(bytes, catalog, accountId, now, expectedHash);
        Display(input); Invalidate();
        status.Text = "账号快照已保存；换队可复用。请核对账号、来源和原采集时间后确认，再检查并保存。";
    }
    private PcrGuideInput Reuse(PcrGuideInput input) => accounts.Reuse(input, configuredAccount?.Invoke() ?? input.Snapshot?.AccountId, DateTimeOffset.Now);
    public void ReuseAccount()
    {
        Display(Reuse(Capture())); Invalidate();
        status.Text = "已核对本机缓存；未登录、未刷新采集时间。请查看覆盖范围和缺项。";
    }
    public void ApplyTeamTable(string text)
    {
        var input = Reuse(PcrGuideDraft.ApplyTable(Capture(),text));
        Display(input); Invalidate(); tabs.SelectedIndex = 0;
        status.Text = $"已生成五人条件，沿用 {input.Facts.Count} 项仍对应的本机资料。请核对五人与 SET 后一次保存。";
    }
    public void ImportDraft(byte[] bytes)
    {
        if (dirty) { status.Text = "请先保存，或放弃编辑并重开，再导入另一份草稿。"; return; }
        var input = Reuse(PcrGuideDraft.Import(Capture(),bytes));
        Display(input); Invalidate(); tabs.SelectedIndex = 0;
        status.Text = $"已载入草稿，沿用 {input.Facts.Count} 项对应的本机资料。请核对五人与 SET 后一次保存；详细来源和练度缺项只提示。";
    }
    public string ExportDraft() => PcrGuideDraft.Export(Capture());
    public void LoadSavedTeam(string key)
    {
        if (dirty) { status.Text = "请先保存，或放弃编辑并重开，再切换队伍。"; return; }
        ImportDraft(library.Read(key));
    }
    private void ShowTeamSummary()
    {
        var members = conditionRows.Where(c => c.Field == "持有" && c.UnitId.HasValue).ToList();
        if (members.Count != 5) { teamSummary.Text = "当前五人尚未完整关联；导入草稿或生成五人条件后显示摘要。"; return; }
        string Actual(int? id, string field)
        {
            var values = factRows.Where(f => f.UnitId == id && f.Field == field && f.Confirmed && !string.IsNullOrWhiteSpace(f.Value)).ToList();
            return values.Count == 1 ? values[0].Value : "未知";
        }
        var sets = openingSets ?? PcrGuideTrial.SuggestedSets(new() { Conditions = conditionRows.ToList() });
        teamSummary.Text = fields["GuideStage"].Text + " · AUTO 开启\n" + string.Join("\n", members.Select(c =>
        {
            var set = sets?.GetValueOrDefault(c.UnitId!.Value);
            return $"{c.Character}（{c.UnitId}） · 持有 {Actual(c.UnitId, "持有")} · 等级 {Actual(c.UnitId, "等级")} / Rank {Actual(c.UnitId, "Rank")} · SET {(set == true ? "开" : set == false ? "关" : "待确认")}";
        })) + $"\n账号资料：{snapshot?.ObservedAt ?? "未采集"}；确认五人和开场设置后一次保存。";
    }
    private async void PickDraft()
    {
        try
        {
            if (dirty) { status.Text = "请先保存，或放弃编辑并重开，再导入另一份草稿。"; return; }
            var storage = TopLevel.GetTopLevel(this)?.StorageProvider ?? throw new InvalidOperationException("本机文件选择器不可用。");
            var files = await storage.OpenFilePickerAsync(new FilePickerOpenOptions { Title="导入攻略草稿",AllowMultiple=false,
                FileTypeFilter=[new FilePickerFileType("攻略草稿 JSON") { Patterns=["*.json"] }] });
            if (files.Count == 1) ImportDraft(PcrGuideData.ReadLocal(files[0].TryGetLocalPath() ?? throw new InvalidDataException("请选择本机文件。")));
        }
        catch (Exception e) { status.Text="草稿未导入，原资料保持："+e.Message; }
    }
    private async void SaveDraft()
    {
        try
        {
            var json = ExportDraft();
            var storage = TopLevel.GetTopLevel(this)?.StorageProvider ?? throw new InvalidOperationException("本机文件选择器不可用。");
            var file = await storage.SaveFilePickerAsync(new FilePickerSaveOptions { Title="导出攻略草稿（请选择新文件名）",
                SuggestedFileName="攻略草稿.guide.json",DefaultExtension="json" });
            if (file == null) return;
            var path = file.TryGetLocalPath() ?? throw new InvalidDataException("请选择本机文件。");
            if (!Path.IsPathFullyQualified(path) || path.StartsWith(@"\\") || path.StartsWith("//")) throw new InvalidDataException("请选择本机文件。");
            using var stream = new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.None);
            using var writer = new StreamWriter(stream,new System.Text.UTF8Encoding(false));
            writer.Write(json);
            status.Text="草稿已导出，当前试打配置未改。文件不含账号字段和报告；自由文字材料仍按原文保留。";
        }
        catch (IOException) { status.Text="草稿未导出。请使用新的文件名，并确认目录可写；已有文件没有覆盖。"; }
        catch (Exception e) { status.Text="草稿未导出："+e.Message; }
    }
    public void LoadIdentity()
    {
        var input = Capture();
        if (input.SourceUrl != PcrGuideIdentity.Url || !PcrGuideIdentity.Supports(input.GuideStage) || input.GuideServer != "国服 B 站")
        { status.Text = "此关卡原图尚未取得并校准，请先核对来源。"; return; }
        identity = PcrGuideIdentity.Load(PcrAlphaOwner.Repository, input.GuideStage);
        ShowIdentity(); Invalidate();
        status.Text = "已载入原图与独立全图库核对。请查看对应角色；原有条件不会被自动覆盖。";
    }
    public void ImportIdentity(byte[] bytes)
    {
        var imported = PcrGuideIdentity.Import(bytes,Capture());
        using (var stream = new MemoryStream(Convert.FromBase64String(imported.ImageBase64)))
        using (var image = new Bitmap(stream))
            if (image.PixelSize.Width != 344 || image.PixelSize.Height != 66) throw new InvalidDataException("原图尺寸不符合已校准布局。");
        identity = imported; requiresImageReview = true; sourceConfirmed.IsChecked = false;
        openingConfirmed.IsChecked = false; Invalidate();
        status.Text = "原图已导入；对照五人的编号与形态，再点击“确认五人和开场设置并保存”。导入分数仅作线索；已接入的原图可生成计划，其余材料保留为离线整理。";
    }
    private async void PickIdentity()
    {
        try
        {
            var storage = TopLevel.GetTopLevel(this)?.StorageProvider ?? throw new InvalidOperationException("本机文件选择器不可用。");
            var files = await storage.OpenFilePickerAsync(new FilePickerOpenOptions { Title="导入原图核对文件", AllowMultiple=false,
                FileTypeFilter=[new FilePickerFileType("原图核对 JSON") { Patterns=["*.json"] }] });
            if (files.Count == 1) ImportIdentity(PcrGuideData.ReadLocal(files[0].TryGetLocalPath() ?? throw new InvalidDataException("请选择本机文件。")));
        }
        catch (Exception e) { status.Text="原图未导入，原资料保持："+e.Message; }
    }
    public void ApplySourceTeam()
    {
        if (identity == null) { status.Text="请先载入所选关卡原图。"; return; }
        var input=Reuse(PcrGuideDraft.FromSource(Capture(),identity));
        Display(input); Invalidate(); tabs.SelectedIndex=0;
        status.Text="已按原图生成五人并复用本机账号资料。参考练度和 SET 原标记待填写；确认状态已清除，尚未保存。";
    }
    private void ShowIdentity()
    {
        identityPanel.Children.Clear();
        foreach (var bitmap in identityImages) bitmap.Dispose();
        identityImages.Clear();
        if (identity?.Reviewable() != true)
        { identityPanel.Children.Add(Note("尚无有效原图核对，不能直接用于试打。")); return; }
        using var stream = new MemoryStream(Convert.FromBase64String(identity.ImageBase64));
        var original = new Bitmap(stream); identityImages.Add(original);
        identityPanel.Children.Add(Note("攻略原图 → 角色目录头像（独立全图库匹配）；账号列仅表示已导入持有资料。"));
        if (identity.ImportedSource is {} evidence)
            identityPanel.Children.Add(Note($"外部原图：{identity.SourceUrl}\n定位：{evidence.Locator}\n取得日期：{evidence.RetrievedOn} · 原件 SHA256 {evidence.OriginalSha256}\n{(identity.Executable() ? "此原图已接入；确认五人与 SET 后可生成试打计划。" : "确认五人后可用于离线对照；尚未接入此材料的游戏执行。")}"));
        identityPanel.Children.Add(new Image { Source = original, Width = 556.5, Height = 99, HorizontalAlignment = HorizontalAlignment.Left });
        var rows = new WrapPanel();
        for (var index = 0; index < identity.Members.Count; index++)
        {
            var member = identity.Members[index];
            var cell = new StackPanel { Width = 168, Margin = new Thickness(0, 0, 8, 8), Spacing = 4 };
            var pair = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
            pair.Children.Add(new Border { Width = 60, Height = 60, Background = new ImageBrush(original)
                { SourceRect = new RelativeRect(50 + index * 50, 3, 50, 50, RelativeUnit.Absolute), Stretch = Stretch.Fill } });
            pair.Children.Add(Note("→"));
            var iconPath = Path.Combine(PcrAlphaOwner.Repository, "assets", "labyrinth", "character-pack", "icons", "characters", $"icon_unit_{member.IconUnitId}.png");
            if (File.Exists(iconPath))
            {
                var icon = new Bitmap(iconPath); identityImages.Add(icon);
                pair.Children.Add(new Image { Source = icon, Width = 60, Height = 60 });
            }
            else pair.Children.Add(Note("目录图缺失"));
            cell.Children.Add(pair);
            var name = catalog?.Units.FirstOrDefault(u => u.UnitId == member.UnitId)?.Name ?? member.Name;
            cell.Children.Add(Note($"{name}\n{member.UnitId} · 火"));
            if (identity.NeedsConfirmation(member))
            {
                var confirmationText = member.ConfirmedFor == identity.ConfirmationBinding(member, PcrGuideIdentity.MaterialHash(new() { Material=fields["Material"].Text ?? "" }))
                    ? "已确认编号与形态" : "待确认编号与形态";
                cell.Children.Add(Note(confirmationText));
            }
            var inPlan = conditionRows.Any(c => c.UnitId == member.UnitId && c.Field == "持有");
            var owned = snapshotConfirmed.IsChecked == true && factRows.Any(f => f.UnitId == member.UnitId && f.Field == "持有" && f.Value == "是" && f.Confirmed);
            cell.Children.Add(Note($"条件：{(inPlan ? "已对应" : "未对应")}\n账号：{(owned ? "已持有" : "待核对")}"));
            rows.Children.Add(cell);
        }
        identityPanel.Children.Add(rows);
        identityPanel.Children.Add(Note("实际编队尚未在此面板读取；开战入口将再次核对原图、计划与当帧五人。任何身份冲突都停止，参考练度差异仅提醒。"));
    }
    public void ConfirmPortrait(int index)
    {
        if (identity == null) throw new InvalidOperationException("请先载入原图。");
        identity.Confirm(index,Capture()); Invalidate();
        status.Text="已记录此角色的来源确认；原识别分数保留，实际编队仍须程序读回。";
    }
    private void ResetOpening()
    { openingSets=PcrGuideTrial.SuggestedSets(Capture()); openingConfirmed.IsChecked=false; ShowOpening(); }
    private void ShowOpening()
    {
        openingPanel.Children.Clear();
        var input=new PcrGuideInput { Conditions=conditionRows.ToList() }; var suggested=openingSets ?? PcrGuideTrial.SuggestedSets(input);
        if (suggested == null) { openingPanel.Children.Add(Note("先关联五个角色，才能逐人设置。")); return; }
        if (openingSets == null && identity?.ImportedSource == null && identity?.Valid()==true && identity.Stage is not ("红焰深域 8-3" or "红焰深域 8-4" or "红焰深域 8-5" or "红焰深域 8-6"))
            suggested=suggested.ToDictionary(p=>p.Key,p=>(bool?)true);
        foreach(var (id,value) in suggested)
        {
            var marks=string.Join(" / ",conditionRows.Where(c=>c.UnitId==id && c.Field is "SET" or "SET标记").Select(c=>c.Expected));
            string Label(bool? state) => $"{catalog?.Units.FirstOrDefault(u=>u.UnitId==id)?.Name ?? id.ToString()}\nSET：{(state==true ? "开启" : state==false ? "关闭" : "待确认")}\n原标记：{(marks.Length==0 ? "未注明" : marks)}";
            var box=new CheckBox { IsThreeState=true, IsChecked=value, Content=Label(value), Width=164 };
            Avalonia.Automation.AutomationProperties.SetName(box,$"{id} 角色 SET 开关");
            box.IsCheckedChanged+=(_,_)=>{ openingSets ??= suggested.ToDictionary(p=>p.Key,p=>p.Value); openingSets[id]=box.IsChecked; box.Content=Label(box.IsChecked); openingConfirmed.IsChecked=false; Invalidate(); };
            openingPanel.Children.Add(box);
        }
    }
    public void ClearAccount()
    {
        var input = Capture(); input.Facts.Clear(); input.Snapshot = null; input.SnapshotConfirmed = false;
        input.AccountLabel = input.AccountServer = input.AccountVersion = input.AccountObservedOn = "";
        Display(input); Invalidate();
    }
    public async void ReadSnapshot()
    {
        if (syncCancel != null) return;
        if (readSnapshot == null) { status.Text = "请从应用内攻略检查入口读取账号。"; return; }
        using var cancel = new CancellationTokenSource();
        syncCancel = cancel;
        status.Text = "正在登录并读取全部持有角色；可随时停止。模拟器可能提示连接中断。";
        try
        {
            var input = Capture();
            var fingerprint = PcrGuideReview.Fingerprint(input);
            var accountId = configuredAccount?.Invoke() ?? input.Snapshot?.AccountId;
            var stamp = input.Catalog != null && accountId != null ? accounts.Stamp(input.Catalog, accountId) : null;
            var bytes = await readSnapshot(input, cancel.Token);
            cancel.Token.ThrowIfCancellationRequested();
            if (fingerprint != PcrGuideReview.Fingerprint(Capture()))
            { status.Text = "读取期间材料或账号资料已改变，本次结果未写入；请按新条件重新读取。"; return; }
            accountId ??= Newtonsoft.Json.Linq.JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes))["account_id"]?.ToString() ?? "";
            PublishSnapshot(bytes, "protocol-account-snapshot.json", accountId, stamp);
        }
        catch (OperationCanceledException) { status.Text = "读取已停止或超时，原资料保持。"; }
        catch (Exception ex)
        { status.Text = ex is InvalidOperationException or InvalidDataException ? ex.Message : "账号读取未完成，原资料保持；请核对账号和运行组件。"; }
        finally { syncCancel = null; }
    }
    private async void PickData(bool account)
    {
        try
        {
            var storage = TopLevel.GetTopLevel(this)?.StorageProvider ?? throw new InvalidOperationException("本机文件选择器不可用。");
            var files = await storage.OpenFilePickerAsync(new FilePickerOpenOptions { Title = account ? "导入规范化账号快照" : "导入静态角色表",
                AllowMultiple = false, FileTypeFilter = [new FilePickerFileType("JSON") { Patterns = ["*.json"] }] });
            if (files.Count != 1) return;
            var path = files[0].TryGetLocalPath() ?? throw new InvalidDataException("请选择本机文件。");
            var bytes = PcrGuideData.ReadLocal(path);
            if (account) ImportSnapshot(bytes, Path.GetFileName(path)); else ImportCatalog(bytes, Path.GetFileName(path));
        }
        catch (Exception ex) { status.Text = "导入未完成，原资料保持：" + ex.Message; }
    }
}
