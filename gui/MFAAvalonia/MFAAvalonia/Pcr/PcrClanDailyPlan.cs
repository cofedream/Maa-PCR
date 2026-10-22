using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MFAAvalonia.Helper;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed class PcrClanDailyPlanDocument
{
    [JsonProperty("version")] public int Version { get; set; } = 1;
    [JsonProperty("policy")] public string Policy { get; set; } = PcrClanDailyPlan.Policy;
    [JsonProperty("slots")] public List<PcrClanDailyPlanSlot> Slots { get; set; } = new();
}
public sealed class PcrClanDailyPlanSlot
{
    [JsonProperty("slot")] public int Slot { get; set; }
    [JsonProperty("bundle_path")] public string BundlePath { get; set; } = "";
    [JsonProperty("bundle_sha256")] public string BundleSha256 { get; set; } = "";
    [JsonProperty("job_id")] public string JobId { get; set; } = "";
    [JsonProperty("material_sha256")] public string MaterialSha256 { get; set; } = "";
    [JsonProperty("confirmed_for")] public string ConfirmedFor { get; set; } = "";
}
public sealed record PcrClanDailyPlanReview(bool Complete, IReadOnlyList<string> Slots, IReadOnlyList<string> Warnings);

// These are references to reviewed jobs. Actual attempts and compensation remain game observations.
public static class PcrClanDailyPlan
{
    public const string Policy = "same_boss_if_observed_else_hold";
    public static string StatePath => PathFor(AppPaths.ConfigDirectory);
    public static string PathFor(string configDirectory) => Path.Combine(configDirectory, "clan-battle-daily-plan.json");
    private static string Local(string path)
    {
        if (!Path.IsPathFullyQualified(path) || path.StartsWith(@"\\") || path.StartsWith("//") || path.Length > 2048)
            throw new InvalidDataException("三刀预设只引用本机原文件。");
        var full = Path.GetFullPath(path);
        if (full[Path.GetPathRoot(full)!.Length..].Contains(':')) throw new InvalidDataException("不能使用文件附加数据流。");
        return full;
    }
    private static bool Digest(string? value) => value != null && Regex.IsMatch(value, "^[0-9a-f]{64}$");
    private static string Material(JObject job) => PcrClanGuide.Hash(Encoding.UTF8.GetBytes(PcrClanGuide.Canonical(job).ToString(Formatting.None)));
    private static void Validate(PcrClanDailyPlanDocument document)
    {
        if (document.Version != 1 || document.Policy != Policy || document.Slots == null || document.Slots.Count > 3)
            throw new InvalidDataException("三刀预设版本或补偿策略无效，请保留原文件。");
        if (document.Slots.Any(s => s == null || s.Slot is < 1 or > 3) || document.Slots.Select(s => s.Slot).Distinct().Count() != document.Slots.Count)
            throw new InvalidDataException("三刀预设位置重复或超出 1～3。");
        foreach (var slot in document.Slots)
        {
            _ = Local(slot.BundlePath);
            if (!Digest(slot.BundleSha256) || !Digest(slot.MaterialSha256) || !Digest(slot.ConfirmedFor) || string.IsNullOrWhiteSpace(slot.JobId) || slot.JobId.Length > 200)
                throw new InvalidDataException("三刀预设缺少作业原件或核对摘要。");
        }
    }
    public static PcrClanDailyPlanDocument Read(string path, out string? stamp)
    {
        path = Local(path); stamp = null;
        if (!File.Exists(path)) return new();
        var bytes = PcrGuideData.ReadLocal(path);
        using var stream = new MemoryStream(bytes);
        using var text = new StreamReader(stream, new UTF8Encoding(false, true));
        using var reader = new JsonTextReader(text) { DateParseHandling = DateParseHandling.None, MaxDepth = 8 };
        var root = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        if (reader.Read() || root.Properties().Any(p => !new[] { "version", "policy", "slots" }.Contains(p.Name)) || root.Count != 3
            || root["version"]?.Type != JTokenType.Integer || root["policy"]?.Type != JTokenType.String || root["slots"] is not JArray slots || slots.Count > 3)
            throw new InvalidDataException("三刀预设格式无效，请保留原文件。");
        var fields = new[] { "slot", "bundle_path", "bundle_sha256", "job_id", "material_sha256", "confirmed_for" };
        foreach (var slot in slots)
            if (slot is not JObject row || row.Count != fields.Length || row.Properties().Any(p => !fields.Contains(p.Name)) || row["slot"]?.Type != JTokenType.Integer
                || fields.Skip(1).Any(key => row[key]?.Type != JTokenType.String)) throw new InvalidDataException("三刀预设引用格式无效。");
        var document = root.ToObject<PcrClanDailyPlanDocument>() ?? throw new InvalidDataException("三刀预设为空。");
        Validate(document); stamp = PcrClanGuide.Hash(bytes); return document;
    }
    private static (PcrClanGuideDocument Document, JObject Plan) Resolve(PcrClanDailyPlanSlot slot, PcrClanGuideDocument saved, string configDirectory, string repository, DateTimeOffset now, PcrClanGuide.PlanInputs? inputs = null)
    {
        var key = slot.BundleSha256 + ":" + slot.JobId;
        if (!saved.SavedJobs.TryGetValue(key, out var job) || Material(job.Job) != slot.MaterialSha256 || job.ConfirmedFor != slot.ConfirmedFor)
            throw new InvalidDataException("原队伍核对已变化，请重新设定此刀。");
        var document = new PcrClanGuideDocument { BundlePath = slot.BundlePath, BundleSha256 = slot.BundleSha256, SelectedJobId = slot.JobId,
            Job = (JObject)job.Job.DeepClone(), SnapshotPath = job.SnapshotPath, SnapshotSha256 = job.SnapshotSha256, ConfirmedFor = job.ConfirmedFor };
        var plan = inputs == null ? PcrClanGuide.BuildPlan(document, configDirectory, repository, now)
            : PcrClanGuide.BuildPlan(document, configDirectory, repository, now, inputs);
        if ((string?)plan["confirmed_for"] != slot.ConfirmedFor) throw new InvalidDataException("原队伍或账号资料已变化，请重新核对。");
        return (document, plan);
    }
    public static PcrClanGuideDocument Load(PcrClanDailyPlanDocument document, int slot, string guideStatePath, string configDirectory, string repository, DateTimeOffset now, out string? guideHash)
    {
        Validate(document);
        if (slot is < 1 or > 3) throw new ArgumentOutOfRangeException(nameof(slot));
        var selected = document.Slots.SingleOrDefault(s => s.Slot == slot) ?? throw new InvalidDataException("此刀尚未设置。请先核对并保存队伍。");
        var saved = PcrClanGuide.Read(Local(guideStatePath), out guideHash);
        var value = Resolve(selected, saved, configDirectory, repository, now).Document;
        value.SavedJobs = saved.SavedJobs;
        if (PcrClanGuide.Stamp(guideStatePath) != guideHash) throw new IOException("原作业在载入时发生变化，本次未载入。");
        return value;
    }
    public static void Assign(PcrClanDailyPlanDocument document, int slot, PcrClanGuideDocument current, string guideStatePath, string configDirectory, string repository, DateTimeOffset now)
    {
        Validate(document);
        if (slot is < 1 or > 3) throw new ArgumentOutOfRangeException(nameof(slot));
        var plan = PcrClanGuide.BuildPlan(current, configDirectory, repository, now);
        if (current.ConfirmedFor == null || current.ConfirmedFor != (string?)plan["confirmed_for"]) throw new InvalidDataException("请先核对并保存当前五人和操作，再设为三刀目标。");
        var saved = PcrClanGuide.Read(Local(guideStatePath), out var before);
        var key = current.BundleSha256 + ":" + current.SelectedJobId;
        if (!saved.SavedJobs.TryGetValue(key, out var job)) throw new InvalidDataException("当前队伍尚未保存到原作业库。");
        var value = new PcrClanDailyPlanSlot { Slot = slot, BundlePath = Local(current.BundlePath), BundleSha256 = current.BundleSha256,
            JobId = current.SelectedJobId, MaterialSha256 = Material(job.Job), ConfirmedFor = current.ConfirmedFor };
        _ = Resolve(value, saved, configDirectory, repository, now);
        if (PcrClanGuide.Stamp(guideStatePath) != before) throw new IOException("原作业在核对时发生变化，本次未设定。");
        document.Slots.RemoveAll(s => s.Slot == slot); document.Slots.Add(value); document.Slots.Sort((a, b) => a.Slot.CompareTo(b.Slot));
    }
    public static void Clear(PcrClanDailyPlanDocument document, int slot)
    { Validate(document); if (slot is < 1 or > 3) throw new ArgumentOutOfRangeException(nameof(slot)); document.Slots.RemoveAll(s => s.Slot == slot); }
    public static PcrClanDailyPlanReview Review(PcrClanDailyPlanDocument document, string guideStatePath, string configDirectory, string repository, DateTimeOffset now, bool requireThree = false)
        => Review(document, guideStatePath, configDirectory, repository, now, requireThree, out _);
    internal static PcrClanDailyPlanReview Review(PcrClanDailyPlanDocument document, string guideStatePath, string configDirectory, string repository, DateTimeOffset now,
        bool requireThree, out List<(int Slot, PcrClanGuideDocument Document, JObject Plan)> plans)
    {
        Validate(document); var inputs = new PcrClanGuide.PlanInputs();
        var lines = Enumerable.Range(1, 3).Select(n => $"第 {n} 刀：尚未设置。").ToArray(); var warnings = new List<string>();
        var saved = PcrClanGuide.Read(Local(guideStatePath), out var before); plans = new();
        foreach (var slot in document.Slots.OrderBy(s => s.Slot))
        {
            try
            {
                var value = Resolve(slot, saved, configDirectory, repository, now, inputs); plans.Add((slot.Slot, value.Document, value.Plan));
                var plan = value.Plan;
                lines[slot.Slot - 1] = $"第 {slot.Slot} 刀：{plan["period"]} · {plan["phase"]} · BOSS {plan["boss"]} · {plan["package"]} / {plan["team_label"]}。";
            }
            catch (Exception ex) when (ex is IOException or InvalidDataException or InvalidOperationException or ArgumentException or JsonException)
            { lines[slot.Slot - 1] = $"第 {slot.Slot} 刀：待重新核对。{ex.Message}"; }
        }
        var consistent = plans.Select(p => $"{p.Plan["server"]}\n{p.Plan["account_id"]}\n{p.Plan["period"]}").Distinct(StringComparer.Ordinal).Count() <= 1;
        if (!consistent) warnings.Add("三刀的服务器、账号和会战期次必须一致，请重新设定。");
        var own = new Dictionary<int, List<int>>();
        foreach (var row in plans)
        {
            var mode = (string)row.Plan["support_mode"]!;
            var owned = mode == "qsjs" ? ((JArray)PcrClanGuide.Snapshot(row.Document, configDirectory, repository, now, inputs, out _)["units"]!)
                .Select(u => (int)u["unit_id"]!).ToHashSet() : null;
            var ids = ((JArray)row.Plan["unit_ids"]!).Values<int>().ToArray();
            foreach (var id in ids.Where(id => mode != "specified" || id != (int)row.Plan["support_unit_id"]!))
            {
                if (mode == "qsjs" && !owned!.Contains(id)) continue;
                if (!own.TryGetValue(id, out var slots)) own[id] = slots = new();
                slots.Add(row.Slot);
            }
        }
        foreach (var pair in own.Where(p => p.Value.Count > 1).OrderBy(p => p.Key))
            warnings.Add($"自有角色 {pair.Key} 在第 {string.Join("、", pair.Value)} 刀重复。若按主刀执行会占用同一角色，请自行更换已核对作业；程序不会替换角色。");
        inputs.Verify();
        if (PcrClanGuide.Stamp(guideStatePath) != before) throw new IOException("原作业在核对时发生变化，请重新读取。");
        var complete = document.Slots.Count == 3 && plans.Count == 3 && consistent && !own.Any(p => p.Value.Count > 1);
        if (requireThree && !complete) throw new InvalidDataException("完整三刀摘要需要恰好 3 队；原件、账号缓存和确认仍须有效，服务器、账号与期次一致，主刀自有角色不能重复。");
        return new(complete, lines, warnings);
    }
    public static string Save(PcrClanDailyPlanDocument document, string path, string? expectedHash)
    {
        Validate(document); path = Local(path); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        using var gate = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        if (PcrClanGuide.Stamp(path) != expectedHash) throw new IOException("三刀预设已被其他窗口修改，请重新读取；原文件未覆盖。");
        var json = JsonConvert.SerializeObject(document, Formatting.Indented); JsonHelper.SaveJsonTextAtomicallyOrThrow(path, json);
        var expected = PcrClanGuide.Hash(Encoding.UTF8.GetBytes(json));
        if (PcrClanGuide.Stamp(path) != expected) throw new IOException("三刀预设写后核对失败，请保留文件并重新读取。");
        return expected;
    }
}
