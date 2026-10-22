using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MFAAvalonia.Helper;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed class PcrClanGuideDocument
{
    public int Version { get; set; } = 1;
    public string BundlePath { get; set; } = "";
    public string BundleSha256 { get; set; } = "";
    public string SelectedJobId { get; set; } = "";
    public JObject Job { get; set; } = new();
    public string SnapshotPath { get; set; } = "";
    public string SnapshotSha256 { get; set; } = "";
    public string? ConfirmedFor { get; set; }
    public Dictionary<string, PcrClanGuideSavedJob> SavedJobs { get; set; } = new(StringComparer.Ordinal);
}
public sealed class PcrClanGuideSavedJob
{
    public JObject Job { get; set; } = new();
    public string SnapshotPath { get; set; } = "";
    public string SnapshotSha256 { get; set; } = "";
    public string? ConfirmedFor { get; set; }
}

// The Agent selection contains references, never a second account cache or another login path.
public static class PcrClanGuide
{
    public const string ProfileId = "pcr-agent-v1";
    public static string StatePath => Path.Combine(AppPaths.ConfigDirectory, "agent-guide-review.json");
    private static readonly string[] JobFields = { "period", "phase", "round_min", "round_max", "boss", "boss_name", "package", "team_label", "unit_ids", "stars", "auto", "set_by_unit", "switches", "support_mode", "support_unit_id", "reference_damage", "source" };
    public static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    public static string? Stamp(string path) => File.Exists(path) ? Hash(PcrGuideData.ReadLocal(Path.GetFullPath(path))) : null;
    private static JObject Material(JObject job) => new(JobFields.Select(key => new JProperty(key, job[key]?.DeepClone() ?? JValue.CreateNull())));
    public static JObject ReadJson(string path) => ParseJson(PcrGuideData.ReadLocal(Path.GetFullPath(path)));
    // One review owns these bytes; every source is rechecked before the result escapes.
    internal sealed class PlanInputs
    {
        private readonly Dictionary<string, (byte[] Bytes, string Hash)> files = new(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, JObject> json = new(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, PcrGuideCatalog> catalogs = new(StringComparer.OrdinalIgnoreCase);
        internal byte[] Read(string path)
        {
            path = Local(path);
            if (!files.TryGetValue(path, out var file))
            {
                var bytes = PcrGuideData.ReadLocal(path); file = (bytes, Hash(bytes)); files.Add(path, file);
            }
            return file.Bytes;
        }
        internal string Stamp(string path) { _ = Read(path); return files[Local(path)].Hash; }
        internal JObject Json(string path)
        {
            path = Local(path);
            if (!json.TryGetValue(path, out var value)) { value = ParseJson(Read(path)); json.Add(path, value); }
            return value;
        }
        internal PcrGuideCatalog Catalog(string configDirectory, string repository, string version)
        {
            var path = Path.GetFullPath(Path.Combine(configDirectory, "guide-review.json"));
            if (!catalogs.TryGetValue(path, out var catalog))
            {
                var bytes = Read(path);
                if (bytes.Length > 1_000_000) throw new InvalidDataException("资料文件过大。");
                using var stream = new MemoryStream(bytes);
                using var text = new StreamReader(stream, new UTF8Encoding(false, true));
                catalog = PcrGuideReview.Parse(text.ReadToEnd()).Input.Catalog
                    ?? throw new InvalidDataException("缺少原静态角色表，请通过原攻略账号模块载入后再核对。");
                catalogs.Add(path, catalog);
            }
            if (catalog.ContentVersion != version) throw new InvalidDataException("静态角色表与账号内容版本不一致。");
            return catalog;
        }
        internal void Verify()
        {
            foreach (var file in files)
                if (PcrClanGuide.Stamp(file.Key) != file.Value.Hash)
                    throw new IOException("材料在核对过程中发生变化，本次未生成计划。");
        }
    }
    private static JObject ParseJson(byte[] bytes)
    {
        using var stream = new MemoryStream(bytes);
        using var text = new StreamReader(stream, new UTF8Encoding(false, true));
        using var reader = new JsonTextReader(text) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 };
        var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        if (reader.Read()) throw new InvalidDataException("JSON 后有多余内容。");
        return value;
    }
    public static PcrClanGuideDocument Read(string path) => Read(path, out _);
    public static PcrClanGuideDocument Read(string path, out string? stamp)
    {
        stamp = null;
        if (!File.Exists(path)) return new();
        var bytes = PcrGuideData.ReadLocal(Path.GetFullPath(path));
        var root = ParseJson(bytes);
        if (root.Properties().Any(p => !new[] { "Version", "BundlePath", "BundleSha256", "SelectedJobId", "Job", "SnapshotPath", "SnapshotSha256", "ConfirmedFor", "SavedJobs" }.Contains(p.Name)))
            throw new InvalidDataException("公会战作业保存格式包含未知字段，请保留原文件。");
        var result = root.ToObject<PcrClanGuideDocument>() ?? throw new InvalidDataException("公会战作业为空。");
        if (result.Version != 1 || result.Job == null || result.SavedJobs == null || result.SavedJobs.Count > 2000 || result.SavedJobs.Values.Any(value => value == null || value.Job == null)) throw new InvalidDataException("不支持此公会战作业版本。");
        stamp = Hash(bytes);
        return result;
    }
    public static JToken Canonical(JToken token) => token switch
    {
        JObject obj => new JObject(obj.Properties().OrderBy(p => p.Name, StringComparer.Ordinal).Select(p => new JProperty(p.Name, Canonical(p.Value)))),
        JArray array => new JArray(array.Select(Canonical)),
        _ => token.DeepClone()
    };
    public static string Fingerprint(JObject plan)
    {
        var copy = (JObject)plan.DeepClone(); copy.Remove("confirmed_for");
        return Hash(Encoding.UTF8.GetBytes(Canonical(copy).ToString(Formatting.None)));
    }
    private static string Text(JToken? token, string label, bool empty = false, int max = 200)
    {
        if (token?.Type != JTokenType.String || (!empty && string.IsNullOrWhiteSpace((string?)token)) || ((string)token!).Length > max)
            throw new InvalidDataException(label + "尚未明确。");
        return (string)token!;
    }
    private static int Number(JToken? token, string label, int min, int max)
    {
        if (token?.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out var value) || value < min || value > max)
            throw new InvalidDataException(label + "无效或尚未核对。");
        return (int)value;
    }
    private static long LongNumber(JToken? token, string label, long min, long max)
    {
        if (token?.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out var value) || value < min || value > max)
            throw new InvalidDataException(label + "无效或尚未核对。");
        return value;
    }
    private static string Local(string path)
    {
        if (!Path.IsPathFullyQualified(path) || path.StartsWith(@"\\") || path.StartsWith("//")) throw new InvalidDataException("请使用本机原文件。");
        return Path.GetFullPath(path);
    }
    public static JObject Bundle(string path)
    {
        return ValidateBundle(ReadJson(Local(path)));
    }
    private static JObject ValidateBundle(JObject root)
    {
        if ((int?)root["version"] != 1 || root["source"] is not JObject || root["pages"] is not JArray pages || pages.Count is < 1 or > 100
            || root["jobs"] is not JArray jobs || jobs.Count is < 1 or > 500 || jobs.Any(x => x is not JObject))
            throw new InvalidDataException("视频作业包无效或未找到阵容页。");
        var ids = jobs.Select(x => Text(x["source"]?["job_id"], "作业标识")).ToArray();
        if (ids.Distinct(StringComparer.Ordinal).Count() != ids.Length) throw new InvalidDataException("视频作业标识重复。");
        return root;
    }
    public static void Select(PcrClanGuideDocument document, string bundlePath, string jobId)
    {
        bundlePath = Local(bundlePath);
        var before = Stamp(bundlePath);
        var bundle = Bundle(bundlePath);
        var jobs = ((JArray)bundle["jobs"]!).OfType<JObject>().Where(j => (string?)j["source"]?["job_id"] == jobId).ToArray();
        if (jobs.Length != 1 || Stamp(bundlePath) != before) throw new InvalidDataException("所选作业不存在或材料读取时发生变化。");
        document.BundlePath = bundlePath; document.BundleSha256 = before!; document.SelectedJobId = jobId;
        document.Job = (JObject)jobs[0].DeepClone(); document.ConfirmedFor = null;
        if (document.SavedJobs.TryGetValue(before + ":" + jobId, out var saved) && JToken.DeepEquals(saved.Job["source"], jobs[0]["source"]))
        {
            document.Job = (JObject)saved.Job.DeepClone();
            document.ConfirmedFor = saved.SnapshotPath == document.SnapshotPath && saved.SnapshotSha256 == document.SnapshotSha256 ? saved.ConfirmedFor : null;
        }
    }
    public static PcrGuideCatalog Catalog(string configDirectory, string repository, string version)
    {
        var existing = Path.Combine(configDirectory, "guide-review.json");
        if (File.Exists(existing))
        {
            var input = new PcrGuideStore(existing).Load().Input;
            if (input.Catalog is { } catalog && catalog.ContentVersion == version) return catalog;
        }
        throw new InvalidDataException("缺少与账号内容版本 " + version + " 一致的原静态角色表，请通过原攻略账号模块载入后再核对。");
    }
    public static IReadOnlyList<string> SnapshotPaths(string configDirectory)
    {
        var root = Path.Combine(configDirectory, "guide-accounts", "cn-bilibili");
        return Directory.Exists(root) ? Directory.EnumerateDirectories(root).SelectMany(d => Directory.EnumerateFiles(d, "*.json")).OrderBy(p => p, StringComparer.Ordinal).ToArray() : Array.Empty<string>();
    }
    public static JObject Snapshot(PcrClanGuideDocument document, string configDirectory, string repository, DateTimeOffset now, out PcrGuideCatalog catalog)
    {
        var inputs = new PlanInputs();
        var snapshot = Snapshot(document, configDirectory, repository, now, inputs, out catalog);
        inputs.Verify(); return snapshot;
    }
    internal static JObject Snapshot(PcrClanGuideDocument document, string configDirectory, string repository, DateTimeOffset now, PlanInputs inputs, out PcrGuideCatalog catalog)
    {
        var path = Local(document.SnapshotPath);
        var root = Path.GetFullPath(Path.Combine(configDirectory, "guide-accounts", "cn-bilibili")) + Path.DirectorySeparatorChar;
        if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("请选择原账号缓存中的资料。");
        var bytes = inputs.Read(path);
        if (inputs.Stamp(path) != document.SnapshotSha256) throw new InvalidDataException("账号缓存已改变，请重新选择并核对。");
        var header = inputs.Json(path);
        catalog = inputs.Catalog(configDirectory, repository, Text(header["content_version"], "内容版本"));
        var account = Text(header["account_id"], "账号编号", max: 30);
        var expectedName = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(catalog.ContentVersion))) + ".json";
        if (!string.Equals(path, Path.Combine(root, account, expectedName), StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("账号缓存文件与账号或内容版本不对应。");
        var value = PcrGuideData.AccountSnapshot(bytes, catalog, account, now);
        var at = PcrGuideData.Observation((string)value["observed_at"]!);
        if (now - at > TimeSpan.FromHours(24)) throw new InvalidDataException("账号缓存已超过 24 小时，请在空闲时通过原账号模块刷新。");
        return value;
    }
    public static JObject BuildPlan(PcrClanGuideDocument document, string configDirectory, string repository, DateTimeOffset now)
    {
        var inputs = new PlanInputs();
        var plan = BuildPlan(document, configDirectory, repository, now, inputs);
        inputs.Verify(); return plan;
    }
    internal static JObject BuildPlan(PcrClanGuideDocument document, string configDirectory, string repository, DateTimeOffset now, PlanInputs inputs)
    {
        if (inputs.Stamp(document.BundlePath) != document.BundleSha256) throw new InvalidDataException("视频作业材料已改变，请重新载入核对。");
        var bundle = ValidateBundle(inputs.Json(document.BundlePath));
        var original = ((JArray)bundle["jobs"]!).OfType<JObject>().SingleOrDefault(j => (string?)j["source"]?["job_id"] == document.SelectedJobId)
            ?? throw new InvalidDataException("所选作业已不存在。");
        var job = document.Job;
        if (job["source"] is not JObject source || !JToken.DeepEquals(source, original["source"])) throw new InvalidDataException("作业来源与所选原件不符。");
        var url = Text(source["url"], "视频链接", max: 2000);
        var bvid = Text(source["bvid"], "BV 编号");
        if (!Regex.IsMatch(bvid, "^BV[0-9A-Za-z]{10}$") || !Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.Host != "www.bilibili.com"
            || uri.UserInfo != "" || uri.AbsolutePath.TrimEnd('/') != "/video/" + bvid) throw new InvalidDataException("视频来源必须对应原 B 站 HTTPS 链接。");
        var sourcePage = Number(source["page"], "分 P", 1, 100);
        var queryPages = uri.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries).Select(pair => pair.Split('=', 2))
            .Where(pair => Uri.UnescapeDataString(pair[0]) == "p").Select(pair => pair.Length == 2 ? Uri.UnescapeDataString(pair[1]) : "").ToArray();
        if (queryPages.Length > 1 || queryPages.Length == 0 && sourcePage != 1 || queryPages.Length == 1 && queryPages[0] != sourcePage.ToString(CultureInfo.InvariantCulture))
            throw new InvalidDataException("视频链接与作业分 P 不一致。");
        _ = LongNumber(source["cid"], "视频分集", 1, long.MaxValue);
        _ = Number(source["at_seconds"], "原图时间", 0, 86400); _ = Text(source["job_id"], "作业标识");
        var image = Local(Text(source["image_path"], "原图路径", max: 2048));
        if (inputs.Stamp(image) != Text(source["image_sha256"], "原图摘要")) throw new InvalidDataException("阵容原图已改变，请重新载入核对。");
        var snapshot = Snapshot(document, configDirectory, repository, now, inputs, out var catalog);
        if (!Regex.IsMatch((string)snapshot["content_version"]!, @"\A[0-9]{12}\z")) throw new InvalidDataException("执行所需的内容版本必须为 12 位数字，请载入对应原静态表与账号缓存。");
        var period = Text(job["period"], "会战期次");
        if (!Regex.IsMatch(period, "^[0-9]{4}-(0[1-9]|1[0-2])$")) throw new InvalidDataException("会战期次填写 YYYY-MM。");
        if (!new[] { "B", "C", "BC", "D", "E" }.Contains(Text(job["phase"], "阶段"))) throw new InvalidDataException("请选择 B、C、BC、D 或 E 阶段。");
        var first = Number(job["round_min"], "起始周目", 1, 100000);
        if (job["round_max"]?.Type != JTokenType.Null) _ = Number(job["round_max"], "结束周目", first, 100000);
        _ = Number(job["boss"], "BOSS 序号", 1, 5); _ = Text(job["boss_name"], "BOSS 名称", true);
        _ = Text(job["package"], "套餐"); _ = Text(job["team_label"], "队伍名称");
        if (job["unit_ids"] is not JArray members || members.Count != 5) throw new InvalidDataException("请核对完整五人队伍。");
        var ids = members.Select(x => Number(x, "角色编号", 1, 100000000)).ToArray();
        if (ids.Distinct().Count() != 5 || ids.Any(id => !catalog.Units.Any(u => u.UnitId == id))) throw new InvalidDataException("五人编号重复或不在原角色目录中。");
        if (job["stars"] is not JArray stars || stars.Count != 5) throw new InvalidDataException("请核对五人的战斗星级。");
        var units = ((JArray)snapshot["units"]!).OfType<JObject>().ToDictionary(u => (int)u["unit_id"]!);
        var unknown = ((JArray)snapshot["unknown_fields"]!).Values<string>().ToHashSet(StringComparer.Ordinal);
        bool Unknown(string path) => unknown.Any(item => path == item || path.StartsWith(item + ".", StringComparison.Ordinal));
        bool OwnershipUnknown(int id) => Unknown("units") || Unknown("unit_list") || Unknown("ownership") || Unknown("units.ownership") || Unknown("units." + id.ToString(CultureInfo.InvariantCulture) + ".ownership");
        var resolvedStars = new JArray();
        for (var index = 0; index < 5; index++)
        {
            var star = stars[index];
            if (star.Type == JTokenType.Null && !Unknown("units." + ids[index].ToString(CultureInfo.InvariantCulture) + ".battle_rarity") && units.TryGetValue(ids[index], out var owned)) star = owned["battle_rarity"] ?? JValue.CreateNull();
            resolvedStars.Add(Number(star, "战斗星级", 1, 6));
        }
        if (job["auto"]?.Type != JTokenType.Boolean) throw new InvalidDataException("请明确开场 AUTO。");
        void Sets(JToken? token, bool complete)
        {
            if (token is not JObject sets || complete && sets.Count != 5 || sets.Properties().Any(p => !ids.Select(x => x.ToString(CultureInfo.InvariantCulture)).Contains(p.Name) || p.Value.Type != JTokenType.Boolean))
                throw new InvalidDataException("SET 必须对应本队角色，未知不能默认开启。");
        }
        Sets(job["set_by_unit"], true);
        if (job["switches"] is not JArray switches || switches.Count > 90) throw new InvalidDataException("秒数轴无效。");
        var previous = 90;
        foreach (var step in switches)
        {
            if (step is not JObject row || row.Properties().Any(p => !new[] { "remaining_seconds", "auto", "set_by_unit" }.Contains(p.Name))) throw new InvalidDataException("秒数轴格式无效。");
            var second = Number(row["remaining_seconds"], "切换剩余秒数", 0, 89);
            if (second >= previous) throw new InvalidDataException("秒数轴须按剩余秒数严格递减，不得重复。");
            previous = second;
            if (row["auto"]?.Type is not (JTokenType.Boolean or JTokenType.Null)) throw new InvalidDataException("秒数轴 AUTO 只能为开启、关闭或不变。");
            Sets(row["set_by_unit"], false);
            if (row["auto"]?.Type == JTokenType.Null && !((JObject)row["set_by_unit"]!).HasValues) throw new InvalidDataException("秒数轴每行至少切换一个开关。");
        }
        var mode = Text(job["support_mode"], "支援方式");
        if (!new[] { "none", "qsjs", "specified" }.Contains(mode)) throw new InvalidDataException("请选择无支援、缺谁借谁或指定支援。");
        var support = job["support_unit_id"];
        if (mode == "specified" && !ids.Contains(Number(support, "指定支援", 1, 100000000)) || mode != "specified" && support?.Type != JTokenType.Null)
            throw new InvalidDataException("指定支援必须是本队角色，其他支援方式不填写指定角色。");
        var absent = ids.Where(id => !units.ContainsKey(id)).ToArray();
        if (ids.Any(id => (mode != "specified" || id != (int)support!) && OwnershipUnknown(id))) throw new InvalidDataException("本队自有角色的持有资料标为未知，不能推断持有或缺少。");
        if (mode == "qsjs" && absent.Length > 0 && snapshot["ownership_complete"]?.Value<bool>() != true) throw new InvalidDataException("原账号资料的持有范围不完整，缺行不能当作缺角色。");
        if (mode == "none" && absent.Length > 0 || mode == "qsjs" && absent.Length > 1 || mode == "specified" && absent.Any(id => id != (int)support!))
            throw new InvalidDataException("本队缺少的角色超出所选支援方式，不能替换其他角色。");
        if (job["reference_damage"]?.Type != JTokenType.Null) _ = LongNumber(job["reference_damage"], "参考伤害", 1, long.MaxValue);
        var plan = new JObject { ["version"] = 1, ["server"] = "cn-bilibili", ["account_id"] = snapshot["account_id"]!.DeepClone(), ["content_version"] = snapshot["content_version"]!.DeepClone() };
        foreach (var key in JobFields) plan[key] = job[key]?.DeepClone() ?? JValue.CreateNull();
        plan["stars"] = resolvedStars;
        plan["snapshot"] = new JObject { ["path"] = Local(document.SnapshotPath), ["sha256"] = document.SnapshotSha256 };
        plan["confirmed_for"] = Fingerprint(plan);
        return plan;
    }
    public static JObject CreatePlan(string? repository = null) => CreatePlan(StatePath, AppPaths.ConfigDirectory, repository ?? PcrAlphaOwner.Repository, DateTimeOffset.Now);
    public static JObject CreatePlan(string statePath, string configDirectory, string repository, DateTimeOffset now)
    {
        var document = Read(statePath);
        var plan = BuildPlan(document, configDirectory, repository, now);
        if (document.ConfirmedFor != (string?)plan["confirmed_for"]) throw new InvalidOperationException("作业或账号资料已变化，请核对五人和操作后一次确认保存。");
        return plan;
    }
    public static string Save(PcrClanGuideDocument document, string path, string? expectedHash, string configDirectory, string repository, DateTimeOffset now, bool confirm)
    {
        document.ConfirmedFor = null;
        if (confirm) document.ConfirmedFor = (string?)BuildPlan(document, configDirectory, repository, now)["confirmed_for"];
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        using var gate = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        if (Stamp(path) != expectedHash) throw new IOException("作业已被其他窗口修改，请重开后核对；原文件未覆盖。");
        if (!string.IsNullOrEmpty(document.SelectedJobId) && document.BundleSha256.Length == 64)
            document.SavedJobs[document.BundleSha256 + ":" + document.SelectedJobId] = new() { Job = Material(document.Job), ConfirmedFor = document.ConfirmedFor, SnapshotPath = document.SnapshotPath, SnapshotSha256 = document.SnapshotSha256 };
        var saved = JObject.FromObject(document); saved["Job"] = Material(document.Job);
        var json = saved.ToString(Formatting.Indented); JsonHelper.SaveJsonTextAtomicallyOrThrow(path, json);
        return Hash(Encoding.UTF8.GetBytes(json));
    }
    public static void SavePublicGuide(PcrClanGuideDocument document, string configDirectory, string repository)
    {
        var header = ReadJson(document.SnapshotPath);
        var catalog = Catalog(configDirectory, repository, (string)header["content_version"]!);
        var job = document.Job;
        var input = new PcrGuideInput { Title = $"{job["package"]} · {job["team_label"]}", SourceUrl = (string?)job["source"]?["url"] ?? "",
            GuideServer = "国服 B 站", GuideStage = $"公会战 {job["period"]} {job["phase"]} BOSS {job["boss"]}", Material = Material(job).ToString(Formatting.Indented), Catalog = catalog };
        foreach (var id in ((JArray)job["unit_ids"]!).Values<int>())
            input.Conditions.Add(new PcrGuideCondition { UnitId = id, Character = catalog.Units.Single(u => u.UnitId == id).Name, Form = "编号 " + id, Field = "持有", Expected = "是" });
        var library = new PcrGuideLibrary(Path.Combine(configDirectory, "guide-library")); library.Reload();
        // Existing public entries remain historical material; the current edits live in the Agent selection.
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(PcrGuideReview.Serialize(new { input.Title, input.SourceUrl, input.GuideServer, input.GuideStage,
            Units = input.Conditions.Where(c => c.UnitId.HasValue).Select(c => c.UnitId!.Value).Distinct().Order().ToArray() }))));
        if (!library.Entries.Any(entry => entry.Key == key)) _ = library.Save(input);
    }
}
