using System;
using System.IO;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// First executable slice: one explicit team and stage, using the normal Start owner.
public static class PcrGuideTrial
{
    public static Dictionary<int, bool?>? SuggestedSets(PcrGuideInput input)
    {
        var ids = input.Conditions.Where(c => c.UnitId.HasValue).Select(c => c.UnitId!.Value).Distinct().ToArray();
        if (ids.Length != 5) return null;
        return ids.ToDictionary(id => id, id =>
        {
            var values = input.Conditions.Where(c => c.UnitId == id && c.Field is "SET" or "SET标记")
                .Select(c => c.Expected.Trim().ToUpperInvariant() switch { "O" or "○" or "⚪" or "开启" => (bool?)true, "X" or "×" or "关闭" => false, _ => null }).Distinct().ToArray();
            return values.Length == 1 ? values[0] : null;
        });
    }
    public static JObject CreatePlan(PcrGuideInput input) => CreatePlan(input, null);
    public static JObject CreatePlan(PcrGuideInput input, string? repository)
    {
        var report = PcrGuideReview.Check(input, DateTimeOffset.Now);
        if (input.Identity?.Executable() != true || input.RequiresImageReview && input.Identity.ImportedSource == null
            || input.TargetServer != "国服 B 站" || !PcrGuideIdentity.Supports(input.TargetStage)
            || input.Snapshot == null || !input.SnapshotConfirmed
            || input.Identity?.Matches(input) != true)
            throw new InvalidOperationException("请先保存所选关卡五人的有效检查报告，并确认账号资料。参考差异可以保留。");
        var plan = new JObject { ["version"] = 2, ["server"] = "cn-bilibili", ["stage"] = input.TargetStage,
            ["account_id"] = input.Snapshot.AccountId, ["unit_ids"] = new JArray(PcrGuideIdentity.Team),
            ["stars"] = new JArray(5, 5, 5, 5, 6), ["all_set"] = true, ["auto"] = true,
            ["max_attempts"] = 3, ["source_hash"] = PcrGuideReview.Fingerprint(input), ["source_image_sha256"] = input.Identity.ImageSha256 };
        if (input.OpeningSets != null)
        {
            var ids = input.Identity.Members.Select(m => m.UnitId).ToArray();
            if (!input.OpeningConfirmed || input.OpeningSets.Values.Any(v => !v.HasValue)
                || !input.OpeningSets.Keys.Order().SequenceEqual(ids.Order())) throw new InvalidOperationException("请逐人确认开场 SET；AUTO 必须保持开启。");
            var stars = ids.Select(id => input.Facts.Where(f => f.UnitId == id && f.Field == "星级" && f.Confirmed).ToArray()).ToArray();
            if (stars.Any(f => f.Length != 1 || !int.TryParse(f[0].Value, out var value) || value is < 1 or > 6))
                throw new InvalidOperationException("请先读取五人当前战斗星级；参考星级不能替代账号实值。");
            plan["version"] = 3; plan["unit_ids"] = new JArray(ids);
            plan["stars"] = new JArray(stars.Select(f => int.Parse(f[0].Value)));
            plan.Remove("all_set");
            plan["set_by_unit"] = new JObject(ids.Select(id => new JProperty(id.ToString(System.Globalization.CultureInfo.InvariantCulture), input.OpeningSets[id]!.Value)));
            plan["source_material_sha256"] = PcrGuideIdentity.MaterialHash(input);
            plan["source_confirmations"] = new JArray(input.Identity.Members.Where(input.Identity.NeedsConfirmation).Select(m => new JObject
            { ["unit_id"]=m.UnitId,["icon_unit_id"]=m.IconUnitId,["binding"]=m.ConfirmedFor }));
        }
        else if (!input.Identity.Members.Select(m => m.UnitId).SequenceEqual(PcrGuideIdentity.Team)
            || input.TargetStage is "红焰深域 8-3" or "红焰深域 8-4" or "红焰深域 8-5" or "红焰深域 8-6" or "红焰深域 8-8")
            throw new InvalidOperationException("此队伍需要逐人确认开场 SET。");
        if (!report.Summary.StartsWith("可以试打", StringComparison.Ordinal) && !HasProgressObservation(repository, plan))
            throw new InvalidOperationException("请先保存所选关卡五人的有效检查报告，并确认账号资料。参考差异可以保留。");
        return plan;
    }

    private static bool HasProgressObservation(string? repository, JObject plan)
    {
        if (repository == null) return false;
        var directory = Path.Combine(repository, ".runtime", "guide-trial-work", "ledger");
        var ledger = Path.Combine(directory, "transactions.jsonl");
        if (!File.Exists(ledger) || new FileInfo(ledger).Length > 16_000_000) return false;
        var bytes = File.ReadAllBytes(ledger);
        var lines = System.Text.Encoding.UTF8.GetString(bytes).Split('\n', StringSplitOptions.RemoveEmptyEntries);
        if (lines.Length % 2 != 1) return false;
        var intent = JObject.Parse(lines[^1]);
        var id = (string?)intent["intent_id"];
        if ((string?)intent["state"] != "in_flight" || (string?)intent["operation"] != "guide_trial"
            || !Guid.TryParseExact(id, "N", out _)
            || new[] {"account_id", "stage", "source_hash"}.Any(key => !JToken.DeepEquals(intent[key], plan[key]))) return false;
        var request = Path.Combine(directory, $"progress-observation-{id}.json");
        if (!File.Exists(request) || File.Exists(Path.Combine(directory, $"progress-observation-{id}-claimed.json"))) return false;
        if (new FileInfo(request).Length > 16384) throw new InvalidDataException("进度观察请求过大。");
        var value = JObject.Parse(File.ReadAllText(request), new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        // The Python owner rechecks and claims this exact pending intent before any navigation.
        return (int?)value["version"] == 1 && (string?)value["intent_id"] == id
            && JToken.DeepEquals(value["plan"], plan)
            && (string?)value["ledger_sha256"] == Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant()
            && (string?)value["confirmation"]?["source"] == "direct_user_message"
            && value["confirmation"]?["navigation_only"]?.Type == JTokenType.Boolean
            && (bool)value["confirmation"]!["navigation_only"]!;
    }
}
