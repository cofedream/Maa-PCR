using System;
using System.IO;
using System.Linq;
using System.Text;
using MFAAvalonia.Helper;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed class PcrClanDailyExecutionDocument
{
    [JsonProperty("version")] public int Version { get; set; } = 1;
    [JsonProperty("mode")] public string Mode { get; set; } = PcrClanDailyExecution.Simulation;
    [JsonProperty("confirmed_for")] public string? ConfirmedFor { get; set; }
}

// Configuration and review only. Formal game input still has no calibrated production adapter.
public static class PcrClanDailyExecution
{
    public const string Simulation = "simulation";
    public const string FormalDaily = "formal_daily";
    public static string StatePath => PathFor(AppPaths.ConfigDirectory);
    public static string PathFor(string configDirectory) => Path.Combine(configDirectory, "clan-battle-execution.json");
    public static PcrClanDailyExecutionDocument Read(string path, out string? stamp)
    {
        stamp = null;
        if (!File.Exists(path)) return new();
        var before = PcrClanGuide.Stamp(path); var value = PcrClanGuide.ReadJson(path);
        if (PcrClanGuide.Stamp(path) != before) throw new IOException("会战模式在读取时变化，请重新打开。");
        if (value.Count != 3 || value.Properties().Any(p => p.Name is not ("version" or "mode" or "confirmed_for"))
            || value["version"]?.Type != JTokenType.Integer || (int?)value["version"] != 1 || value["mode"]?.Type != JTokenType.String
            || value["confirmed_for"]?.Type is not (JTokenType.Null or JTokenType.String)) throw new InvalidDataException("会战模式配置格式无效，请保留原文件。");
        var document = value.ToObject<PcrClanDailyExecutionDocument>()!; Validate(document); stamp = before; return document;
    }
    public static string ModeFor(string configDirectory) => Read(PathFor(configDirectory), out _).Mode;
    private static void Validate(PcrClanDailyExecutionDocument document)
    {
        if (document.Version != 1 || document.Mode is not (Simulation or FormalDaily)
            || document.ConfirmedFor != null && (document.Mode != FormalDaily || document.ConfirmedFor.Length != 64 || document.ConfirmedFor.Any(c => !"0123456789abcdef".Contains(c))))
            throw new InvalidDataException("会战模式或三目标确认无效。");
    }
    public static string Save(PcrClanDailyExecutionDocument document, string path, string? expectedHash)
    {
        Validate(document); Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        using var gate = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        if (PcrClanGuide.Stamp(path) != expectedHash) throw new IOException("会战模式已被其他窗口修改，请重新打开；原文件未覆盖。");
        var json = JsonConvert.SerializeObject(document, Formatting.Indented); JsonHelper.SaveJsonTextAtomicallyOrThrow(path, json);
        var expected = PcrClanGuide.Hash(Encoding.UTF8.GetBytes(json));
        if (PcrClanGuide.Stamp(path) != expected) throw new IOException("会战模式写后核对失败，请重新打开。");
        return expected;
    }
    public static JObject BuildPlan(string dailyPath, string guidePath, string configDirectory, string repository, DateTimeOffset now)
    {
        var daily = PcrClanDailyPlan.Read(dailyPath, out var dailyHash);
        _ = PcrClanDailyPlan.Review(daily, guidePath, configDirectory, repository, now, true, out var reviewed);
        var plans = new JObject(); var materials = new JObject();
        foreach (var slot in reviewed)
        {
            var key = slot.Slot.ToString(System.Globalization.CultureInfo.InvariantCulture);
            plans[key] = slot.Plan;
            materials[key] = slot.Document.Job.DeepClone();
        }
        if (PcrClanGuide.Stamp(dailyPath) != dailyHash)
            throw new IOException("三目标预设在核对时变化，本次未生成请求。");
        var request = new JObject { ["version"] = 1, ["daily_plan"] = JObject.FromObject(daily), ["slot_plans"] = plans, ["slot_materials"] = materials };
        request["confirmed_for"] = PcrClanGuide.Fingerprint(request); return request;
    }
    public static JObject CreatePlan(string configDirectory, string repository, DateTimeOffset now)
    {
        var statePath = PathFor(configDirectory); var state = Read(statePath, out var before);
        if (state.Mode != FormalDaily || state.ConfirmedFor == null) throw new InvalidOperationException("请单独核对并保存正式三目标，单队确认不能代替三目标确认。");
        var request = BuildPlan(PcrClanDailyPlan.PathFor(configDirectory), Path.Combine(configDirectory, "agent-guide-review.json"), configDirectory, repository, now);
        if (state.ConfirmedFor != (string?)request["confirmed_for"] || PcrClanGuide.Stamp(statePath) != before)
            throw new InvalidOperationException("正式三目标或模式已变化，请重新预览并核对保存。");
        return request;
    }
    public static void RequireStartAllowed(string mode)
    {
        if (mode == FormalDaily) throw new InvalidOperationException("正式三刀尚未完成开放后的真实页面与次数、补偿校准；本轮仅能保存和预览，不会创建 Owner、连接设备或出刀。[production_daily_adapter_unavailable]");
        if (mode != Simulation) throw new InvalidDataException("未知会战执行模式。");
    }
    public static void RequireSelectionAllowed(JObject selection)
    { if (selection["selected_modules"]!.Values<string>().Contains("clan_battle_daily")) RequireStartAllowed(FormalDaily); }
}
