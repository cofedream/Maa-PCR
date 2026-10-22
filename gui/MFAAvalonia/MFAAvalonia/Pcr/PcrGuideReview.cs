using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MFAAvalonia.Helper;
using CommunityToolkit.Mvvm.ComponentModel;
using Newtonsoft.Json;

namespace MFAAvalonia.Pcr;

public sealed class PcrGuideCondition
{
    public int? UnitId { get; set; }
    public string Character { get; set; } = "";
    public string Form { get; set; } = "";
    public string Field { get; set; } = "持有";
    public string Comparison { get; set; } = "=";
    public string Expected { get; set; } = "是";
    public string Evidence { get; set; } = "";
}

public sealed class PcrGuideFact : ObservableObject
{
    public int? UnitId { get; set; }
    public string Character { get; set; } = "";
    public string Form { get; set; } = "";
    public string Field { get; set; } = "持有";
    public string Value { get; set; } = "";
    public string Source { get; set; } = "";
    private bool confirmed;
    public bool Confirmed { get => confirmed; set => SetProperty(ref confirmed, value); }
}

public sealed class PcrGuideInput
{
    [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
    public Dictionary<int, bool?>? OpeningSets { get; set; }
    [JsonProperty(DefaultValueHandling = DefaultValueHandling.Ignore)]
    public bool OpeningConfirmed { get; set; }
    public PcrGuideIdentity? Identity { get; set; }
    [JsonProperty(DefaultValueHandling = DefaultValueHandling.Ignore)]
    public bool RequiresImageReview { get; set; }
    public PcrGuideCatalog? Catalog { get; set; }
    public PcrGuideSnapshot? Snapshot { get; set; }
    public bool SnapshotConfirmed { get; set; }
    public string Title { get; set; } = "";
    public string SourceUrl { get; set; } = "";
    public string Material { get; set; } = "";
    public string RetrievedOn { get; set; } = "";
    public string ValidThrough { get; set; } = "";
    public bool SourceConfirmed { get; set; }
    public string GuideServer { get; set; } = "";
    public string GuideStage { get; set; } = "";
    public string GuideVersion { get; set; } = "";
    public string Mode { get; set; } = "";
    public bool CoverageConfirmed { get; set; }
    public string TargetServer { get; set; } = "";
    public string TargetStage { get; set; } = "";
    public string TargetVersion { get; set; } = "";
    public string AccountLabel { get; set; } = "";
    public string AccountServer { get; set; } = "";
    public string AccountVersion { get; set; } = "";
    public string AccountObservedOn { get; set; } = "";
    public List<PcrGuideCondition> Conditions { get; set; } = new();
    public List<PcrGuideFact> Facts { get; set; } = new();
}

public sealed record PcrGuideFinding(string Status, string Item, string Detail, string Source);
public sealed record PcrGuideReport(string InputHash, DateTimeOffset CheckedAt, string Summary,
    IReadOnlyList<PcrGuideFinding> Findings)
{
    public string Text => Summary + "\n核对时间：" + CheckedAt.ToLocalTime().ToString("yyyy-MM-dd HH:mm") +
        "\n仅核对已整理的条件，不保证通关，不操作游戏。\n\n" + string.Join("\n\n", Findings.Select(f =>
            $"【{f.Status}】{f.Item}\n{f.Detail}\n依据：{f.Source}"));
}

public sealed class PcrGuideDocument
{
    public int SchemaVersion { get; set; } = 1;
    public PcrGuideInput Input { get; set; } = new();
    public PcrGuideReport? Report { get; set; }
}

// Read-only facts never create a plan, grant, controller or game action.
public static class PcrGuideReview
{
    private static readonly JsonSerializerSettings JsonSettings = new()
    {
        MissingMemberHandling = MissingMemberHandling.Error,
        MaxDepth = 16,
        TypeNameHandling = TypeNameHandling.None
    };
    public static string Serialize(object value) => JsonConvert.SerializeObject(value, Formatting.Indented);
    public static string Fingerprint(PcrGuideInput input) => Convert.ToHexString(
        SHA256.HashData(Encoding.UTF8.GetBytes(Serialize(input))));
    public static PcrGuideDocument Parse(string json)
    {
        if (json.Length > 1_000_000) throw new InvalidDataException("资料过大，请只保留本队相关内容。");
        var doc = JsonConvert.DeserializeObject<PcrGuideDocument>(json, JsonSettings)
            ?? throw new InvalidDataException("资料为空。");
        if (doc.SchemaVersion != 1 || doc.Input == null) throw new InvalidDataException("不支持的攻略资料格式。");
        Validate(doc.Input);
        return doc;
    }
    public static void Validate(PcrGuideInput input)
    {
        if (input.Conditions == null || input.Facts == null || input.Conditions.Count > 100 || input.Facts.Count > 300
            || input.Conditions.Any(x => x == null) || input.Facts.Any(x => x == null))
            throw new InvalidDataException("条件或资料列表无效，首版仅用于一队。");
        if (Serialize(input).Length > 200_000) throw new InvalidDataException("资料过大，请只保留本队相关内容。");
        if (input.Catalog != null) PcrGuideData.ValidateCatalog(input.Catalog);
        if (input.Snapshot != null) PcrGuideData.ValidateSnapshot(input.Snapshot);
        if (input.OpeningSets != null && (input.OpeningSets.Count != 5 || input.OpeningSets.Keys.Any(id => id <= 0)))
            throw new InvalidDataException("开场 SET 必须对应五个不同角色编号。");
        if (input.Conditions.Any(c => c.UnitId is <= 0) || input.Facts.Any(f => f.UnitId is <= 0))
            throw new InvalidDataException("角色编号无效。");
    }
    private static string Clean(string? value) => (value ?? "").Trim();
    private static bool Same(string? a, string? b) => Clean(a) == Clean(b);
    private static bool Date(string? value, out DateOnly day) => DateOnly.TryParseExact(value, "yyyy-MM-dd",
        CultureInfo.InvariantCulture, DateTimeStyles.None, out day);
    private static bool Number(string value, out int number) => int.TryParse(value, NumberStyles.None,
        CultureInfo.InvariantCulture, out number) && number >= 0 && number <= 100_000;
    public static bool IsWebSource(string? value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && (uri.Scheme == "https" || uri.Scheme == "http") && string.IsNullOrEmpty(uri.UserInfo);

    public static PcrGuideReport Check(PcrGuideInput input, DateTimeOffset now)
    {
        Validate(input);
        var result = new List<PcrGuideFinding>();
        var today = DateOnly.FromDateTime(now.LocalDateTime);
        var source = IsWebSource(input.SourceUrl) ? input.SourceUrl : "攻略来源未填写或链接无效";
        void Add(string status, string item, string detail, string? evidence = null) =>
            result.Add(new(status, item, detail, evidence ?? source));
        void Compare(string item, string? expected, string? actual, string evidence)
        {
            if (string.IsNullOrWhiteSpace(expected) || string.IsNullOrWhiteSpace(actual))
                Add("未知", item, "资料未明确，不能确认一致。", evidence);
            else Add(Same(expected, actual) ? "相符" : "冲突", item,
                $"要求：{expected}；当前：{actual}。", evidence);
        }
        var sourceReady = input.SourceConfirmed && IsWebSource(input.SourceUrl) && !string.IsNullOrWhiteSpace(input.Material);
        if (!sourceReady) Add("未知", "来源核实", "文字材料尚未逐项核实；保留来源提示，不阻断已核对五人的试打。");
        var retrievedValid = Date(input.RetrievedOn, out var retrieved) && retrieved <= today;
        var validityReady = Date(input.ValidThrough, out var until) && retrievedValid && until >= retrieved && until >= today;
        if (!validityReady) Add("未知", "来源时效", "取得日期或复核有效期缺失、无效或已过期；仅作时效提醒。");
        Compare("区服", input.GuideServer, input.TargetServer, source + " · 攻略区服与目标区服");
        Compare("关卡", input.GuideStage, input.TargetStage, source + " · 攻略关卡与目标关卡");
        Compare("版本", input.GuideVersion, input.TargetVersion, source + " · 攻略版本与目标版本");
        if (input.Mode != "AUTO" && input.Mode != "SET/AUTO") Add("未知", "操作方式", "未确认这是一份 AUTO 或 SET/AUTO 作业。");
        var account = string.IsNullOrWhiteSpace(input.AccountLabel) ? "账号尚未标识" : input.AccountLabel;
        Compare("账号区服", input.TargetServer, input.AccountServer, account + " · 局部账号资料");
        Compare("账号资料版本", input.TargetVersion, input.AccountVersion, account + " · 局部账号资料");
        var accountReady = !string.IsNullOrWhiteSpace(input.AccountLabel)
            && Date(input.AccountObservedOn, out var observed) && observed <= today;
        if (!accountReady) Add("未知", "账号资料日期", "账号名称或实际观察日期缺失或无效。", account);
        if (input.Snapshot is { } snapshot)
        {
            var evidence = $"{snapshot.FileName} · SHA256 {snapshot.Sha256} · 账号 {snapshot.AccountId} · {snapshot.ObservedAt}";
            if (!input.SnapshotConfirmed) Add("未知", "导入账号确认", "请核对快照账号、采集时间和来源后确认；文件导入不证明当前登录账号一致。", evidence);
            var at = PcrGuideData.Observation(snapshot.ObservedAt);
            if (at > now || now - at > TimeSpan.FromHours(24)) Add("未知", "快照时效", "快照超过 24 小时或时间异常，需要刷新。", evidence);
            Compare("快照观察日期", at.ToLocalTime().ToString("yyyy-MM-dd"), input.AccountObservedOn, evidence);
            Compare("快照内容版本", snapshot.ContentVersion, input.AccountVersion, evidence);
            Compare("快照区服", "国服 B 站", input.AccountServer, evidence);
            if (input.Catalog == null) Add("未知", "快照角色映射", "缺少对应静态角色表。", evidence);
            else
            {
                Compare("静态表内容版本", snapshot.ContentVersion, input.Catalog.ContentVersion, input.Catalog.Source);
                Compare("静态表区服", snapshot.Server, input.Catalog.Server, input.Catalog.Source);
            }
        }
        if (!input.CoverageConfirmed || input.Conditions.Count == 0
            || !input.Conditions.Any(c => c.Field == "持有" && c.Character != "账号"))
            Add("未知", "条件覆盖", "详细练度、EX 或账号强化条件未整理齐；未知项保留，不要求补齐后才能试打。");
        if (PcrGuideIdentity.Required(input))
        {
            if (input.Identity?.Valid() != true)
                Add("未知", "原图角色核对", "尚无有效的独立头像核对。请载入本作业原图核对资料；旧名单和旧结论不能沿用。");
            else Add(input.Identity.Matches(input) ? "相符" : "冲突", "原图角色核对",
                "原图依次为" + string.Join("、", input.Identity.Members.Select(m => $"{m.Name}（{m.UnitId}）")) + "。" +
                (input.Identity.Matches(input) ? "条件中的编号与原图一致；实际编队仍需开战前核对。" : "来源、区服、关卡或角色编号不一致，不能进入试打。"),
                input.Identity.SourceUrl + " · 原图 SHA256 " + input.Identity.ImageSha256);
        }
        var trialReady = !result.Any(f => f.Status is "冲突" or "未知"
            && f.Item is not ("来源核实" or "来源时效" or "版本" or "操作方式" or "条件覆盖"));
        if (input.OpeningSets != null)
        {
            var complete = input.OpeningConfirmed && input.OpeningSets.Values.All(v => v.HasValue);
            trialReady &= complete;
            Add(complete ? "相符" : "未知", "开场 AUTO / SET", complete
                ? "AUTO 保持开启；" + string.Join("；", input.OpeningSets.Select(p => $"{p.Key} SET {(p.Value == true ? "开启" : "关闭")}"))
                : "请逐人核对 SET 与原标记、特殊说明，再确认开场设置；本轮不执行战中时间表。");
        }
        var owned = new HashSet<(int? Id, string Name, string Form)>();
        foreach (var c in input.Conditions)
        {
            var required = c.Field is "持有" or "SET" or "SET标记";
            var reference = c.Comparison == "参考" && c.Field != "持有";
            var identityReady = !string.IsNullOrWhiteSpace(c.Character) &&
                (c.Character == "账号" || c.UnitId.HasValue || !string.IsNullOrWhiteSpace(c.Form));
            var item = $"{c.Character}（{c.Form}） · {c.Field}";
            var evidence = source + " · " + (string.IsNullOrWhiteSpace(c.Evidence) ? "未给出文字依据" : c.Evidence);
            if (!identityReady)
            { trialReady = false; Add("未知", item, "需明确角色全名、形态与编号。", evidence); continue; }
            if (string.IsNullOrWhiteSpace(c.Evidence) || !(input.Material ?? "").Contains(c.Evidence, StringComparison.Ordinal))
                Add("未知", item, "文字依据尚未对应；不把未核实的练度要求当作执行条件。", evidence);
            if (c.UnitId.HasValue && input.Catalog?.Units.Any(u => u.UnitId == c.UnitId && u.Name == c.Character) != true)
            { trialReady = false; Add("未知", item, "编号与静态角色表未对应，请重新关联角色。", evidence); continue; }
            if (c.UnitId.HasValue) evidence += $"；角色映射：{input.Catalog!.Source} · SHA256 {input.Catalog.Sha256} · 编号 {c.UnitId}";
            if (c.Field is "SET" or "SET标记" && input.OpeningSets != null)
            { Add(input.OpeningConfirmed ? "相符" : "未知", item, $"原文：{c.Expected}；按已确认开场设置执行，战斗时再读回。", evidence); continue; }
            var matches = input.Facts.Where(f => Same(f.Field, c.Field) && (c.UnitId.HasValue
                ? f.UnitId == c.UnitId && Same(f.Character, c.Character)
                : !f.UnitId.HasValue && Same(f.Character, c.Character) && Same(f.Form, c.Form))).ToList();
            if (matches.Count != 1)
            {
                trialReady &= !required;
                var otherForm = input.Facts.Any(f => Same(f.Character, c.Character) && !Same(f.Form, c.Form));
                Add("未知", item, matches.Count > 1 ? "同一字段有多条资料，先消除歧义。" :
                    otherForm ? "只找到同名的其他形态，不能替代此角色。" : "没有这项资料；未出现在局部资料中不等于未持有。", evidence);
                continue;
            }
            var fact = matches[0];
            evidence += $"；账号依据：{fact.Source}（{input.AccountObservedOn}，{account}）";
            if (!fact.Confirmed || string.IsNullOrWhiteSpace(fact.Source) || string.IsNullOrWhiteSpace(fact.Value) || !accountReady)
            { trialReady &= !required; Add("未知", item, "账号值、依据或确认状态不足。", evidence); continue; }
            bool? matched = null;
            var expected = Clean(c.Expected); var value = Clean(fact.Value);
            var numeric = c.Field is "等级" or "Rank" or "星级" or "解放星级" or "专武1等级" or "专武2等级"
                or "UB等级" or "技能1等级" or "技能2等级" or "EX技能等级";
            if (numeric && (!Number(value, out _) || expected.Length > 0 && !Number(expected, out _)))
            { Add("未知", item, "练度值必须是有效的非负整数。", evidence); continue; }
            if (expected.Length == 0)
            { trialReady &= !required; Add("未知", item, $"来源未给出此项要求；账号现状：{fact.Value}。不据此要求补练度。", evidence); continue; }
            // Published clear examples are observations, not proven minimum requirements.
            if (reference)
            {
                var equal = numeric ? int.Parse(value, CultureInfo.InvariantCulture) == int.Parse(expected, CultureInfo.InvariantCulture) : value == expected;
                Add(equal ? "参考一致" : "未知", item, $"作者参考值：{expected}；已知：{value}。" +
                    (equal ? "与展示配置一致，不代表这是最低要求或通关保证。" : "与展示配置不同；仅凭参考值不能判断可用、不可用或需要培养。"), evidence);
                continue;
            }
            if (c.Field == "持有")
            {
                if (c.Comparison == "=" && expected == "是" && (value == "是" || value == "否")) matched = value == "是";
            }
            else if (c.Comparison == "=") matched = value == expected;
            else if (Number(expected, out var need) && Number(value, out var actual))
                matched = c.Comparison switch { ">=" => actual >= need, "<=" => actual <= need, _ => null };
            if (!matched.HasValue) Add("未知", item, "仅支持 =、>=、<=、参考；范围比较必须是非负整数。持有使用 =，填写是或否。", evidence);
            else Add(matched.Value ? "相符" : "冲突", item, $"要求 {c.Comparison} {expected}；已知 {value}。", evidence);
            trialReady &= !required || matched == true;
            if (c.Field == "持有" && matched == true && c.Character != "账号") owned.Add((c.UnitId, c.Character, c.Form));
        }
        var team = input.Conditions.Where(c => c.Character != "账号").Select(c => (c.UnitId, c.Character, c.Form)).ToHashSet();
        // Trial advice is not execution permission; the battle path still verifies its own preconditions.
        trialReady &= owned.Count == 5 && owned.SetEquals(team);
        var summary = trialReady ? (input.RequiresImageReview || input.Identity?.ImportedSource != null) && input.Identity?.Executable() != true
                ? "离线对照已完成：五人、账号与开场设置已对应；此导入材料尚未校准游戏执行，暂不能开战。练度和 EX 差异仅作提示。"
                : "可以试打：五人、账号与开场设置已对应。练度、EX 和强化差异仅作提示；胜负以游戏结果为准，战败后停止，不自动重试。"
            : result.Any(f => f.Status == "冲突") ? "存在明确冲突，当前不能按这份作业使用。"
            : result.Any(f => f.Status == "未知") ? "资料不足，尚不能判断账号是否适用。"
            : result.Any(f => f.Status == "参考一致") ? "已知必需条件相符，参考配置一致；参考练度不是最低要求，也不保证通关。"
            : "已整理并确认的条件相符；这不是通关保证。";
        return new(Fingerprint(input), now, summary, result);
    }

    public static PcrGuideInput RealExample() => new()
    {
        Title = "红焰深域 2-1 · 真实来源待补充",
        SourceUrl = "https://www.bilibili.com/video/BV1Xunyz2E21/",
        RetrievedOn = "2026-10-18",
        Material = "作者：檬猫Neko。来源页面标题将火 2-1 至 2-10 归为 SetAuto 合集；分集列表列出 2-1，另将 2-10 标为半自动。\n本样例仅取得页面文字索引，未取得视频画面中的队伍、练度、强化和操作细节；区服与版本尚未核实。",
        GuideStage = "红焰深域 2-1", Mode = "SET/AUTO", TargetStage = "红焰深域 2-1",
        SourceConfirmed = false, CoverageConfirmed = false
    };

    public static PcrGuideInput TextExample()
    {
        var input = new PcrGuideInput
        {
            Title = "日服红焰深域 2-1 · 文字参考（非国服验证）",
            SourceUrl = "https://gamewith.jp/pricone-re/article/show/449888",
            RetrievedOn = "2026-10-18", GuideServer = "日服", TargetServer = "国服 B 站",
            GuideStage = "红焰深域 2-1", TargetStage = "红焰深域 2-1", Mode = "SET/AUTO",
            SourceConfirmed = true, CoverageConfirmed = false,
            Material = "来源：GameWith 日服火深域攻略的 2-1~2-9 魔法全自动示例，五人均 SET。以下转述作者展示配置，不当作最低练度。\n" +
                "エリス：5 星、Rank 32。\nアメス：5 星、Rank 32，标注突破。\nカスミ：6 星、Rank 32、专武 310，标注突破。\n" +
                "ネネカ：5 星、Rank 32、专武 310。\nアネモネ：5 星、Rank 32。\n" +
                "本次只整理上述队伍字段。页面另列各属性强化数值，尚未逐项整理；角色等级、技能、EX 装备和适用游戏版本仍待核实。" +
                "未将专武栏的横线解释为 0。保留原文角色名，尚未关联国服编号；页面更新日期不作为游戏版本。" +
                "这不是此前檬猫的国服作业，不能合并其区服或配置。"
        };
        foreach (var (name, stars, detail) in new[] {
            ("エリス", "5", "エリス：5 星、Rank 32。"),
            ("アメス", "5", "アメス：5 星、Rank 32，标注突破。"),
            ("カスミ", "6", "カスミ：6 星、Rank 32、专武 310，标注突破。"),
            ("ネネカ", "5", "ネネカ：5 星、Rank 32、专武 310。"),
            ("アネモネ", "5", "アネモネ：5 星、Rank 32。") })
        {
            void Add(string field, string value, string comparison = "参考", string? evidence = null) =>
                input.Conditions.Add(new() { Character = name, Form = "原版（日服名称）", Field = field,
                    Expected = value, Comparison = comparison, Evidence = evidence ?? detail });
            Add("持有", "是", "="); Add("星级", stars); Add("Rank", "32");
            Add("SET", "开启", "=", "五人均 SET");
            if (name is "カスミ" or "ネネカ") Add("专武1等级", "310");
            if (name is "アメス" or "カスミ") Add("限界突破", "是");
        }
        return input;
    }
}

public sealed class PcrGuideStore(string path)
{
    private string? loadedHash;
    private bool loaded;
    private static string Hash(string text) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text)));
    private string? Read()
    {
        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (stream.Length > 1_000_000) throw new InvalidDataException("资料文件过大。");
            using var reader = new StreamReader(stream, new UTF8Encoding(false, true));
            return reader.ReadToEnd();
        }
        catch (FileNotFoundException) { return null; }
        catch (DirectoryNotFoundException) { return null; }
    }
    public PcrGuideDocument Load()
    {
        loaded = false;
        var text = Read();
        var doc = text == null ? new PcrGuideDocument() : PcrGuideReview.Parse(text);
        loadedHash = text == null ? null : Hash(text);
        loaded = true;
        // Persisted conclusions are never trusted independently of the exact input and today's expiry.
        if (doc.Report != null)
            doc.Report = doc.Report.InputHash == PcrGuideReview.Fingerprint(doc.Input)
                ? PcrGuideReview.Check(doc.Input, DateTimeOffset.Now) : null;
        return doc;
    }
    public void Save(PcrGuideDocument doc)
    {
        if (!loaded) throw new InvalidOperationException("尚未成功读取原文件，禁止覆盖。请保留原文件并排查读取失败。");
        PcrGuideReview.Validate(doc.Input);
        if (doc.Report != null && doc.Report.InputHash != PcrGuideReview.Fingerprint(doc.Input)) doc.Report = null;
        var json = PcrGuideReview.Serialize(doc);
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        using var gate = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        var current = Read();
        if ((current == null ? null : Hash(current)) != loadedHash)
            throw new IOException("资料文件已被其他窗口修改。请重新打开后再编辑，当前内容未覆盖。");
        JsonHelper.SaveJsonTextAtomicallyOrThrow(path, json);
        loadedHash = Hash(json);
    }
}
