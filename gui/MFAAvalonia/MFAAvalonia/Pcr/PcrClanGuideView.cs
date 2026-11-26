using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using MFAAvalonia.Helper;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed partial class PcrClanGuideView : UserControl
{
    private sealed record Choice(string Value, string Label) { public override string ToString() => Label; }
    private sealed class Member
    {
        public TextBox Role { get; } = new() { Watermark = "完整角色名称或编号", MinWidth = 180 };
        public TextBox Star { get; } = new() { Watermark = "1～6；留空用账号实值", MinWidth = 130 };
        public ComboBox Set { get; } = Toggle(false);
    }
    private readonly string repository;
    private readonly string statePath;
    private readonly string configDirectory;
    private readonly PcrClanDailyPlanView dailyPlan;
    // Instance-local readback seams retain the normal file operations by default.
    internal Func<string, string?> ReadSavedModeStamp { get; set; } = PcrClanGuide.Stamp;
    internal Func<string, (PcrClanGuideDocument Document, string? Stamp)> ReadSavedGuide { get; set; } = path =>
    {
        var value = PcrClanGuide.Read(path, out var stamp);
        return (value, stamp);
    };
    private readonly ComboBox executionMode = new() { ItemsSource = new[] { new Choice(PcrClanDailyExecution.Simulation, "单次模拟"), new Choice(PcrClanDailyExecution.FormalDaily, "正式三刀") }, MinWidth = 260 };
    private readonly ComboBox battleSpeed = new() { ItemsSource = new[] { new Choice("1", "1 倍速"), new Choice("2", "2 倍速"), new Choice("4", "4 倍速") }, MinWidth = 120 };
    private readonly TextBlock executionSummary = Note("");
    private readonly TextBlock calibrationSummary = Note("");
    private readonly TextBlock calibrationDetails = Note("");
    private readonly TextBlock speedLabel = Note("模拟战倍速");
    private readonly TextBlock formalSpeed = new() { Text = "固定 2 倍速 · 单次任务最长 60 分钟", IsVisible = false };
    private readonly Expander workSelection = new() { Header = "选择或更换作业", IsExpanded = true };
    private readonly Expander dailyDetails = new() { Header = "一天三刀目标" };
    private Button stopFetch = null!;
    private readonly StackPanel formalControls = new() { Spacing = 6, IsVisible = false };
    private readonly TextBlock selectionSummary = Note("尚未选择作业。");
    private readonly Expander sourceDetails = new() { Header = "作业原图与来源", IsExpanded = false };
    private PcrClanDailyExecutionDocument execution = new();
    private string? executionHash;
    private bool executionLoaded;
    private PcrClanGuideDocument document = new();
    private string? loadedHash;
    private bool loaded;
    private bool displaying;
    private bool unparsedSwitches;
    private PcrGuideCatalog? catalog;
    private JObject? bundle;
    private Bitmap? bitmap;
    private Bitmap? previewBitmap;
    private CancellationTokenSource? fetchCancel;
    private readonly TextBox video = new() { Watermark = "粘贴 B 站公会战作业视频链接", MinWidth = 300 };
    private readonly ComboBox page = new() { PlaceholderText = "分 P / 阶段", MinWidth = 180 };
    private readonly ComboBox package = new() { PlaceholderText = "套餐", MinWidth = 180 };
    private readonly ComboBox teams = new() { PlaceholderText = "选择队伍", MinWidth = 320, MaxWidth = 600 };
    private readonly ComboBox accounts = new() { PlaceholderText = "选择原账号缓存", MinWidth = 320, MaxWidth = 540 };
    private readonly Dictionary<string, TextBox> fields = new();
    private readonly Member[] members = Enumerable.Range(0, 5).Select(_ => new Member()).ToArray();
    private readonly ComboBox auto = Toggle(false);
    private readonly ComboBox support = new() { ItemsSource = new[] { new Choice("none", "全部自有"), new Choice("qsjs", "缺谁借谁（qsjs）"), new Choice("specified", "指定支援") }, MinWidth = 180 };
    private readonly TextBox supportRole = new() { Watermark = "指定支援的完整角色名称或编号" };
    private readonly TextBox switches = new() { AcceptsReturn = true, MinHeight = 130, TextWrapping = TextWrapping.Wrap,
        Watermark = "剩余秒数 | AUTO（不变 / 开 / 关）| SET（角色名=开；角色名=关）\n例如：75 | 不变 | 100101=关。原轴未知时可明确写：无切换" };
    private readonly TextBlock timingStatus = Note("");
    private readonly Image image = new() { Stretch = Stretch.Uniform, MaxHeight = 420 };
    private readonly Image preview = new() { Stretch = Stretch.Uniform, MaxHeight = 220 };
    private readonly TextBlock imageNote = Note("请选择一队并对照原图。未知字段保留待确认。练度、EX 和强化差异只作提示。" );
    private readonly TextBlock status = Note("");
    private readonly TextBlock videoStatus = Note("");
    private readonly TextBlock cacheNotice = Note("");
    private readonly TextBlock accountNote = Note("");
    public string StatusText => status.Text ?? "";
    public string ExecutionMode => executionLoaded ? execution.Mode : "unavailable";
    public string StartHint => ExecutionMode switch
    {
        PcrClanDailyExecution.Simulation => "模拟当前队伍一局",
        PcrClanDailyExecution.FormalDaily => "准备正式三刀，核对现场后另行确认普通挑战和补偿",
        _ => "会战执行模式尚未确认"
    };
    public int BattleSpeed => execution.BattleSpeed;
    public string ExecutionSummaryText => executionSummary.Text ?? "";
    public event EventHandler? ExecutionModeChanged;
    public event EventHandler? AccountRefreshRequested;
    public string? ExecutionConfirmationForStart
    {
        get
        {
            if (!executionLoaded || !HasCurrentConfirmation) return null;
            if (execution.Mode == PcrClanDailyExecution.Simulation) return Capture().ConfirmedFor;
            try { return (string?)PcrClanDailyExecution.CreatePlan(configDirectory, repository, DateTimeOffset.Now)["confirmed_for"]; }
            catch { return null; }
        }
    }
    public bool HasCurrentConfirmation
    {
        get { if (!loaded || loading || dailyPlan.IsLoading) return false; try { var value = Capture(); return value.ConfirmedFor != null && value.ConfirmedFor == (string?)PcrClanGuide.BuildPlan(value, configDirectory, repository, DateTimeOffset.Now)["confirmed_for"]; } catch { return false; } }
    }

    public PcrClanGuideView() : this(null) { }
    public PcrClanGuideView(string? repository) : this(repository ?? PcrAlphaOwner.Repository, PcrClanGuide.StatePath, AppPaths.ConfigDirectory, true) { }
    public PcrClanGuideView(string repository, string statePath, string configDirectory) : this(repository, statePath, configDirectory, false) { }
    private PcrClanGuideView(string repository, string statePath, string configDirectory, bool deferLoad)
    {
        this.repository = Path.GetFullPath(repository); this.statePath = Path.GetFullPath(statePath); this.configDirectory = Path.GetFullPath(configDirectory);
        var guide = new StackPanel { Spacing = 10 };
        guide.Children.Add(new TextBlock { Text = "公会战", FontSize = 22, FontWeight = FontWeight.SemiBold });
        Avalonia.Automation.AutomationProperties.SetName(executionMode, "会战执行模式");
        Avalonia.Automation.AutomationProperties.SetName(battleSpeed, "模拟战倍速");
        guide.Children.Add(Buttons(Note("执行模式"), executionMode, speedLabel, battleSpeed, formalSpeed));
        guide.Children.Add(executionSummary);
        guide.Children.Add(calibrationSummary);
        guide.Children.Add(new TextBlock { Text = "账号", FontSize = 16, FontWeight = FontWeight.SemiBold, Margin = new Thickness(0, 8, 0, 0) });
        guide.Children.Add(Buttons(accounts, Button("账号资料与刷新", () => AccountRefreshRequested?.Invoke(this, EventArgs.Empty))));
        guide.Children.Add(accountNote);
        guide.Children.Add(new Expander { Header = "更多账号操作", Content = Buttons(Button("选用最新账号资料", () => _ = UseSelectedAccountAsync())) });
        formalControls.Children.Add(Note("保存三目标不授权消费。Start 会先读取实时 BOSS 实例和次数，再分别确认普通挑战与补偿。"));
        formalControls.Children.Add(Buttons(Button("预览正式三目标（不执行）", PreviewDaily), Button("核对并保存正式三目标", ConfirmDaily)));
        guide.Children.Add(new TextBlock { Text = "作业", FontSize = 16, FontWeight = FontWeight.SemiBold, Margin = new Thickness(0, 8, 0, 0) });
        guide.Children.Add(selectionSummary);
        var chooseWork = new StackPanel { Spacing = 8 };
        chooseWork.Children.Add(video);
        stopFetch = Button("停止获取", () => fetchCancel?.Cancel()); stopFetch.IsVisible = false;
        chooseWork.Children.Add(Buttons(Button("获取视频作业", async () => await FetchVideo()), Button("导入已获取的作业包", PickBundle), stopFetch));
        Avalonia.Automation.AutomationProperties.SetName(videoStatus, "视频作业获取状态");
        chooseWork.Children.Add(videoStatus);
        Avalonia.Automation.AutomationProperties.SetName(cacheNotice, "本地视频缓存提示");
        chooseWork.Children.Add(cacheNotice);
        chooseWork.Children.Add(Buttons(page, package, teams));
        workSelection.Content = chooseWork; guide.Children.Add(workSelection);
        var source = new StackPanel { Spacing = 8 }; source.Children.Add(imageNote); source.Children.Add(image); source.Children.Add(preview); source.Children.Add(calibrationDetails);
        sourceDetails.Header = "作业原图、来源与校准";
        sourceDetails.Content = source; guide.Children.Add(sourceDetails);
        var target = new Grid { ColumnDefinitions = new ColumnDefinitions("*,*,*"), RowDefinitions = new RowDefinitions("Auto,Auto"), ColumnSpacing = 12, RowSpacing = 8 };
        var reference = new StackPanel { Spacing = 8 };
        var targetFields = new[] { ("period", "期次（YYYY-MM）"), ("phase", "阶段（B / C / BC / D / E）"), ("boss", "BOSS 序号（1～5）"), ("round_min", "起始周目"), ("round_max", "结束周目（不限留空）"), ("boss_name", "BOSS 名称（可留空）"), ("package", "套餐名称"), ("team_label", "队伍名称"), ("reference_damage", "作者参考伤害（可留空）") };
        for (var index = 0; index < targetFields.Length; index++)
        {
            var (key, label) = targetFields[index]; var panel = new StackPanel { Spacing = 3 }; panel.Children.Add(Note(label));
            var box = new TextBox(); Avalonia.Automation.AutomationProperties.SetName(box, label); fields.Add(key, box); panel.Children.Add(box); Watch(box);
            if (index < 6) { Grid.SetRow(panel, index / 3); Grid.SetColumn(panel, index % 3); target.Children.Add(panel); }
            else reference.Children.Add(panel);
        }
        guide.Children.Add(new Expander { Header = "目标 BOSS 与周目", Content = target });
        guide.Children.Add(new TextBlock { Text = "核对阵容", FontSize = 16, FontWeight = FontWeight.SemiBold, Margin = new Thickness(0, 8, 0, 0) });
        guide.Children.Add(Note("五人按作业顺序填写。角色使用原角色目录中的完整名称或编号；星级优先作业明确值，未注明时留空使用原账号的战斗星级。"));
        var roster = new Grid { ColumnDefinitions = new ColumnDefinitions("Auto,*,Auto,Auto"), RowDefinitions = new RowDefinitions("Auto,Auto,Auto,Auto,Auto,Auto"), ColumnSpacing = 10, RowSpacing = 6 };
        foreach (var (text, col) in new[] { ("顺序", 0), ("角色 / 形态", 1), ("战斗星级", 2), ("开场 SET", 3) }) { var label = Note(text); Grid.SetColumn(label, col); roster.Children.Add(label); }
        for (var n = 0; n < 5; n++)
        {
            var row = members[n]; var index = Note((n + 1).ToString()); Grid.SetRow(index, n + 1); roster.Children.Add(index);
            foreach (var (control, col) in new (Control, int)[] { (row.Role, 1), (row.Star, 2), (row.Set, 3) }) { Grid.SetColumn(control, col); Grid.SetRow(control, n + 1); roster.Children.Add(control); }
            Avalonia.Automation.AutomationProperties.SetName(row.Role, $"第 {n + 1} 人完整角色名称或编号");
            Avalonia.Automation.AutomationProperties.SetName(row.Star, $"第 {n + 1} 人战斗星级");
            Avalonia.Automation.AutomationProperties.SetName(row.Set, $"第 {n + 1} 人开场 SET");
            Watch(row.Role); Watch(row.Star); row.Set.SelectionChanged += (_, _) => Invalidate();
        }
        guide.Children.Add(roster);
        guide.Children.Add(Buttons(Note("开场 AUTO"), auto, Note("支援"), support)); guide.Children.Add(supportRole);
        guide.Children.Add(new Expander { Header = "支援说明", Content = Note("缺谁借谁：五人齐全用本人，缺一人借该角色；缺多名或支援不匹配时停止。指定支援只借本队所选角色，不替换角色。当前全自有校准不开放借人。") });
        var timing = new StackPanel { Spacing = 6 }; timing.Children.Add(Note("每行：剩余秒数 | AUTO（不变 / 开 / 关）| SET（完整角色名或编号=开/关，用分号分隔）。秒数严格递减。未解析的原轴须填写支持的秒数，或明确写“无切换”；空白不代替未知。错过秒数、读回未知或断线即停，不补点。")); timing.Children.Add(switches);
        timing.Children.Add(timingStatus);
        guide.Children.Add(new Expander { Header = "战中 SET / AUTO 秒数轴", Content = timing });
        guide.Children.Add(Buttons(Button("核对五人和操作并保存", () => _ = PersistAsync(true)), Button("暂存草稿", () => _ = PersistAsync(false)), Button("放弃编辑并重开", () => _ = ReloadAsync())));
        guide.Children.Add(status);
        guide.Children.Add(new Expander { Header = "作业名称与参考伤害", Content = reference });
        dailyPlan = new PcrClanDailyPlanView(this.repository, PcrClanDailyPlan.PathFor(this.configDirectory), this.configDirectory,
            this.statePath, Capture, () => HasCurrentConfirmation, LoadReviewed, LoadReviewedAsync);
        var dailySection = new StackPanel { Spacing = 8 }; dailySection.Children.Add(dailyPlan); dailySection.Children.Add(formalControls);
        dailyDetails.Content = dailySection; guide.Children.Add(dailyDetails);
        Content = new ScrollViewer { Content = guide, Margin = new Thickness(16), HorizontalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Disabled };
        page.SelectionChanged += (_, _) => { if (!displaying) Guard(() => PopulatePackages(true), true); };
        package.SelectionChanged += (_, _) => { if (!displaying) Guard(() => PopulateTeams(true), true); };
        teams.SelectionChanged += (_, _) => { if (!displaying && teams.SelectedItem is Choice choice) _ = SelectJobAsync(choice.Value); };
        accounts.SelectionChanged += (_, _) =>
        {
            if (displaying) return;
            _ = UseSelectedAccountAsync();
        };
        auto.SelectionChanged += (_, _) => Invalidate(); support.SelectionChanged += (_, _) => { supportRole.IsVisible = (support.SelectedItem as Choice)?.Value == "specified"; Invalidate(); if (!displaying) UpdateCalibrationSummary(); };
        Watch(supportRole); Watch(switches); Watch(video);
        executionMode.SelectionChanged += (_, _) => { if (!displaying && executionMode.SelectedItem is Choice selected) GuardExecution(() => ChangeExecutionMode(selected.Value)); };
        battleSpeed.SelectionChanged += (_, _) => { if (!displaying && battleSpeed.SelectedItem is Choice selected) GuardExecution(() => ChangeBattleSpeed(int.Parse(selected.Value, CultureInfo.InvariantCulture))); };
        foreach (var (control, name) in new (Control, string)[] { (video, "公会战视频链接"), (page, "视频分 P"), (package, "视频套餐"), (teams, "视频队伍"), (accounts, "共用账号缓存"), (auto, "开场 AUTO"), (support, "支援方式"), (supportRole, "指定支援角色"), (switches, "剩余秒数 SET AUTO 切换轴") })
            Avalonia.Automation.AutomationProperties.SetName(control, name);
        DetachedFromVisualTree += (_, _) => { fetchCancel?.Cancel(); CancelPendingLoad(); ReleaseImages(); };
        sourceDetails.PropertyChanged += (_, e) =>
        {
            if (e.Property != Expander.IsExpandedProperty) return;
            if (sourceDetails.IsExpanded) RestoreImages();
            else ReleaseImages();
        };
        AttachedToVisualTree += (_, _) =>
        {
            if (sourceDetails.IsExpanded) RestoreImages();
        };
        if (deferLoad) _ = ReloadAsync(); else Reload();
    }
    private void RestoreImages() => _ = RestoreImagesAsync();
    private void ReleaseImages()
    {
        imageVersion++;
        image.Source = null; preview.Source = null;
        bitmap?.Dispose(); bitmap = null; previewBitmap?.Dispose(); previewBitmap = null;
    }
    private static ComboBox Toggle(bool unchanged) => new() { MinWidth = 100, ItemsSource = new[] { unchanged ? "不变" : "待确认", "开启", "关闭" }, SelectedIndex = 0 };
    private static TextBlock Note(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap };
    private static WrapPanel Buttons(params Control[] controls)
    { var panel = new WrapPanel(); foreach (var item in controls) { item.Margin = new Thickness(0, 0, 8, 5); panel.Children.Add(item); } return panel; }
    private Button Button(string label, Action action)
    { var button = new Button { Content = label }; button.Click += (_, _) => { try { action(); } catch (Exception ex) { status.Text = "未完成：" + ex.Message; } }; return button; }
    private void SetVideoStatus(string text) { videoStatus.Text = text; status.Text = text; }
    private void Guard(Action action, bool videoChoice = false)
    { try { action(); } catch (Exception ex) { document.ConfirmedFor = null; var message = "作业不可用，尚未确认：" + ex.Message; if (videoChoice) SetVideoStatus(message); else status.Text = message; } }
    private void Watch(TextBox box) => box.PropertyChanged += (_, e) => { if (e.Property == TextBox.TextProperty) Invalidate(); };
    private void Invalidate()
    { if (displaying) return; CancelPendingLoad(); document.ConfirmedFor = null; execution.ConfirmedFor = null; UpdateSelectionSummary(); status.Text = "内容已修改，请对照原图后一次确认保存。"; }
    private void UpdateSelectionSummary()
    {
        var roster = members.Select(m => m.Role.Text?.Trim()).Where(name => !string.IsNullOrEmpty(name)).ToArray();
        if (string.IsNullOrEmpty(document.SelectedJobId) && roster.Length == 0 && fields.Values.All(field => string.IsNullOrWhiteSpace(field.Text)))
        {
            selectionSummary.Text = "尚未选择作业。";
            return;
        }
        string Value(string key) => fields[key].Text?.Trim() is { Length: > 0 } text ? text : "待填写";
        selectionSummary.Text = $"当前队伍：{Value("team_label")} · BOSS {Value("boss")} · {Value("phase")} 阶段 · {Value("period")}";
        if (roster.Length > 0) selectionSummary.Text += Environment.NewLine + string.Join(" / ", roster);
    }
    private static bool? State(ComboBox box) => box.SelectedIndex == 1 ? true : box.SelectedIndex == 2 ? false : null;
    private static bool? ParseState(string value) => value.Trim() switch { "开" or "开启" or "true" => true, "关" or "关闭" or "false" => false, "" or "不变" => null, _ => throw new InvalidDataException("开关只填写开、关或不变。") };
    private static JToken Numeric(string? value)
    { if (string.IsNullOrWhiteSpace(value)) return JValue.CreateNull(); return long.TryParse(value.Trim(), NumberStyles.None, CultureInfo.InvariantCulture, out var number) ? new JValue(number) : new JValue(value.Trim()); }
    private int Resolve(string text)
    {
        text = text.Trim(); var value = text.Split('·').Last().Trim();
        if (int.TryParse(value, out var id) && catalog?.Units.Any(u => u.UnitId == id) == true) return id;
        var matches = catalog?.Units.Where(u => u.Name == text).ToArray();
        if (matches?.Length != 1) throw new InvalidDataException("角色未对应原目录：" + (text.Length == 0 ? "尚未填写" : text));
        return matches[0].UnitId;
    }
    public PcrClanGuideDocument Capture()
    {
        var captured = JsonConvert.DeserializeObject<PcrClanGuideDocument>(JsonConvert.SerializeObject(document))!;
        var job = captured.Job;
        foreach (var pair in fields) job[pair.Key] = pair.Key is "boss" or "round_min" or "round_max" or "reference_damage" ? Numeric(pair.Value.Text) : new JValue(pair.Value.Text?.Trim() ?? "");
        var ids = new JArray(); var stars = new JArray(); var sets = new JObject();
        foreach (var row in members)
        {
            JToken id;
            try { id = new JValue(Resolve(row.Role.Text ?? "")); } catch { id = JValue.CreateNull(); }
            ids.Add(id); stars.Add(Numeric(row.Star.Text));
            if (id.Type == JTokenType.Integer) sets[id.ToString()] = State(row.Set).HasValue ? new JValue(State(row.Set)) : JValue.CreateNull();
        }
        job["unit_ids"] = ids; job["stars"] = stars; job["set_by_unit"] = sets; job["auto"] = State(auto).HasValue ? new JValue(State(auto)) : JValue.CreateNull();
        job["support_mode"] = (support.SelectedItem as Choice)?.Value ?? "unknown";
        job["support_unit_id"] = JValue.CreateNull();
        if ((support.SelectedItem as Choice)?.Value == "specified")
        {
            try { job["support_unit_id"] = Resolve(supportRole.Text ?? ""); }
            catch { /* An unfinished support choice remains a draft, never an executable plan. */ }
        }
        var timeline = new JArray();
        var axis = (switches.Text ?? "").Trim();
        foreach (var line in (axis == "无切换" ? "" : axis).Split('\n', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries))
        {
            var cells = line.Split('|');
            if (cells.Length != 3) throw new InvalidDataException("秒数轴每行需有三列，用 | 分隔。");
            var values = new JObject();
            foreach (var setting in cells[2].Split(new[] { ';', '；' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            {
                var split = setting.Split('=');
                if (split.Length != 2) throw new InvalidDataException("SET 切换填写角色=开或角色=关。");
                var id = Resolve(split[0]).ToString(CultureInfo.InvariantCulture);
                if (values.ContainsKey(id)) throw new InvalidDataException("同一秒的角色 SET 重复。");
                var state = ParseState(split[1]); if (!state.HasValue) throw new InvalidDataException("SET 差异必须明确开或关。");
                values[id] = state.Value;
            }
            var autoState = ParseState(cells[1]);
            timeline.Add(new JObject { ["remaining_seconds"] = Numeric(cells[0]), ["auto"] = autoState.HasValue ? new JValue(autoState) : JValue.CreateNull(), ["set_by_unit"] = values });
        }
        job["switches"] = axis.Length == 0 && unparsedSwitches ? JValue.CreateNull() : timeline;
        return captured;
    }
    private string RoleName(JToken? token)
    { var id = token?.Type == JTokenType.Integer ? (int?)token : null; return id.HasValue ? catalog?.Units.FirstOrDefault(u => u.UnitId == id)?.ToString() ?? id.ToString()! : ""; }
    public void Display(PcrClanGuideDocument value)
    {
        CancelPendingLoad();
        using var data = PrepareDisplay(value, sourceDetails.IsExpanded);
        ApplyDisplay(data);
    }
    private void ApplyDisplay(DisplayData data)
    {
        displaying = true;
        try
        {
            document = data.Document; catalog = data.Catalog;
            workSelection.IsExpanded = string.IsNullOrEmpty(document.SelectedJobId);
            var job = document.Job;
            foreach (var pair in fields) pair.Value.Text = job[pair.Key]?.Type == JTokenType.Null ? "" : job[pair.Key]?.ToString() ?? "";
            var ids = job["unit_ids"] as JArray; var stars = job["stars"] as JArray;
            for (var index = 0; index < members.Length; index++)
            {
                var id = ids?.ElementAtOrDefault(index); var row = members[index];
                var candidate = job["members"] is JArray proposals ? proposals.ElementAtOrDefault(index) : null;
                var proposal = candidate?["proposal_unit_id"];
                row.Role.Text = RoleName(id?.Type == JTokenType.Integer ? id : proposal);
                row.Star.Text = stars?.ElementAtOrDefault(index)?.Type == JTokenType.Null ? "" : stars?.ElementAtOrDefault(index)?.ToString() ?? "";
                var set = id?.Type == JTokenType.Integer ? job["set_by_unit"]?[id.ToString()] : null;
                if (set?.Type != JTokenType.Boolean && candidate?["set"]?.Type == JTokenType.Boolean) set = candidate["set"];
                row.Set.SelectedIndex = set?.Type == JTokenType.Boolean ? ((bool)set ? 1 : 2) : 0;
            }
            auto.SelectedIndex = job["auto"]?.Type == JTokenType.Boolean ? ((bool)job["auto"]! ? 1 : 2) : 0;
            support.SelectedItem = ((IEnumerable<Choice>)support.ItemsSource!).FirstOrDefault(c => c.Value == (string?)job["support_mode"]);
            supportRole.Text = RoleName(job["support_unit_id"]); supportRole.IsVisible = (string?)job["support_mode"] == "specified";
            unparsedSwitches = job["switches"] is not JArray;
            switches.Text = job["switches"] is JArray timeline ? string.Join("\n", timeline.Select(step => $"{step["remaining_seconds"]} | {(step["auto"]?.Type == JTokenType.Boolean ? ((bool)step["auto"]! ? "开" : "关") : "不变")} | " + string.Join("；", (step["set_by_unit"] as JObject)?.Properties().Select(p => p.Name + "=" + (p.Value.Type == JTokenType.Boolean ? ((bool)p.Value ? "开" : "关") : "待确认")) ?? Array.Empty<string>()))) : "";
            timingStatus.Text = unparsedSwitches ? "原轴尚未解析；当前空白表示未知，不能执行。请补秒数轴，或明确写“无切换”。" : "已导入明确的操作轴；空白表示原作业没有战中切换。";
            video.Text = (string?)job["source"]?["url"] ?? "";
            ReleaseImages();
            bitmap = data.Image; previewBitmap = data.Preview; data.Image = data.Preview = null;
            image.Source = bitmap; preview.Source = previewBitmap;
            imageNote.Text = $"原图：P{job["source"]?["page"]} · {job["source"]?["at_seconds"]} 秒。请对照原图核对五人、星级和操作。练度、EX 和强化差异只作提示。";
            if (job["members"] is JArray candidateMembers && candidateMembers.Any(m => m["unit_id"]?.Type != JTokenType.Integer && m["proposal_unit_id"]?.Type == JTokenType.Integer))
                imageNote.Text += "\n角色栏预填的是识别候选，请结合完整原图与局部放大核对形态；候选尚未确认。";
            if (job["warnings"] is JArray warnings && warnings.Count > 0) imageNote.Text += "\n" + string.Join("；", warnings.Values<string>());
            ApplyAccount(data);
        }
        finally { displaying = false; }
        UpdateSelectionSummary();
    }
    public void LoadBundle(string path) => LoadBundle(path, null);
    private void LoadBundle(string path, int? preferredPage)
    {
        var next = PcrClanGuide.Bundle(path);
        var choices = ((JArray)next["pages"]!).Select(p => new Choice(p["page"]!.ToString(), $"P{p["page"]} · {p["label"]}")).ToArray();
        var selected = preferredPage == null ? 0 : Array.FindIndex(choices, c => c.Value == preferredPage.Value.ToString(CultureInfo.InvariantCulture));
        if (selected < 0) throw new InvalidDataException("缓存中没有对应的分 P。");
        cacheNotice.Text = "";
        bundle = next;
        document.BundlePath = Path.GetFullPath(path); document.BundleSha256 = PcrClanGuide.Stamp(path)!; document.ConfirmedFor = null;
        displaying = true;
        try { page.ItemsSource = choices; page.SelectedIndex = selected; }
        finally { displaying = false; }
        PopulatePackages();
        workSelection.IsExpanded = true;
        SetVideoStatus(teams.SelectedItem is Choice ? "已载入视频作业，请选择分 P、套餐和队伍并核对。" : "当前分 P 未识别到作业，请选择其他分 P。");
    }
    private void PopulatePackages(bool asynchronously = false)
    {
        if (bundle == null) return;
        var number = (page.SelectedItem as Choice)?.Value;
        var list = ((JArray)bundle["jobs"]!).OfType<JObject>().Where(j => j["source"]?["page"]?.ToString() == number).Select(j => (string?)j["package"] ?? "").Distinct().ToArray();
        displaying = true;
        try { package.ItemsSource = list.Select(p => new Choice(p, p.Length == 0 ? "待核对套餐" : p)).ToArray(); package.SelectedIndex = 0; }
        finally { displaying = false; }
        PopulateTeams(asynchronously);
    }
    private void PopulateTeams(bool asynchronously = false)
    {
        if (bundle == null) return;
        var number = (page.SelectedItem as Choice)?.Value; var name = (package.SelectedItem as Choice)?.Value;
        var list = ((JArray)bundle["jobs"]!).OfType<JObject>().Where(j => j["source"]?["page"]?.ToString() == number && ((string?)j["package"] ?? "") == name).ToArray();
        displaying = true;
        try { teams.ItemsSource = list.Select(j => new Choice((string)j["source"]!["job_id"]!, $"{j["team_label"] ?? "待核对队伍"} · BOSS {j["boss"]} · {j["source"]?["at_seconds"]} 秒")).ToArray(); teams.SelectedIndex = 0; }
        finally { displaying = false; }
        if (teams.SelectedItem is Choice selected) { if (asynchronously) _ = SelectJobAsync(selected.Value); else SelectJob(selected.Value); }
        else { Invalidate(); SetVideoStatus("当前分 P 或套餐未识别到作业，请选择其他项。"); }
    }
    public void SelectJob(string jobId)
    {
        PcrClanGuide.Select(document, document.BundlePath, jobId);
        var expanded = workSelection.IsExpanded;
        try { Display(document); }
        finally { workSelection.IsExpanded = expanded; }
        if (!string.IsNullOrEmpty(cacheNotice.Text)) Invalidate();
        SynchronizeSelection(); status.Text = HasCurrentConfirmation ? "已恢复本队保存的核对，账号和原件保持一致。" : "已载入作业。未知字段请对照原图明确填写，完成后一次确认保存。";
        videoStatus.Text = status.Text;
    }
    private void SynchronizeSelection()
    {
        if (bundle == null) return;
        var selected = ((JArray)bundle["jobs"]!).OfType<JObject>().SingleOrDefault(j => (string?)j["source"]?["job_id"] == document.SelectedJobId);
        if (selected == null) return;
        var selectedPage = selected["source"]?["page"]?.ToString(); var selectedPackage = (string?)selected["package"] ?? "";
        var before = displaying; displaying = true;
        try
        {
            page.ItemsSource = ((JArray)bundle["pages"]!).Select(p => new Choice(p["page"]!.ToString(), $"P{p["page"]} · {p["label"]}")).ToArray();
            page.SelectedItem = ((IEnumerable<Choice>)page.ItemsSource!).FirstOrDefault(c => c.Value == selectedPage);
            var jobs = ((JArray)bundle["jobs"]!).OfType<JObject>().Where(j => j["source"]?["page"]?.ToString() == selectedPage).ToArray();
            package.ItemsSource = jobs.Select(j => (string?)j["package"] ?? "").Distinct().Select(p => new Choice(p, p.Length == 0 ? "待核对套餐" : p)).ToArray();
            package.SelectedItem = ((IEnumerable<Choice>)package.ItemsSource!).FirstOrDefault(c => c.Value == selectedPackage);
            teams.ItemsSource = jobs.Where(j => ((string?)j["package"] ?? "") == selectedPackage).Select(j => new Choice((string)j["source"]!["job_id"]!, $"{j["team_label"] ?? "待核对队伍"} · BOSS {j["boss"]} · {j["source"]?["at_seconds"]} 秒")).ToArray();
            teams.SelectedItem = ((IEnumerable<Choice>)teams.ItemsSource!).FirstOrDefault(c => c.Value == document.SelectedJobId);
        }
        finally { displaying = before; }
    }
    public void RefreshAccounts()
    {
        var choices = new List<Choice>();
        foreach (var path in PcrClanGuide.SnapshotPaths(configDirectory))
        {
            try { var value = PcrClanGuide.ReadJson(path); choices.Add(new(path, $"账号 {value["account_id"]} · {value["observed_at"]} · {value["content_version"]}")); }
            catch { /* A malformed cache remains untouched and cannot be selected. */ }
        }
        var before = displaying; displaying = true;
        try { accounts.ItemsSource = choices; accounts.SelectedItem = choices.FirstOrDefault(c => c.Value == document.SnapshotPath); }
        finally { displaying = before; }
        DescribeAccount();
    }
    public void UseAccountSnapshot(PcrGuideSnapshot snapshot)
    {
        var path = PcrClanGuide.SnapshotPaths(configDirectory).SingleOrDefault(p =>
            PcrClanGuide.Stamp(p)?.Equals(snapshot.Sha256, StringComparison.OrdinalIgnoreCase) == true)
            ?? throw new InvalidDataException("所选账号缓存已改变，请重新读取。");
        var next = JsonConvert.DeserializeObject<PcrClanGuideDocument>(JsonConvert.SerializeObject(document))!;
        next.SnapshotPath = path; next.SnapshotSha256 = PcrClanGuide.Stamp(path)!;
        var data = PcrClanGuide.Snapshot(next, configDirectory, repository, DateTimeOffset.Now, out _);
        if ((string?)data["account_id"] != snapshot.AccountId || (string?)data["server"] != snapshot.Server
            || (string?)data["content_version"] != snapshot.ContentVersion)
            throw new InvalidDataException("所选资料与账号身份不一致。");
        if (document.SnapshotPath == path && document.SnapshotSha256 == next.SnapshotSha256) { RefreshAccounts(); return; }
        document.SnapshotPath = next.SnapshotPath; document.SnapshotSha256 = next.SnapshotSha256;
        Invalidate(); RefreshAccounts(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
        status.Text = "已使用账号资料，编辑内容保留；请重新核对作业和三刀。";
    }
    public void UseLatestAccount()
    {
        RefreshAccounts();
        if (accounts.SelectedItem is not Choice choice) { status.Text = "请先选择账号资料。"; return; }
        var stamp = PcrClanGuide.Stamp(choice.Value) ?? throw new IOException("所选账号资料不存在，请重新读取。");
        if (document.SnapshotSha256 == stamp) return;
        document.SnapshotSha256 = stamp; Invalidate(); DescribeAccount(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
        status.Text = "已选用最新账号资料，编辑内容保留；请重新核对五人和操作并保存。";
    }
    private void DescribeAccount()
    {
        using var data = PrepareDisplay(document, false);
        ApplyAccount(data);
    }
    private void UpdateCalibrationSummary()
    {
        var calibration = displayCalibration;
        calibrationDetails.Text = calibration.Summary + Environment.NewLine + calibration.AutoBattleSummary;
        var formal = execution.Mode == PcrClanDailyExecution.FormalDaily;
        var capability = formal ? "formal" : "simulation";
        var label = formal ? "正式三刀" : "模拟战";
        var version = (string?)displaySnapshot?["content_version"];
        var prefix = version == null ? "战斗校准" : $"战斗校准（内容版本 {version}）";
        var reason = calibration.Error ?? (!calibration.Supports(capability) ? "缺少对应页面校准。" : null);
        if (reason == null && !formal && calibration.OwnUnitsOnly)
        {
            try
            {
                if (displaySnapshot == null || PcrClanGuide.SupportRequirement(Capture().Job, displaySnapshot).HasValue)
                    reason = "当前校准仅支持全自有队伍。";
            }
            catch (Exception) { reason = "请先核对五人及其持有资料；当前校准仅支持全自有队伍。"; }
        }
        if (reason == null && formal && !calibration.FormalOpening) reason = "缺少正式开场校准。";
        if (reason == null && formal && execution.BattleSpeed != 2) reason = "旧正式配置需重新确认并保存为 2 倍速。";
        if (reason == null && !formal && execution.BattleSpeed != 1 && calibration.AutoBattleSpeeds?.Contains(execution.BattleSpeed) != true)
            reason = $"缺少 {execution.BattleSpeed} 倍速校准。";
        calibrationSummary.Text = !executionLoaded ? prefix + "：待读取执行模式。"
            : reason != null ? $"{prefix}：{label}不可用。{reason}"
            : $"{prefix}：已登记{label}校准。{(calibration.OwnUnitsOnly ? "支持全自有队伍。" : "")}"
                + (formal ? "预览正式三目标可分别核对每队。" : "开始前会核对所选队伍、操作和当前游戏页面。");
    }
    private void Persist(bool confirm)
    {
        if (!loaded || loading || dailyPlan.IsLoading) throw new InvalidOperationException("原作业未成功载入，禁止覆盖。请保留原文件并重开。");
        var value = Capture();
        loadedHash = PcrClanGuide.Save(value, statePath, loadedHash, configDirectory, repository, DateTimeOffset.Now, confirm);
        document = value;
        if (confirm)
        {
            try { PcrClanGuide.SavePublicGuide(document, configDirectory, repository); }
            catch (Exception ex) { status.Text = "本队已确认保存；公共攻略库未保存：" + ex.Message; return; }
        }
        status.Text = !confirm ? "草稿已保存，尚未确认执行。"
            : executionLoaded && execution.Mode == PcrClanDailyExecution.Simulation ? "已核对并保存。点击开始任务执行一场模拟；缺少对应校准时会停止。"
            : "本队已核对并保存。正式三刀还需单独核对并保存完整三目标。";
    }
    public void ConfirmAndSave() => Persist(true);
    public void SaveDraft() => Persist(false);
    private void GuardExecution(Action action)
    { try { action(); } catch (Exception ex) { status.Text = "会战模式未完成：" + ex.Message; } }
    public void ChangeExecutionMode(string mode)
    {
        if (loading) throw new InvalidOperationException("正在载入公会战资料，请等待读取完成再修改模式或倍速。");
        try
        {
            if (!executionLoaded) throw new InvalidOperationException("会战模式未成功读取，禁止覆盖。请重新打开。");
            var value = new PcrClanDailyExecutionDocument { Mode = mode, BattleSpeed = mode == PcrClanDailyExecution.FormalDaily ? 2 : execution.BattleSpeed };
            executionHash = PcrClanDailyExecution.Save(value, PcrClanDailyExecution.PathFor(configDirectory), executionHash, ReadSavedModeStamp); execution = value;
        }
        catch
        {
            executionLoaded = false; SetExecutionDisplay(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
            throw;
        }
        SetExecutionDisplay(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
    }
    public void ChangeBattleSpeed(int speed)
    {
        if (loading) throw new InvalidOperationException("正在载入公会战资料，请等待读取完成再修改模式或倍速。");
        try
        {
            if (!executionLoaded || execution.Mode != PcrClanDailyExecution.Simulation)
                throw new InvalidOperationException("请在单次模拟模式设置倍速。");
            var value = new PcrClanDailyExecutionDocument { Mode = execution.Mode, BattleSpeed = speed };
            executionHash = PcrClanDailyExecution.Save(value, PcrClanDailyExecution.PathFor(configDirectory), executionHash, ReadSavedModeStamp); execution = value;
        }
        catch
        {
            executionLoaded = false; SetExecutionDisplay(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
            throw;
        }
        SetExecutionDisplay(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
    }
    private void SetExecutionDisplay()
    {
        var before = displaying; displaying = true;
        try
        {
            executionMode.SelectedItem = executionLoaded ? ((IEnumerable<Choice>)executionMode.ItemsSource!).Single(c => c.Value == execution.Mode) : null;
            battleSpeed.SelectedItem = executionLoaded ? ((IEnumerable<Choice>)battleSpeed.ItemsSource!).Single(c => c.Value == execution.BattleSpeed.ToString(CultureInfo.InvariantCulture)) : null;
            executionMode.IsEnabled = !loading;
            battleSpeed.IsEnabled = !loading && executionLoaded && execution.Mode == PcrClanDailyExecution.Simulation;
            battleSpeed.IsVisible = speedLabel.IsVisible = !executionLoaded || execution.Mode == PcrClanDailyExecution.Simulation;
            formalSpeed.IsVisible = executionLoaded && execution.Mode == PcrClanDailyExecution.FormalDaily;
        }
        finally { displaying = before; }
        formalControls.IsVisible = executionLoaded && execution.Mode == PcrClanDailyExecution.FormalDaily;
        dailyDetails.IsExpanded = formalControls.IsVisible;
        executionSummary.Text = !executionLoaded ? "会战模式保存未完成，执行确认已取消；编辑内容保留，请重新打开后核对实际保存的模式。"
            : execution.Mode == PcrClanDailyExecution.Simulation ? $"{StartHint}，{execution.BattleSpeed} 倍速。已击败的 BOSS 仍可选择模拟战。"
            : $"{StartHint}。正式挑战固定 2 倍速，请先核对并保存三目标。"
                + (execution.BattleSpeed != 2 ? " 旧 1 倍配置尚未重新确认。" : "");
        UpdateCalibrationSummary();
    }
    public void PreviewDaily()
    {
        var request = PcrClanDailyExecution.BuildPlan(PcrClanDailyPlan.PathFor(configDirectory), statePath, configDirectory, repository, DateTimeOffset.Now);
        executionSummary.Text = StartHint + Environment.NewLine
            + string.Join(Environment.NewLine, ((JObject)request["slot_plans"]!).Properties().Select(p => $"第 {p.Name} 刀：{p.Value["period"]} · {p.Value["phase"]} · BOSS {p.Value["boss"]} · {p.Value["package"]} / {p.Value["team_label"]} · 作业 {p.Value["source"]?["job_id"]}"))
            + Environment.NewLine + "实际次数、日界与补偿未知；开始时从游戏核对。本次只预览，不执行。";
        try { PcrClanDailyExecution.RequireCapabilities(request, repository); executionSummary.Text += Environment.NewLine + "三个目标均有正式校准；实际出刀仍需通过现场核对。"; }
        catch (InvalidOperationException ex) { executionSummary.Text += Environment.NewLine + ex.Message; }
    }
    public void ConfirmDaily()
    {
        if (!executionLoaded || execution.Mode != PcrClanDailyExecution.FormalDaily) throw new InvalidOperationException("请先选择正式三刀模式；单次模拟确认不适用于三目标。");
        if (!HasCurrentConfirmation) throw new InvalidOperationException("当前编辑尚未核对保存，请先保存或放弃编辑后重开。");
        var request = PcrClanDailyExecution.BuildPlan(PcrClanDailyPlan.PathFor(configDirectory), statePath, configDirectory, repository, DateTimeOffset.Now);
        var value = new PcrClanDailyExecutionDocument { Mode = execution.Mode, BattleSpeed = 2, ConfirmedFor = (string?)request["confirmed_for"] };
        try
        {
            executionHash = PcrClanDailyExecution.Save(value, PcrClanDailyExecution.PathFor(configDirectory), executionHash, ReadSavedModeStamp); execution = value;
            _ = PcrClanDailyExecution.CreatePlan(configDirectory, repository, DateTimeOffset.Now); PreviewDaily();
        }
        catch
        {
            executionLoaded = false; SetExecutionDisplay(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
            throw;
        }
        status.Text = "正式三目标已单独核对保存。保存不会出刀；开始任务仍需通过校准与现场核对。";
    }
    public void LoadDailySlot(int slot) => dailyPlan.LoadSlot(slot);
    private void LoadReviewed(PcrClanGuideDocument value, string? expectedHash)
    {
        if (!loaded || loadedHash != expectedHash) throw new IOException("原作业已被其他窗口修改，请重开后载入；当前队伍未改变。");
        var nextBundle = PcrClanGuide.Bundle(value.BundlePath);
        // Decode before saving so a broken source image cannot replace the visible selection.
        using (var source = new Bitmap(new MemoryStream(PcrGuideData.ReadLocal((string)value.Job["source"]!["image_path"]!)))) { }
        var savedHash = PcrClanGuide.Save(value, statePath, expectedHash, configDirectory, repository, DateTimeOffset.Now, true);
        loaded = false;
        try
        {
            var (saved, readHash) = ReadSavedGuide(statePath);
            var plan = PcrClanGuide.CreatePlan(statePath, configDirectory, repository, DateTimeOffset.Now);
            if (readHash != savedHash || saved.SelectedJobId != value.SelectedJobId || saved.ConfirmedFor != (string?)plan["confirmed_for"])
                throw new IOException("当前模拟队写后核对失败，请重新打开。");
            loadedHash = readHash; bundle = nextBundle; cacheNotice.Text = ""; Display(saved); SynchronizeSelection(); loaded = true;
        }
        catch { document.ConfirmedFor = null; status.Text = "当前模拟队读回失败，请重新打开核对；旧显示已取消确认。"; throw; }
        status.Text = "已载入本刀并保存为当前编辑队伍。";
    }
    public void Reload()
    {
        loaded = false;
        cacheNotice.Text = "";
        try
        {
            var value = PcrClanGuide.Read(statePath, out loadedHash);
            Display(value);
            if (!string.IsNullOrEmpty(value.BundlePath))
            {
                bundle = PcrClanGuide.Bundle(value.BundlePath);
                SynchronizeSelection();
            }
            loaded = true; status.Text = HasCurrentConfirmation ? "已载入本队保存的核对。" : "已载入草稿；请选作业、核对五人和操作后一次保存。";
            dailyPlan.Reload();
            executionLoaded = false;
            execution = PcrClanDailyExecution.Read(PcrClanDailyExecution.PathFor(configDirectory), out executionHash);
            executionLoaded = true; SetExecutionDisplay(); ExecutionModeChanged?.Invoke(this, EventArgs.Empty);
        }
        catch (Exception ex) { status.Text = "载入失败，原文件未改：" + ex.Message; }
    }
    private async void PickBundle()
    {
        try
        {
            var top = TopLevel.GetTopLevel(this); if (top == null) return;
            var files = await top.StorageProvider.OpenFilePickerAsync(new Avalonia.Platform.Storage.FilePickerOpenOptions { Title = "选择已获取的 bundle.json", AllowMultiple = false });
            if (files.Count == 1) await LoadBundleAsync(files[0].Path.LocalPath);
        }
        catch (Exception ex) { SetVideoStatus("导入未完成：" + ex.Message); }
    }
    private static JArray ReadCacheRecords(string path)
    {
        using var stream = new MemoryStream(PcrGuideData.ReadLocal(Path.GetFullPath(path)));
        using var text = new StreamReader(stream, new System.Text.UTF8Encoding(false, true));
        using var reader = new JsonTextReader(text) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 };
        var records = JArray.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        if (reader.Read()) throw new InvalidDataException("缓存记录后有多余内容。");
        return records;
    }
    private (string Path, string Stamp, DateTimeOffset At, int Page)? FindCachedVideo(Uri uri, CancellationToken cancel)
    {
        var videoId = System.Text.RegularExpressions.Regex.Match(uri.AbsolutePath, @"^/video/(BV[0-9A-Za-z]{10})/?$");
        var pages = uri.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries).Select(x => x.Split('=', 2))
            .Where(x => Uri.UnescapeDataString(x[0]) == "p").ToArray();
        if (!videoId.Success || pages.Length != 1 || pages[0].Length != 2
            || !System.Text.RegularExpressions.Regex.IsMatch(pages[0][1], @"^[0-9]{1,3}$")
            || !int.TryParse(pages[0][1], NumberStyles.None, CultureInfo.InvariantCulture, out var part) || part is < 1 or > 500) return null;
        var bvid = videoId.Groups[1].Value;
        var runtime = Path.GetFullPath(Path.Combine(repository, ".runtime")) + Path.DirectorySeparatorChar;
        var root = Path.Combine(runtime, "clan-guide-sources");
        if (!Directory.Exists(root)) return null;
        var candidates = new List<(string Path, string Stamp, DateTimeOffset At, int Page)>();
        foreach (var directory in Directory.EnumerateDirectories(root))
        {
            cancel.ThrowIfCancellationRequested();
            try
            {
                if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0) continue;
                var path = Path.Combine(directory, "bundle.json"); var recognitionPath = Path.Combine(directory, "recognition.json");
                if (!File.Exists(path) || !File.Exists(recognitionPath)) continue;
                var stamp = PcrClanGuide.Stamp(path)!; var cached = PcrClanGuide.Bundle(path);
                if ((string?)cached["source"]?["bvid"] != bvid) continue;
                var matchingPages = ((JArray)cached["pages"]!).OfType<JObject>().Where(x => (int?)x["page"] == part).ToArray();
                if (matchingPages.Length != 1 || matchingPages[0]["cid"]?.Type != JTokenType.Integer || (long)matchingPages[0]["cid"]! <= 0) continue;
                var cid = (long)matchingPages[0]["cid"]!;
                var jobs = ((JArray)cached["jobs"]!).OfType<JObject>().Where(x => (int?)x["source"]?["page"] == part).ToArray();
                if (jobs.Length == 0) continue;
                var records = ReadCacheRecords(recognitionPath).OfType<JObject>().Where(x => (int?)x["page"] == part).ToArray();
                if (records.Length != 1 || records[0]["acquisition"] is not JObject acquisition
                    || (string?)acquisition["bvid"] != bvid || (int?)acquisition["page"] != part || (long?)acquisition["cid"] != cid
                    || acquisition["retrieved_at"]?.Type != JTokenType.String || records[0]["observations"] is not JArray observations) continue;
                var at = PcrGuideData.Observation((string)acquisition["retrieved_at"]!);
                if (at > DateTimeOffset.Now) continue;
                var recognitionStamp = PcrClanGuide.Stamp(recognitionPath);
                var valid = true;
                foreach (var job in jobs)
                {
                    cancel.ThrowIfCancellationRequested();
                    var source = job["source"] as JObject;
                    var image = (string?)source?["image_path"]; var id = (string?)source?["job_id"];
                    if (source == null || (string?)source["bvid"] != bvid || (long?)source["cid"] != cid || image == null || id == null
                        || source["at_seconds"]?.Type != JTokenType.Integer || !Path.IsPathFullyQualified(image)
                        || !Path.GetFullPath(image).StartsWith(runtime, StringComparison.OrdinalIgnoreCase)
                        || PcrClanGuide.Stamp(image) != (string?)source["image_sha256"]) { valid = false; break; }
                    var imageDir = Path.GetDirectoryName(image); var sourceDir = imageDir == null ? null : Path.GetDirectoryName(imageDir);
                    var sourceRoot = sourceDir == null ? null : Path.GetDirectoryName(sourceDir);
                    if (sourceDir == null || sourceRoot == null || !JToken.DeepEquals(PcrClanGuide.ReadJson(Path.Combine(sourceDir, "acquisition.json")), acquisition)
                        || PcrClanGuide.Stamp(Path.Combine(sourceRoot, "recognition.json")) != recognitionStamp) { valid = false; break; }
                    var matching = observations.OfType<JObject>().Where(x => JToken.DeepEquals(x["at_seconds"], source["at_seconds"])
                        && (string?)x["image_path"] == image).ToArray();
                    if (matching.Length != 1 || matching[0]["tables"] is not JArray tables
                        || tables.OfType<JObject>().Count(x => (string?)x["job_id"] == id) != 1) { valid = false; break; }
                }
                if (valid && PcrClanGuide.Stamp(path) == stamp) candidates.Add((path, stamp, at, part));
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception) { /* An incomplete or changed local cache is not a usable fallback. */ }
        }
        return candidates.OrderByDescending(x => x.At).ThenBy(x => x.Path, StringComparer.Ordinal).FirstOrDefault() is var found && found.Path != null ? found : null;
    }
    private async Task FetchVideo()
    {
        if (fetchCancel != null) { SetVideoStatus("正在获取，可先停止当前获取。"); return; }
        var url = video.Text?.Trim() ?? "";
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || (uri.Host != "www.bilibili.com" && uri.Host != "bilibili.com") || uri.UserInfo != "") { SetVideoStatus("请粘贴 B 站 HTTPS 视频链接。"); return; }
        using var cancel = new CancellationTokenSource(TimeSpan.FromMinutes(5)); fetchCancel = cancel; stopFetch.IsVisible = true;
        SetVideoStatus("正在获取视频原图与分 P 作业，较长的视频可能需要几分钟；取得后仍需核对五人和操作。");
        var output = Path.Combine(repository, ".runtime", "clan-guide-sources", Guid.NewGuid().ToString("N"));
        using var child = new Process(); var started = false;
        try
        {
            child.StartInfo = new ProcessStartInfo { FileName = PcrRuntimePaths.ResolvePython(repository), WorkingDirectory = repository, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = System.Text.Encoding.UTF8, StandardErrorEncoding = System.Text.Encoding.UTF8 };
            foreach (var arg in new[] { "-X", "utf8", "-B", Path.Combine(repository, "scripts", "prepare-clan-guide.py"), "--video-url", url, "--output", output }) child.StartInfo.ArgumentList.Add(arg);
            if (!child.Start()) throw new InvalidOperationException("视频获取程序未启动。");
            started = true;
            var stdout = child.StandardOutput.ReadToEndAsync(cancel.Token); var stderr = child.StandardError.ReadToEndAsync(cancel.Token);
            await child.WaitForExitAsync(cancel.Token); var text = await stdout; var error = await stderr;
            if (child.ExitCode != 0)
            {
                var detail = error.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).LastOrDefault() ?? "请检查网络后重试。";
                if (detail.Contains("HTTP Error 412", StringComparison.OrdinalIgnoreCase))
                {
                    SetVideoStatus("线上获取受阻，正在查找同一分 P 的本地缓存。");
                    var cached = await Task.Run(() => FindCachedVideo(uri, cancel.Token), cancel.Token);
                    if (cached is { } found && PcrClanGuide.Stamp(found.Path) == found.Stamp)
                    {
                        await LoadBundleAsync(found.Path, found.Page);
                        Invalidate();
                        cacheNotice.Text = $"本地缓存（采集于 {found.At:yyyy-MM-dd HH:mm:ss zzz}）；线上是否更新尚未核验，请对照原图核对。";
                        SetVideoStatus("线上获取受阻，已载入本地缓存供核对。");
                        return;
                    }
                    detail = "B 站暂时拒绝自动获取（HTTP 412）；可稍后重试或导入已获取的作业包。";
                }
                else if (detail.StartsWith("ValueError: ", StringComparison.Ordinal)) detail = detail[12..];
                if (detail.Length > 300) detail = detail[..300] + "…";
                throw new InvalidOperationException(detail);
            }
            var last = text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).LastOrDefault() ?? "";
            var result = JObject.Parse(last); var path = (string?)result["bundle_path"] ?? throw new InvalidDataException("视频获取未返回作业包。");
            if (Path.GetFullPath(path) != Path.Combine(output, "bundle.json")) throw new InvalidDataException("视频获取返回了其他目录。");
            await LoadBundleAsync(path);
        }
        catch (OperationCanceledException) { if (started && !child.HasExited) child.Kill(true); SetVideoStatus("获取已停止，已保存的作业未改变。"); }
        catch (Exception ex) { SetVideoStatus("获取未完成：" + ex.Message); }
        finally { fetchCancel = null; stopFetch.IsVisible = false; }
    }
}
