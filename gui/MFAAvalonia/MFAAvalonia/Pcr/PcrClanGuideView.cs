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

public sealed class PcrClanGuideView : UserControl
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
    private readonly ComboBox executionMode = new() { ItemsSource = new[] { new Choice(PcrClanDailyExecution.Simulation, "单次模拟"), new Choice(PcrClanDailyExecution.FormalDaily, "正式三刀（待开放校准）") }, MinWidth = 260 };
    private readonly TextBlock executionSummary = Note("");
    private readonly StackPanel formalControls = new() { Spacing = 6, IsVisible = false };
    private readonly TextBlock selectionSummary = Note("尚未选定作业。");
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
    private readonly ComboBox teams = new() { PlaceholderText = "选择队伍", MinWidth = 320 };
    private readonly ComboBox accounts = new() { PlaceholderText = "选择原账号缓存", MinWidth = 320 };
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
    private readonly TextBlock accountNote = Note("");
    public string StatusText => status.Text ?? "";
    public string ExecutionMode => executionLoaded ? execution.Mode : "unavailable";
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
        get { if (!loaded) return false; try { var value = Capture(); return value.ConfirmedFor != null && value.ConfirmedFor == (string?)PcrClanGuide.BuildPlan(value, configDirectory, repository, DateTimeOffset.Now)["confirmed_for"]; } catch { return false; } }
    }

    public PcrClanGuideView() : this(null) { }
    public PcrClanGuideView(string? repository) : this(repository ?? PcrAlphaOwner.Repository, PcrClanGuide.StatePath, AppPaths.ConfigDirectory) { }
    public PcrClanGuideView(string repository, string statePath, string configDirectory)
    {
        this.repository = Path.GetFullPath(repository); this.statePath = Path.GetFullPath(statePath); this.configDirectory = Path.GetFullPath(configDirectory);
        var guide = new StackPanel { Spacing = 10 };
        guide.Children.Add(new TextBlock { Text = "公会战", FontSize = 22, FontWeight = FontWeight.SemiBold });
        Avalonia.Automation.AutomationProperties.SetName(executionMode, "会战执行模式");
        guide.Children.Add(Buttons(Note("执行模式"), executionMode));
        guide.Children.Add(executionSummary);
        guide.Children.Add(selectionSummary);
        guide.Children.Add(Buttons(accounts, Button("选用最新账号资料", UseLatestAccount), Button("账号资料与刷新", () => AccountRefreshRequested?.Invoke(this, EventArgs.Empty))));
        guide.Children.Add(accountNote);
        formalControls.Children.Add(Note("正式三刀尚未开放校准，目前只可保存和预览，不会正式出刀或消费补偿。"));
        formalControls.Children.Add(Buttons(Button("预览正式三目标（不执行）", PreviewDaily), Button("核对并保存正式三目标（不启用）", ConfirmDaily)));
        guide.Children.Add(formalControls);
        guide.Children.Add(new TextBlock { Text = "选作业", FontSize = 16, FontWeight = FontWeight.SemiBold, Margin = new Thickness(0, 8, 0, 0) });
        guide.Children.Add(video);
        guide.Children.Add(Buttons(Button("获取视频作业", async () => await FetchVideo()), Button("导入已获取的作业包", PickBundle), Button("停止获取", () => fetchCancel?.Cancel())));
        guide.Children.Add(Buttons(page, package, teams));
        var source = new StackPanel { Spacing = 8 }; source.Children.Add(imageNote); source.Children.Add(image); source.Children.Add(preview);
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
        guide.Children.Add(new Expander { Header = "目标 BOSS 与周目", IsExpanded = true, Content = target });
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
        guide.Children.Add(Note("缺谁借谁：五人齐全用本人，缺一人借该角色；缺多名或支援不匹配时停止。指定支援只借本队所选角色，不替换角色。"));
        var timing = new StackPanel { Spacing = 6 }; timing.Children.Add(Note("每行：剩余秒数 | AUTO（不变 / 开 / 关）| SET（完整角色名或编号=开/关，用分号分隔）。秒数严格递减。未解析的原轴须填写支持的秒数，或明确写“无切换”；空白不代替未知。错过秒数、读回未知或断线即停，不补点。")); timing.Children.Add(switches);
        guide.Children.Add(timingStatus);
        guide.Children.Add(new Expander { Header = "战中 SET / AUTO 秒数轴", Content = timing });
        guide.Children.Add(Buttons(Button("核对五人和操作并保存", ConfirmAndSave), Button("暂存草稿", SaveDraft), Button("放弃编辑并重开", Reload)));
        guide.Children.Add(status);
        guide.Children.Add(new Expander { Header = "作业名称与参考伤害", Content = reference });
        dailyPlan = new PcrClanDailyPlanView(this.repository, PcrClanDailyPlan.PathFor(this.configDirectory), this.configDirectory,
            this.statePath, Capture, () => HasCurrentConfirmation, LoadReviewed);
        guide.Children.Add(new Expander { Header = "一天三刀目标（仅离线预设）", Content = dailyPlan });
        Content = new ScrollViewer { Content = guide, Margin = new Thickness(16), HorizontalScrollBarVisibility = Avalonia.Controls.Primitives.ScrollBarVisibility.Disabled };
        page.SelectionChanged += (_, _) => { if (!displaying) Guard(PopulatePackages); };
        package.SelectionChanged += (_, _) => { if (!displaying) Guard(PopulateTeams); };
        teams.SelectionChanged += (_, _) => { if (!displaying && teams.SelectedItem is Choice choice) Guard(() => SelectJob(choice.Value)); };
        accounts.SelectionChanged += (_, _) =>
        {
            if (displaying) return;
            Guard(() =>
            {
                if (accounts.SelectedItem is Choice choice) { document.SnapshotPath = choice.Value; document.SnapshotSha256 = PcrClanGuide.Stamp(choice.Value) ?? ""; LoadCatalog(); }
                Invalidate(); DescribeAccount();
            });
        };
        auto.SelectionChanged += (_, _) => Invalidate(); support.SelectionChanged += (_, _) => { supportRole.IsVisible = (support.SelectedItem as Choice)?.Value == "specified"; Invalidate(); };
        Watch(supportRole); Watch(switches); Watch(video);
        executionMode.SelectionChanged += (_, _) => { if (!displaying && executionMode.SelectedItem is Choice selected) GuardExecution(() => ChangeExecutionMode(selected.Value)); };
        foreach (var (control, name) in new (Control, string)[] { (video, "公会战视频链接"), (page, "视频分 P"), (package, "视频套餐"), (teams, "视频队伍"), (accounts, "共用账号缓存"), (auto, "开场 AUTO"), (support, "支援方式"), (supportRole, "指定支援角色"), (switches, "剩余秒数 SET AUTO 切换轴") })
            Avalonia.Automation.AutomationProperties.SetName(control, name);
        DetachedFromVisualTree += (_, _) => { fetchCancel?.Cancel(); ReleaseImages(); };
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
        Reload();
    }
    private void RestoreImages()
    {
        if (bitmap != null || previewBitmap != null || document.Job["source"]?["image_path"]?.Type != JTokenType.String) return;
        try { LoadImages(); }
        catch (Exception ex) { document.ConfirmedFor = null; status.Text = "图片恢复失败，旧确认已取消；编辑内容保留：" + ex.Message; }
    }
    private void ReleaseImages()
    {
        image.Source = null; preview.Source = null;
        bitmap?.Dispose(); bitmap = null; previewBitmap?.Dispose(); previewBitmap = null;
    }
    private void LoadImages()
    {
        ReleaseImages();
        var job = document.Job;
        try
        {
            JObject? originalJob = null;
            if (!string.IsNullOrEmpty(document.BundlePath))
            {
                if (PcrClanGuide.Stamp(document.BundlePath) != document.BundleSha256) throw new InvalidDataException("作业原件摘要不一致。");
                originalJob = ((JArray)PcrClanGuide.Bundle(document.BundlePath)["jobs"]!).OfType<JObject>().SingleOrDefault(j => (string?)j["source"]?["job_id"] == document.SelectedJobId);
                if (originalJob == null || !JToken.DeepEquals(job["source"], originalJob["source"]) || PcrClanGuide.Stamp(document.BundlePath) != document.BundleSha256)
                    throw new InvalidDataException("图片来源与当前作业原件不符。");
            }
            if (job["source"]?["image_path"]?.Type == JTokenType.String)
            {
                var bytes = PcrGuideData.ReadLocal((string)job["source"]!["image_path"]!);
                if (PcrClanGuide.Hash(bytes) != (string?)job["source"]?["image_sha256"]) throw new InvalidDataException("原图摘要不一致。");
                bitmap = new Bitmap(new MemoryStream(bytes));
            }
            var cropToken = originalJob?["image_base64"] ?? job["image_base64"];
            if (cropToken?.Type == JTokenType.String && ((string)cropToken!).Length is > 0 and < 8_000_000)
                previewBitmap = new Bitmap(new MemoryStream(Convert.FromBase64String((string)cropToken!)));
            image.Source = bitmap; preview.Source = previewBitmap;
        }
        catch { ReleaseImages(); throw; }
    }
    private static ComboBox Toggle(bool unchanged) => new() { MinWidth = 100, ItemsSource = new[] { unchanged ? "不变" : "待确认", "开启", "关闭" }, SelectedIndex = 0 };
    private static TextBlock Note(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap };
    private static WrapPanel Buttons(params Control[] controls)
    { var panel = new WrapPanel(); foreach (var item in controls) { item.Margin = new Thickness(0, 0, 8, 5); panel.Children.Add(item); } return panel; }
    private Button Button(string label, Action action)
    { var button = new Button { Content = label }; button.Click += (_, _) => { try { action(); } catch (Exception ex) { status.Text = "未完成：" + ex.Message; } }; return button; }
    private void Guard(Action action)
    { try { action(); } catch (Exception ex) { document.ConfirmedFor = null; status.Text = "作业不可用，尚未确认：" + ex.Message; } }
    private void Watch(TextBox box) => box.PropertyChanged += (_, e) => { if (e.Property == TextBox.TextProperty) Invalidate(); };
    private void Invalidate()
    { if (displaying) return; document.ConfirmedFor = null; UpdateSelectionSummary(); status.Text = "内容已修改，请对照原图后一次确认保存。"; }
    private void UpdateSelectionSummary()
    {
        string Value(string key) => fields[key].Text?.Trim() is { Length: > 0 } text ? text : "待填写";
        selectionSummary.Text = $"当前队伍：{Value("team_label")} · BOSS {Value("boss")} · {Value("phase")} 阶段 · {Value("period")}";
        var roster = members.Select(m => m.Role.Text?.Trim()).Where(name => !string.IsNullOrEmpty(name)).ToArray();
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
    private void LoadCatalog()
    {
        try
        {
            var version = File.Exists(document.SnapshotPath) ? (string?)PcrClanGuide.ReadJson(document.SnapshotPath)["content_version"] : null;
            var old = Path.Combine(configDirectory, "guide-review.json");
            catalog = File.Exists(old) ? new PcrGuideStore(old).Load().Input.Catalog : null;
            if (catalog != null && (version == null || catalog.ContentVersion == version)) return;
            // The shared roster supplies display names only; execution still requires the versioned catalog above.
            var path = Path.Combine(repository, "assets", "labyrinth", "character-pack", "characters.landosolroster.json");
            var bytes = PcrGuideData.ReadLocal(path); var roster = PcrClanGuide.ReadJson(path);
            if ((int?)roster["schemaVersion"] != 1 || roster["characters"] is not JArray rows) throw new InvalidDataException("原角色目录格式无效。");
            var units = new List<PcrGuideUnit>();
            foreach (var row in rows)
            {
                if (!int.TryParse((string?)row["id"], NumberStyles.None, CultureInfo.InvariantCulture, out var character) || character is <= 1000 or > 9999) continue;
                var name = (string?)row["officialName"]; if (string.IsNullOrWhiteSpace(name)) name = (string?)row["name"];
                if (!string.IsNullOrWhiteSpace(name)) units.Add(new(character * 100 + 1, name));
            }
            if (units.Count == 0 || units.Select(u => u.UnitId).Distinct().Count() != units.Count) throw new InvalidDataException("原角色目录为空或编号重复。");
            catalog = new PcrGuideCatalog { Server = "cn-bilibili", Source = path, Sha256 = PcrClanGuide.Hash(bytes), Units = units };
        }
        catch (Exception ex) { catalog = null; accountNote.Text = "角色目录暂不可用：" + ex.Message; }
    }
    public void Display(PcrClanGuideDocument value)
    {
        displaying = true;
        try
        {
            document = JsonConvert.DeserializeObject<PcrClanGuideDocument>(JsonConvert.SerializeObject(value))!; LoadCatalog();
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
            if (sourceDetails.IsExpanded) LoadImages();
            imageNote.Text = $"原图：P{job["source"]?["page"]} · {job["source"]?["at_seconds"]} 秒。请对照原图核对五人、星级和操作。练度、EX 和强化差异只作提示。";
            if (job["members"] is JArray candidateMembers && candidateMembers.Any(m => m["unit_id"]?.Type != JTokenType.Integer && m["proposal_unit_id"]?.Type == JTokenType.Integer))
                imageNote.Text += "\n角色栏预填的是识别候选，请结合完整原图与局部放大核对形态；候选尚未确认。";
            if (job["warnings"] is JArray warnings && warnings.Count > 0) imageNote.Text += "\n" + string.Join("；", warnings.Values<string>());
            RefreshAccounts();
        }
        finally { displaying = false; }
        DescribeAccount(); UpdateSelectionSummary();
    }
    public void LoadBundle(string path)
    {
        var next = PcrClanGuide.Bundle(path);
        bundle = next;
        document.BundlePath = Path.GetFullPath(path); document.BundleSha256 = PcrClanGuide.Stamp(path)!; document.ConfirmedFor = null;
        displaying = true;
        try { page.ItemsSource = ((JArray)bundle["pages"]!).Select(p => new Choice(p["page"]!.ToString(), $"P{p["page"]} · {p["label"]}")).ToArray(); page.SelectedIndex = 0; }
        finally { displaying = false; }
        PopulatePackages();
    }
    private void PopulatePackages()
    {
        if (bundle == null) return;
        var number = (page.SelectedItem as Choice)?.Value;
        var list = ((JArray)bundle["jobs"]!).OfType<JObject>().Where(j => j["source"]?["page"]?.ToString() == number).Select(j => (string?)j["package"] ?? "").Distinct().ToArray();
        displaying = true;
        try { package.ItemsSource = list.Select(p => new Choice(p, p.Length == 0 ? "待核对套餐" : p)).ToArray(); package.SelectedIndex = 0; }
        finally { displaying = false; }
        PopulateTeams();
    }
    private void PopulateTeams()
    {
        if (bundle == null) return;
        var number = (page.SelectedItem as Choice)?.Value; var name = (package.SelectedItem as Choice)?.Value;
        var list = ((JArray)bundle["jobs"]!).OfType<JObject>().Where(j => j["source"]?["page"]?.ToString() == number && ((string?)j["package"] ?? "") == name).ToArray();
        displaying = true;
        try { teams.ItemsSource = list.Select(j => new Choice((string)j["source"]!["job_id"]!, $"{j["team_label"] ?? "待核对队伍"} · BOSS {j["boss"]} · {j["source"]?["at_seconds"]} 秒")).ToArray(); teams.SelectedIndex = 0; }
        finally { displaying = false; }
        if (teams.SelectedItem is Choice selected) SelectJob(selected.Value);
    }
    public void SelectJob(string jobId)
    {
        PcrClanGuide.Select(document, document.BundlePath, jobId);
        Display(document); SynchronizeSelection(); status.Text = HasCurrentConfirmation ? "已恢复本队保存的核对，账号和原件保持一致。" : "已载入作业。未知字段请对照原图明确填写，完成后一次确认保存。";
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
    public void UseLatestAccount()
    {
        RefreshAccounts();
        if (accounts.SelectedItem is not Choice choice) { status.Text = "请先选择账号资料。"; return; }
        var stamp = PcrClanGuide.Stamp(choice.Value) ?? throw new IOException("所选账号资料不存在，请重新读取。");
        if (document.SnapshotSha256 == stamp) return;
        document.SnapshotSha256 = stamp; LoadCatalog(); Invalidate(); DescribeAccount();
        status.Text = "已选用最新账号资料，编辑内容保留；请重新核对五人和操作并保存。";
    }
    private void DescribeAccount()
    {
        if (string.IsNullOrEmpty(document.SnapshotPath)) { accountNote.Text = "尚未选择账号资料。可在“账号资料与刷新”读取，再选择所需账号。"; return; }
        try
        {
            var value = PcrClanGuide.Snapshot(document, configDirectory, repository, DateTimeOffset.Now, out _);
            accountNote.Text = $"账号 {value["account_id"]} · 采集于 {value["observed_at"]} · {((bool?)value["ownership_complete"] == true ? "完整持有名单" : "部分持有资料")}。资料有效期 24 小时。";
        }
        catch (Exception ex) { accountNote.Text = "账号资料不可用：" + ex.Message + "；请在“账号资料与刷新”核对后，点击“选用最新账号资料”。"; }
    }
    private void Persist(bool confirm)
    {
        if (!loaded) throw new InvalidOperationException("原作业未成功载入，禁止覆盖。请保留原文件并重开。");
        var value = Capture();
        loadedHash = PcrClanGuide.Save(value, statePath, loadedHash, configDirectory, repository, DateTimeOffset.Now, confirm);
        document = value;
        if (confirm)
        {
            try { PcrClanGuide.SavePublicGuide(document, configDirectory, repository); }
            catch (Exception ex) { status.Text = "本队已确认保存；公共攻略库未保存：" + ex.Message; return; }
        }
        status.Text = !confirm ? "草稿已保存，尚未确认执行。"
            : executionLoaded && execution.Mode == PcrClanDailyExecution.Simulation ? "已核对并保存。模拟战待校准开放，当前不会开始战斗。"
            : "本队已核对并保存。模拟、正式三刀及补偿执行均待校准开放。";
    }
    public void ConfirmAndSave() => Persist(true);
    public void SaveDraft() => Persist(false);
    private void GuardExecution(Action action)
    { try { action(); } catch (Exception ex) { status.Text = "会战模式未完成：" + ex.Message; } }
    public void ChangeExecutionMode(string mode)
    {
        try
        {
            if (!executionLoaded) throw new InvalidOperationException("会战模式未成功读取，禁止覆盖。请重新打开。");
            var value = new PcrClanDailyExecutionDocument { Mode = mode };
            executionHash = PcrClanDailyExecution.Save(value, PcrClanDailyExecution.PathFor(configDirectory), executionHash); execution = value;
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
        try { executionMode.SelectedItem = executionLoaded ? ((IEnumerable<Choice>)executionMode.ItemsSource!).Single(c => c.Value == execution.Mode) : null; }
        finally { displaying = before; }
        formalControls.IsVisible = executionLoaded && execution.Mode == PcrClanDailyExecution.FormalDaily;
        executionSummary.Text = !executionLoaded ? "会战模式保存未完成，执行确认已取消；编辑内容保留，请重新打开后核对实际保存的模式。"
            : execution.Mode == PcrClanDailyExecution.Simulation ? "当前为单次模拟。选作业 → 核对阵容 → 保存；模拟战待校准开放。"
            : "正式三刀仅离线配置，尚未校准，禁止执行。请预览完整三目标后单独核对保存。";
    }
    public void PreviewDaily()
    {
        var request = PcrClanDailyExecution.BuildPlan(PcrClanDailyPlan.PathFor(configDirectory), statePath, configDirectory, repository, DateTimeOffset.Now);
        executionSummary.Text = string.Join(Environment.NewLine, ((JObject)request["slot_plans"]!).Properties().Select(p => $"第 {p.Name} 刀：{p.Value["period"]} · {p.Value["phase"]} · BOSS {p.Value["boss"]} · {p.Value["package"]} / {p.Value["team_label"]} · 作业 {p.Value["source"]?["job_id"]}"))
            + Environment.NewLine + "实际次数、日界与补偿未知；尚未开放校准，不执行。";
    }
    public void ConfirmDaily()
    {
        if (!executionLoaded || execution.Mode != PcrClanDailyExecution.FormalDaily) throw new InvalidOperationException("请先选择正式三刀模式；单次模拟确认不适用于三目标。");
        if (!HasCurrentConfirmation) throw new InvalidOperationException("当前编辑尚未核对保存，请先保存或放弃编辑后重开。");
        var request = PcrClanDailyExecution.BuildPlan(PcrClanDailyPlan.PathFor(configDirectory), statePath, configDirectory, repository, DateTimeOffset.Now);
        var value = new PcrClanDailyExecutionDocument { Mode = execution.Mode, ConfirmedFor = (string?)request["confirmed_for"] };
        executionHash = PcrClanDailyExecution.Save(value, PcrClanDailyExecution.PathFor(configDirectory), executionHash); execution = value;
        _ = PcrClanDailyExecution.CreatePlan(configDirectory, repository, DateTimeOffset.Now); PreviewDaily();
        status.Text = "正式三目标已单独核对保存；尚未开放校准，开始任务不会正式出刀。";
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
            var saved = PcrClanGuide.Read(statePath, out var readHash);
            var plan = PcrClanGuide.CreatePlan(statePath, configDirectory, repository, DateTimeOffset.Now);
            if (readHash != savedHash || saved.SelectedJobId != value.SelectedJobId || saved.ConfirmedFor != (string?)plan["confirmed_for"])
                throw new IOException("当前模拟队写后核对失败，请重新打开。");
            loadedHash = readHash; bundle = nextBundle; Display(saved); SynchronizeSelection(); loaded = true;
        }
        catch { document.ConfirmedFor = null; status.Text = "当前模拟队读回失败，请重新打开核对；旧显示已取消确认。"; throw; }
        status.Text = "已载入并保存本刀为当前模拟队；可见队伍、原文件和开始任务读取的队伍一致。一次开始仍只做一场模拟战。";
    }
    public void Reload()
    {
        loaded = false;
        try
        {
            var value = PcrClanGuide.Read(statePath, out loadedHash);
            Display(value);
            if (!string.IsNullOrEmpty(value.BundlePath))
            {
                bundle = PcrClanGuide.Bundle(value.BundlePath);
                SynchronizeSelection();
            }
            loaded = true; status.Text = HasCurrentConfirmation ? "已载入本队确认。开始前仍会核对实际账号、BOSS 和模拟战入口。" : "已载入草稿；请选作业、核对五人和操作后一次保存。";
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
            if (files.Count == 1) LoadBundle(files[0].Path.LocalPath);
        }
        catch (Exception ex) { status.Text = "导入未完成：" + ex.Message; }
    }
    private async Task FetchVideo()
    {
        if (fetchCancel != null) { status.Text = "正在获取，可先停止当前获取。"; return; }
        var url = video.Text?.Trim() ?? "";
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.Host != "www.bilibili.com" || uri.UserInfo != "") { status.Text = "请粘贴 B 站 HTTPS 视频链接。"; return; }
        using var cancel = new CancellationTokenSource(TimeSpan.FromMinutes(5)); fetchCancel = cancel;
        status.Text = "正在获取视频原图与分 P 作业，取得后仍需核对五人和操作。";
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
            if (child.ExitCode != 0) throw new InvalidOperationException("视频获取失败：" + error.Trim().Substring(0, Math.Min(error.Trim().Length, 1500)));
            var last = text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).LastOrDefault() ?? "";
            var result = JObject.Parse(last); var path = (string?)result["bundle_path"] ?? throw new InvalidDataException("视频获取未返回作业包。");
            if (Path.GetFullPath(path) != Path.Combine(output, "bundle.json")) throw new InvalidDataException("视频获取返回了其他目录。");
            LoadBundle(path);
        }
        catch (OperationCanceledException) { if (started && !child.HasExited) child.Kill(true); status.Text = "获取已停止，已保存的作业未改变。"; }
        catch (Exception ex) { status.Text = "获取未完成：" + ex.Message; }
        finally { fetchCancel = null; }
    }
}
