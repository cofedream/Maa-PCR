using System;
using System.Collections.Generic;
using System.IO;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed record PcrClanCalibration(int Version, IReadOnlySet<string> Capabilities, string? Error, bool OwnUnitsOnly = false, bool AccountStars = false, IReadOnlySet<int>? AutoBattleSpeeds = null, bool ManageInheritance = false, bool FormalOpening = false)
{
    public const string IndexPath = "assets/resource/clan-battle-calibrations/index.json";
    public const string IndexSha256 = PcrClanCalibrationPin.IndexSha256;
    private static readonly string[] Names = { "simulation", "timed", "formal", "carryover" };
    private static readonly string[] Labels = { "模拟战", "秒数轴", "正式三刀", "补偿刀" };
    private string? SimulationError { get; init; }
    private string? FormalResultError { get; init; }
    private sealed record TargetScope(Dictionary<(string Period, string Phase, int Boss), IReadOnlySet<int>?> Targets, string? Error = null)
    {
        public string Description => string.Join("、", Targets
            .OrderBy(t => t.Key.Period, StringComparer.Ordinal).ThenBy(t => t.Key.Phase, StringComparer.Ordinal).ThenBy(t => t.Key.Boss)
            .Select(t => $"{t.Key.Period} {t.Key.Phase}{t.Key.Boss}" + (t.Value == null ? "" : $"（{string.Join("／", t.Value.Order())} 倍速）")));
    }
    private static readonly string[] TargetCapabilities = { "simulation", "formal", "carryover" };
    private static string TargetLabel(string capability) => capability switch { "simulation" => "模拟", "formal" => "正式", _ => "补偿" };
    private IReadOnlyDictionary<string, TargetScope> TargetScopes { get; init; } = new Dictionary<string, TargetScope>();
    internal string? UnavailableReason(string capability) => Error ?? TargetScopes.GetValueOrDefault(capability)?.Error ?? (capability switch
    {
        "simulation" => SimulationError,
        "formal" or "carryover" => FormalResultError,
        _ => null
    });
    public bool Supports(string capability) => Capabilities.Contains(capability) && UnavailableReason(capability) == null;
    public string Summary => (Error != null ? string.Join(" / ", Labels) + "：不可用。" + Error
        : string.Join("；", Names.Select((name, i) => Labels[i] + "：" + (Supports(name)
            ? name == "timed" ? "测量资料齐全，开始前核对时限" : Version == 1 && name == "simulation" ? "校准资料已登记（仅全程 AUTO、无切换）" : "校准资料已登记" : UnavailableReason(name) ?? "缺少校准"))))
        + (Error == null ? "。资料校验通过不代表当前阶段已通过正常 Start 全程验收。" : "")
        + string.Concat(TargetScopes.Where(s => Supports(s.Key)).Select(s => $" 可{TargetLabel(s.Key)}目标为 {s.Value.Description}。"))
        + (OwnUnitsOnly ? "仅全自有队伍可用；借人身份尚未校准。" : "")
        + (AccountStars ? " 出战星级使用24小时内的已确认账号资料，调整后须重新同步。" : "");
    public string AutoBattleSummary => AutoBattleSpeeds == null ? "开场设置与倍速：缺少校准。"
        : "保留当前战斗继承，进战读取 SET／AUTO／速度，只调整与作业不同的项。"
            + "正常流程不例行进入设置页检查继承。"
            + "普通挑战和模拟需校正时，须确认仍在受影响角色首次 UB 前；无法确认或已错过时停止。无战中切换时支持 "
            + string.Join("／", AutoBattleSpeeds.Order()) + " 倍速。";

    public static PcrClanCalibration Read(string repository, string? contentVersion) => Read(repository, contentVersion, IndexSha256);

    internal static PcrClanCalibration Read(string repository, string? contentVersion, string expectedIndexSha256)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(repository)) throw new InvalidDataException("程序校准目录未就绪");
            var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(repository));
            var index = Json(ReadVerified(root, IndexPath, expectedIndexSha256, 1024 * 1024));
            if (!Keys(index, "version", "entries") || index["version"]?.Type != JTokenType.Integer
                || (int?)index["version"] is not (1 or 2) || index["entries"] is not JArray entries || entries.Count > 100)
                throw new InvalidDataException("校准索引格式无效");
            var version = (int)index["version"]!;
            var identities = new HashSet<string>(StringComparer.Ordinal);
            JObject? selected = null;
            foreach (var token in entries)
            {
                var fields = new[] { "calibration_id", "server", "content_version", "frame_size", "profile_path", "profile_sha256" };
                if (token is not JObject entry || !Keys(entry, version == 2 ? fields.Append("capabilities").ToArray() : fields)
                    || entry["calibration_id"]?.Type != JTokenType.String || string.IsNullOrEmpty((string?)entry["calibration_id"])
                    || entry["server"]?.Type != JTokenType.String || (string?)entry["server"] != "cn-bilibili"
                    || entry["content_version"]?.Type != JTokenType.String || !Regex.IsMatch((string)entry["content_version"]!, "\\A[0-9]{12}\\z")
                    || entry["frame_size"] is not JArray size || size.Count != 2 || size.Any(v => v.Type != JTokenType.Integer)
                    || (int?)size[0] != 1280 || (int?)size[1] != 720 || entry["profile_path"]?.Type != JTokenType.String
                    || entry["profile_sha256"]?.Type != JTokenType.String || !Digest((string)entry["profile_sha256"]!))
                    throw new InvalidDataException("校准条目格式无效");
                _ = Local(root, (string)entry["profile_path"]!);
                if (!identities.Add((string)entry["content_version"]!)) throw new InvalidDataException("同一内容版本存在重复校准");
                if (version == 2)
                {
                    if (entry["capabilities"] is not JArray caps || caps.Any(v => v.Type != JTokenType.String || !Names.Contains((string)v))
                        || caps.Values<string>().Distinct(StringComparer.Ordinal).Count() != caps.Count
                        || caps.Values<string>().Contains("carryover") && !caps.Values<string>().Contains("formal"))
                        throw new InvalidDataException("校准能力格式无效");
                }
                if ((string?)entry["content_version"] == contentVersion) selected = entry;
            }
            if (contentVersion == null) throw new InvalidDataException("请先选用有效账号资料");
            if (selected == null) throw new InvalidDataException("当前内容版本缺少随包校准。[clan_battle_calibration_missing]");
            var profile = Json(ReadVerified(root, (string)selected["profile_path"]!, (string)selected["profile_sha256"]!, 4 * 1024 * 1024));
            var capabilities = (version == 1 ? new[] { "simulation" } : selected["capabilities"]!.Values<string>()).ToHashSet(StringComparer.Ordinal);
            if (profile["version"]?.Type != JTokenType.Integer || (int?)profile["version"] is not (1 or 2)
                || profile["evidence_kind"]?.Type != JTokenType.String || (string?)profile["evidence_kind"] != "device_saved_frame"
                || (string?)profile["calibration_id"] != (string?)selected["calibration_id"] || (string?)profile["server"] != "cn-bilibili"
                || !JToken.DeepEquals(profile["frame_size"], selected["frame_size"])) throw new InvalidDataException("校准档案与索引不符");
            if ((int?)profile["version"] == 1 && capabilities.Any(cap => cap != "simulation"))
                throw new InvalidDataException("旧版模拟校准不能授予秒数轴、正式或补偿能力");
            var ownUnitsOnly = profile.ContainsKey("support_modes");
            if (ownUnitsOnly && ((int?)profile["version"] != 2 || !JToken.DeepEquals(profile["support_modes"], new JArray("none"))))
                throw new InvalidDataException("校准支援范围无效。[clan_battle_calibration_support_scope_invalid]");
            var starSource = profile["formation"]?["star_source"];
            if (starSource != null && ((int?)profile["version"] != 2 || !ownUnitsOnly || (string?)starSource != "account_snapshot"))
                throw new InvalidDataException("星级资料来源无效。[clan_battle_calibration_star_source_invalid]");
            var scopes = ReadTargetScopes(profile);
            var execution = (profile["daily"] as JObject)?["execution"] as JObject;
            var formalReady = !capabilities.Contains("formal") || HasDamageLine(profile, contentVersion, execution, formal: true);
            // The binding is data; only the referenced result sample is a file dependency.
            if (execution?["pages"] is JObject formalPages && formalPages["result"] is JObject formalResult)
                formalResult.Remove("damage_line");
            VerifyDependencies(root, profile, capabilities, ownUnitsOnly);
            return new(version == 1 ? 1 : (int)profile["version"]!, capabilities, null, ownUnitsOnly, starSource != null,
                profile["auto_battle"] is JObject autoBattle ? autoBattle["speeds"]!.Values<int>().ToHashSet() : null,
                profile["auto_battle"]?["inheritance"] is JObject, profile["daily"]?["execution"]?["opening"] is JObject)
            {
                TargetScopes = scopes,
                FormalResultError = formalReady ? null : "正式结算缺少已核验的伤害行和正式结果截图。[clan_battle_result_damage_uncalibrated]",
                SimulationError = capabilities.Contains("simulation") && (int)profile["version"]! == 2 && !HasDamageLine(profile, contentVersion, profile, formal: false)
                    ? "模拟战结算伤害行缺少已核验的截图或识别范围。[clan_battle_result_damage_uncalibrated]" : null
            };
        }
        catch (Exception ex) when (ex is IOException or InvalidDataException or UnauthorizedAccessException or ArgumentException
            or InvalidCastException or FormatException or OverflowException or Newtonsoft.Json.JsonException)
        { return new(0, new HashSet<string>(StringComparer.Ordinal), ex.Message); }
    }

    private static Dictionary<string, TargetScope> ReadTargetScopes(JObject profile)
    {
        var scopes = new Dictionary<string, TargetScope>(StringComparer.Ordinal);
        foreach (var capability in TargetCapabilities)
        {
            var field = capability + "_targets";
            if (!profile.TryGetValue(field, out var value)) continue;
            scopes[capability] = ReadTargetScope(profile, capability, value);
            // A scope declares data only. Even a malformed entry cannot add a file dependency.
            profile.Remove(field);
        }
        return scopes;
    }

    private static TargetScope ReadTargetScope(JObject profile, string capability, JToken value)
    {
        var invalid = new TargetScope(new(), $"{TargetLabel(capability)}目标清单格式无效。[clan_battle_{capability}_targets_invalid]");
        if ((int?)profile["version"] != 2 || value is not JArray { Count: >= 1 and <= 100 } rows) return invalid;
        var simulation = capability == "simulation";
        var choices = ((profile["actions"] as JObject)?["select_difficulty"] as JObject)?["choices"] as JObject;
        var phaseLabels = profile["phase_labels"] as JObject;
        var declaredSpeeds = (profile["auto_battle"] as JObject)?["speeds"] as JArray;
        var targets = new Dictionary<(string Period, string Phase, int Boss), IReadOnlySet<int>?>();
        foreach (var token in rows)
        {
            if (token is not JObject row || !(Keys(row, "period", "phase", "boss") || simulation && Keys(row, "period", "phase", "boss", "speeds"))
                || row["period"] is not JValue { Value: string period } || !Regex.IsMatch(period, @"\A[0-9]{4}-(?:0[1-9]|1[0-2])\z")
                || row["phase"] is not JValue { Value: string phase } || phase is not ("B" or "C" or "D" or "E")
                || !TryBoss(row["boss"], out var boss)) return invalid;
            if (simulation ? choices?.ContainsKey(phase) != true
                : phaseLabels?.Properties().Any(p => p.Value is JValue { Value: string label } && label == phase) != true) return invalid;
            HashSet<int>? speeds = null;
            if (row.TryGetValue("speeds", out var requested))
            {
                if (requested is not JArray { Count: >= 1 and <= 3 } values || declaredSpeeds == null) return invalid;
                speeds = new HashSet<int>();
                foreach (var speed in values)
                    if (!TryInteger(speed, out var n) || n is not (1 or 2 or 4)
                        || !declaredSpeeds.Any(s => JToken.DeepEquals(s, speed)) || !speeds.Add(n)) return invalid;
            }
            if (!targets.TryAdd((period, phase, boss), speeds)) return invalid;
        }
        return new TargetScope(targets);
    }

    private static bool TryInteger(JToken? token, out int number)
    {
        number = 0;
        return token?.Type == JTokenType.Integer && int.TryParse(token.ToString(), NumberStyles.None, CultureInfo.InvariantCulture, out number);
    }

    private static bool TryBoss(JToken? token, out int boss) => TryInteger(token, out boss) && boss is >= 1 and <= 5;

    internal (string? Target, string? Error) CheckTarget(string capability, JToken? periodValue, JToken? phaseValue, JToken? bossValue, int battleSpeed)
    {
        if (!TargetScopes.TryGetValue(capability, out var scope) || scope.Error != null) return (null, scope?.Error);
        if (periodValue is JValue { Value: string period } && phaseValue is JValue { Value: string phase }
            && TryBoss(bossValue, out var boss))
        {
            // Shared B/C guides use the first calibrated phase in B, C order.
            foreach (var candidate in phase == "BC" ? new[] { "B", "C" } : new[] { phase })
                if (scope.Targets.TryGetValue((period, candidate, boss), out var speeds))
                {
                    var target = $"{period} {candidate}{boss}";
                    if (speeds != null && !speeds.Contains(battleSpeed))
                        return (target, $"{target} 可用 {string.Join("／", speeds.Order())} 倍速。[clan_battle_auto_speed_uncalibrated]");
                    return (target, null);
                }
        }
        return (null, $"当前作业与可{TargetLabel(capability)}目标不符。可选目标为 {scope.Description}。[clan_battle_{capability}_target_uncalibrated]");
    }

    public void Require(JObject plan, string capability, int battleSpeed = 1)
    {
        if (!Names.Contains(capability)) throw new InvalidDataException("未知会战校准能力。");
        if (!Supports(capability)) throw new InvalidOperationException((UnavailableReason(capability) ?? "缺少" + Labels[Array.IndexOf(Names, capability)] + "校准")
            + " [clan_battle_calibration_capability_missing:" + capability + "]");
        if (CheckTarget(capability, plan["period"], plan["phase"], plan["boss"], battleSpeed).Error is string targetError)
            throw new InvalidOperationException(targetError);
        if (Version == 1 && ((bool?)plan["auto"] != true || plan["switches"] is not JArray { Count: 0 }))
            throw new InvalidOperationException("此校准只支持全程 AUTO、无战中切换。[clan_battle_calibration_auto_only]");
        JObject? supportSnapshot = null;
        var ownUnits = !OwnUnitsOnly && capability is not ("formal" or "carryover") || UsesOwnUnits(plan, out supportSnapshot);
        if (capability is "formal" or "carryover")
        {
            if (!ownUnits)
                throw new InvalidOperationException("正式作业只支持全自有队伍。[clan_battle_support_mode_uncalibrated]");
            if (plan["switches"] is not JArray { Count: 0 })
                throw new InvalidOperationException("正式作业暂不支持战中切换。[clan_battle_daily_auto_only]");
            if (battleSpeed != 2)
                throw new InvalidOperationException("正式作业固定使用已校准的 2 倍速。[clan_battle_daily_speed_invalid]");
            if (!FormalOpening)
                throw new InvalidOperationException("正式开场及继承校准缺失。[clan_battle_daily_opening_calibration_missing]");
        }
        if (plan["switches"] is JArray { Count: > 0 } && !Supports("timed"))
            throw new InvalidOperationException("当前内容版本缺少秒数轴校准。[clan_battle_calibration_capability_missing:timed]");
        if (OwnUnitsOnly && !ownUnits)
            throw new InvalidOperationException("仅全自有队伍可用；借人身份尚未校准。[clan_battle_support_mode_uncalibrated]");
        if (battleSpeed is not (1 or 2 or 4)) throw new InvalidOperationException("会战倍速无效。[clan_battle_speed_invalid]");
        if (capability is not ("formal" or "carryover") && battleSpeed != 1 && (capability != "simulation" || plan["switches"] is not JArray { Count: 0 }
                || AutoBattleSpeeds?.Contains(battleSpeed) != true))
            throw new InvalidOperationException("此作业或内容版本尚未校准所选倍速；秒数轴只支持 1 倍速。[clan_battle_auto_speed_uncalibrated]");
        if (AccountStars) RequireAccountStars(plan, supportSnapshot);
    }

    internal static bool UsesOwnUnits(JObject plan) => UsesOwnUnits(plan, out _);
    private static bool UsesOwnUnits(JObject plan, out JObject? snapshot)
    {
        snapshot = null;
        if ((string?)plan["support_mode"] == "none") return true;
        if ((string?)plan["support_mode"] != "qsjs" || plan["snapshot"] is not JObject) return false;
        snapshot = ReadAccountSnapshot(plan);
        return !PcrClanGuide.SupportRequirement(plan, snapshot).HasValue;
    }
    private static JObject ReadAccountSnapshot(JObject plan)
    {
        if (plan["snapshot"] is not JObject binding || !Keys(binding, "path", "sha256")
            || binding["path"]?.Type != JTokenType.String || binding["sha256"]?.Type != JTokenType.String || !Digest((string)binding["sha256"]!))
            throw new InvalidDataException("账号资料来源无效。[clan_battle_snapshot_invalid]");
        var path = (string)binding["path"]!;
        if (!Path.IsPathFullyQualified(path) || path.StartsWith(@"\\") || path.StartsWith("//"))
            throw new InvalidDataException("账号资料来源无效。[clan_battle_snapshot_invalid]");
        var info = new FileInfo(path);
        if (!info.Exists || info.Length is < 1 or > 16 * 1024 * 1024)
            throw new InvalidDataException("账号资料缺失。[clan_battle_source_file_missing]");
        var bytes = File.ReadAllBytes(path);
        if (PcrClanGuide.Hash(bytes) != (string?)binding["sha256"])
            throw new InvalidDataException("账号资料已变化，请重新核对保存。[clan_battle_source_changed]");
        if (bytes.Length is < 1 or > 16 * 1024 * 1024) throw new InvalidDataException("账号资料过大。[clan_battle_snapshot_invalid]");
        var snapshot = Json(bytes);
        if ((int?)snapshot["schema_version"] != 1 || new[] { "server", "account_id", "content_version" }
                .Any(key => !JToken.DeepEquals(snapshot[key], plan[key])))
            throw new InvalidDataException("账号资料与作业不一致。[clan_battle_snapshot_identity_mismatch]");
        var observedText = (string?)snapshot["observed_at"];
        if (observedText == null || !Regex.IsMatch(observedText, "(?:Z|[+-][0-9]{2}:[0-9]{2})$")
            || !DateTimeOffset.TryParse(observedText, CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var observed) || (DateTimeOffset.UtcNow - observed).TotalHours is < 0 or > 24)
            throw new InvalidOperationException("账号资料超过24小时或时间异常，请重新读取账号资料并保存作业。[clan_battle_snapshot_expired]");
        if (snapshot["unknown_fields"] is not JArray unknown || unknown.Any(field => field.Type != JTokenType.String)
            || snapshot["units"] is not JArray units || units.Count > 2000
            || units.Any(row => row is not JObject || row["unit_id"]?.Type != JTokenType.Integer
                || !long.TryParse(row["unit_id"]!.ToString(), out var id) || id is < 1 or > 100000000)
            || units.Select(row => (int)row["unit_id"]!).Distinct().Count() != units.Count)
            throw new InvalidDataException("账号持有资料无效。[clan_battle_snapshot_invalid]");
        return snapshot;
    }
    private static void RequireAccountStars(JObject plan, JObject? snapshot = null)
    {
        snapshot ??= ReadAccountSnapshot(plan);
        if ((string?)plan["support_mode"] != "none" && ((string?)plan["support_mode"] != "qsjs" || PcrClanGuide.SupportRequirement(plan, snapshot).HasValue))
            throw new InvalidOperationException("账号星级资料不包含支援校验。[clan_battle_account_stars_support_uncalibrated]");
        PcrClanGuide.RequireAccountStars(plan, snapshot);
    }

    private static IEnumerable<JToken> SetResetMarkers(JObject inheritance)
    {
        if (!inheritance.ContainsKey("ub_auto_clear")) return Array.Empty<JToken>();
        if (inheritance["ub_auto_clear"] is not JObject reset || !Keys(reset, "description", "on_label", "off_label")
            || reset.Properties().Any(property => property.Value is not JObject marker
                || (string?)marker["recognition"] != "OCR" || !OpeningNode(marker)))
            throw new InvalidDataException("UB 后解除 SET 的只读校准无效。[clan_battle_set_reset_calibration_invalid]");
        return reset.Properties().Select(property => property.Value);
    }

    private static bool Keys(JObject value, params string[] names) => value.Properties().Select(p => p.Name).ToHashSet(StringComparer.Ordinal).SetEquals(names);
    private static bool HasDamageLine(JObject profile, string? contentVersion, JObject? section, bool formal)
    {
        if (section?["pages"] is not JObject pages || pages["result"] is not JObject result
            || result["fields"] is not JObject fields || fields["damage"] is not JArray main || !Roi(main)) return false;
        if (!result.ContainsKey("damage_line"))
            return contentVersion == "202610141448" && (string?)profile["calibration_id"] == "cn-bilibili-202610141448-clan-simulation"
                && JToken.DeepEquals(main, new JArray(88, 47, 162, 38));
        if (result["damage_line"] is not JObject line || !Keys(line, "sample_sha256", "verify_roi")
            || line["sample_sha256"]?.Type != JTokenType.String || line["verify_roi"] is not JArray verify
            || !Roi(verify) || JToken.DeepEquals(main, verify)) return false;
        if (formal && profile["samples"] is JArray simulationSamples && simulationSamples.OfType<JObject>().Any(sample =>
            (string?)sample["page"] == "result" && JToken.DeepEquals(sample["sha256"], line["sample_sha256"]))) return false;
        return (int)verify[0] >= (int)main[0] && (int)verify[1] >= (int)main[1]
            && (int)verify[0] + (int)verify[2] <= (int)main[0] + (int)main[2]
            && (int)verify[1] + (int)verify[3] <= (int)main[1] + (int)main[3]
            && section["samples"] is JArray samples && samples.OfType<JObject>().Any(sample =>
                (string?)sample["page"] == "result" && JToken.DeepEquals(sample["sha256"], line["sample_sha256"]));
    }
    private static bool Digest(string value) => Regex.IsMatch(value, "\\A[0-9a-f]{64}\\z");
    private static JObject Json(byte[] bytes)
    {
        using var reader = new JsonTextReader(new StringReader(new UTF8Encoding(false, true).GetString(bytes))) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 };
        var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        if (reader.Read()) throw new InvalidDataException("JSON 后有多余内容。");
        return value;
    }
    private static void VerifyDependencies(string root, JObject profile, IReadOnlySet<string> capabilities, bool ownUnitsOnly)
    {
        if (profile["battle_hud"]?.Type != JTokenType.String || (string?)profile["battle_hud"] != "guide-trial-1280x720"
            || new[] { "pages", "actions", "formation" }.Any(key => profile[key] is not JObject)
            || !ownUnitsOnly && profile["support"] is not JObject)
            throw new InvalidDataException("校准档案缺少必填结构。[clan_battle_calibration_not_reviewed_device_profile]");
        var sections = new List<JObject> { profile };
        if (profile.ContainsKey("auto_battle"))
        {
            if ((int?)profile["version"] != 2 || profile["auto_battle"] is not JObject autoBattle
                || !Keys(autoBattle, new[] { "speeds", "opening_guards", "paused_guards", "auto_on", "auto_off", "speed", "set_badge", "samples" }
                    .Concat(autoBattle.ContainsKey("empty_tp") ? new[] { "empty_tp" } : Array.Empty<string>())
                    .Concat(autoBattle.ContainsKey("paused_boss_roi") ? new[] { "paused_boss_roi" } : Array.Empty<string>())
                    .Concat(autoBattle.ContainsKey("paused_clock_alternate_roi") ? new[] { "paused_clock_alternate_roi" } : Array.Empty<string>())
                    .Concat(autoBattle.ContainsKey("inheritance") ? new[] { "inheritance" } : Array.Empty<string>()).ToArray())
                || autoBattle.ContainsKey("paused_boss_roi") && !Roi(autoBattle["paused_boss_roi"])
                || autoBattle.ContainsKey("paused_clock_alternate_roi") && !Roi(autoBattle["paused_clock_alternate_roi"])
                || autoBattle["speeds"] is not JArray { Count: > 0 } speeds || !speeds.Any(v => v.Type == JTokenType.Integer && (int)v == 1)
                || speeds.Any(v => v.Type != JTokenType.Integer || (int)v is not (1 or 2 or 4)) || speeds.Values<int>().Distinct().Count() != speeds.Count
                || autoBattle["opening_guards"] is not JArray { Count: 3 } || autoBattle["paused_guards"] is not JArray { Count: 3 or 4 }
                || autoBattle["speed"] is not JObject speed || !Keys(speed, "1", "2", "4")
                || new[] { "auto_on", "auto_off", "set_badge" }.Any(key => autoBattle[key] is not JObject)
                || autoBattle.ContainsKey("empty_tp") && autoBattle["empty_tp"] is not JObject)
                throw new InvalidDataException("开场设置校准无效。[clan_battle_auto_calibration_invalid]");
            if (autoBattle["paused_guards"]![2] is not JObject clock || (string?)clock["recognition"] is not ("OCR" or "TemplateMatch")
                || (string?)clock["recognition"] == "TemplateMatch"
                    && (clock["template"] is not JArray { Count: 2 } || clock["sha256"] is not JArray { Count: 2 }))
                throw new InvalidDataException("开场时钟校准无效。[clan_battle_auto_clock_calibration_invalid]");
            if (speeds.Values<int>().Contains(2) && profile["pages"]?["battle"]?["speed_two"] is not JObject
                || speeds.Values<int>().Contains(4) && profile["pages"]?["battle"]?["speed_four"] is not JObject)
                throw new InvalidDataException("所选倍速缺少战斗画面校准。[clan_battle_auto_speed_calibration_missing]");
            var inheritanceMarkers = Enumerable.Empty<JToken>();
            if (autoBattle.ContainsKey("inheritance"))
            {
                if (autoBattle["inheritance"] is not JObject inheritance || !Keys(inheritance, new[] { "battle_selected", "radio_on", "radio_off", "samples" }
                        .Concat(inheritance.ContainsKey("ub_auto_clear") ? new[] { "ub_auto_clear" } : Array.Empty<string>()).ToArray())
                    || new[] { "battle_selected", "radio_on", "radio_off" }.Any(key => inheritance[key] is not JObject node
                        || (string?)node["recognition"] != "TemplateMatch"))
                    throw new InvalidDataException("战斗继承校准无效。[clan_battle_inheritance_calibration_invalid]");
                inheritanceMarkers = new[] { inheritance["battle_selected"]!, inheritance["radio_on"]!, inheritance["radio_off"]! }.Concat(SetResetMarkers(inheritance));
                sections.Add(inheritance);
            }
            foreach (var marker in ((JArray)autoBattle["opening_guards"]!).Concat((JArray)autoBattle["paused_guards"]!)
                .Concat(speed.Properties().Select(p => p.Value)).Concat(new[] { autoBattle["auto_on"]!, autoBattle["auto_off"]!, autoBattle["set_badge"]! })
                .Concat(autoBattle.ContainsKey("empty_tp") ? new[] { autoBattle["empty_tp"]! } : Array.Empty<JToken>()).Concat(inheritanceMarkers))
            {
                if (marker is not JObject node || (string?)node["recognition"] is not ("OCR" or "TemplateMatch")
                    || node["roi"] is not JArray { Count: 4 } roi || roi.Any(v => v.Type != JTokenType.Integer)
                    || (int)roi[0] < 0 || (int)roi[1] < 0 || (int)roi[2] <= 0 || (int)roi[3] <= 0
                    || (long)roi[0] + (long)roi[2] > 1280 || (long)roi[1] + (long)roi[3] > 720
                    || (string?)node["recognition"] == "OCR" && (node["expected"]?.Type != JTokenType.String
                        || !((string)node["expected"]!).StartsWith('^') || !((string)node["expected"]!).EndsWith('$')))
                    throw new InvalidDataException("开场设置页面锚点无效。[clan_battle_auto_calibration_invalid]");
            }
            sections.Add(autoBattle);
        }
        if (profile.ContainsKey("daily"))
        {
            if (profile["daily"] is not JObject daily) throw new InvalidDataException("正式校准结构无效。[clan_battle_daily_calibration_invalid]");
            sections.Add(daily);
            if (daily.ContainsKey("execution"))
            {
                if (daily["execution"] is not JObject execution) throw new InvalidDataException("正式执行校准缺失。[clan_battle_daily_execution_calibration_missing]");
                sections.Add(execution);
                if (execution.ContainsKey("opening"))
                {
                    if (execution["opening"] is not JObject opening
                        || !Keys(opening, new[] { "battle_speed", "opening_guards", "paused_guards", "clock", "auto_on", "auto_off", "speed", "speed_two", "set_badge", "targets", "inheritance", "samples" }
                            .Concat(opening.ContainsKey("empty_tp") ? new[] { "empty_tp" } : Array.Empty<string>())
                            .Concat(opening.ContainsKey("start_seconds") ? new[] { "start_seconds" } : Array.Empty<string>()).ToArray())
                        || opening.ContainsKey("start_seconds") && (opening["start_seconds"] is not JArray { Count: > 0 } seconds
                            || seconds.Any(value => value.Type != JTokenType.Integer || (long)value is < 1 or > 90)
                            || seconds.Values<int>().Distinct().Count() != seconds.Count || !seconds.Values<int>().Contains(90))
                        || opening["battle_speed"]?.Type != JTokenType.Integer || (int?)opening["battle_speed"] != 2
                        || opening["opening_guards"] is not JArray { Count: 3 } || opening["paused_guards"] is not JArray { Count: 3 }
                        || !Roi(opening["clock"]) || opening["speed"] is not JObject speed || !Keys(speed, "1", "2")
                        || opening["targets"] is not JObject targets || !Keys(targets, "pause", "auto", "speed", "resume", "sets")
                        || new[] { "pause", "auto", "speed", "resume" }.Any(key => !Roi(targets[key]))
                        || targets["sets"] is not JArray { Count: 5 } setTargets || setTargets.Any(value => !Roi(value))
                        || opening["samples"] is not JArray { Count: 3 } openingSamples
                        || openingSamples.Any(value => value is not JObject sample || sample["page"]?.Type != JTokenType.String)
                        || !openingSamples.Select(value => (string?)value["page"]).ToHashSet().SetEquals(new[] { "battle", "paused", "battle_two" }))
                        throw new InvalidDataException("正式开场校准无效。[clan_battle_daily_opening_calibration_missing]");
                    if (opening["inheritance"] is not JObject inheritance || !Keys(inheritance, new[] { "battle_selected", "radio_on", "radio_off", "samples" }
                        .Concat(inheritance.ContainsKey("ub_auto_clear") ? new[] { "ub_auto_clear" } : Array.Empty<string>()).ToArray())
                        || new[] { "battle_selected", "radio_on", "radio_off" }.Any(key => inheritance[key] is not JObject marker
                            || (string?)marker["recognition"] != "TemplateMatch" || !OpeningNode(marker)))
                        throw new InvalidDataException("正式继承校准无效。[clan_battle_inheritance_calibration_invalid]");
                    foreach (var marker in ((JArray)opening["opening_guards"]!).Concat((JArray)opening["paused_guards"]!)
                        .Concat(speed.Properties().Select(value => value.Value))
                        .Concat(new[] { opening["auto_on"]!, opening["auto_off"]!, opening["speed_two"]!, opening["set_badge"]! })
                        .Concat(opening.ContainsKey("empty_tp") ? new[] { opening["empty_tp"]! } : Array.Empty<JToken>()).Concat(SetResetMarkers(inheritance)))
                        if (!OpeningNode(marker)) throw new InvalidDataException("正式开场锚点无效。[clan_battle_daily_opening_calibration_missing]");
                    sections.Add(opening); sections.Add(inheritance);
                }
            }
        }
        if (capabilities.Contains("formal") || capabilities.Contains("carryover"))
        {
            if (profile["daily"] is not JObject daily || daily["version"]?.Type != JTokenType.Integer || (int?)daily["version"] != 2
                || daily["execution"] is not JObject execution)
                throw new InvalidDataException("正式执行校准字段不完整。[clan_battle_daily_execution_calibration_missing]");
            if (execution["opening"] is not JObject)
                throw new InvalidDataException("正式开场及继承校准缺失。[clan_battle_daily_opening_calibration_missing]");
            var personal = execution.ContainsKey("used_units");
            var executionKeys = new[] { "pages", "actions", "carry_slots", "boss_slots", "formation_owner", "formation_mode", "speed_one", "opening", "samples" };
            var optional = new[] { "start_confirmation", "carry_start_confirmation", "result_reward" }.Where(execution.ContainsKey).ToArray();
            if (!Keys(execution, executionKeys.Concat(personal ? new[] { "used_units" } : Array.Empty<string>())
                .Concat(execution.ContainsKey("carry_selection") ? new[] { "carry_selection" } : Array.Empty<string>()).Concat(optional).ToArray()))
                throw new InvalidDataException("正式执行校准字段不完整。[clan_battle_daily_execution_calibration_missing]");
            if (execution["pages"] is not JObject recordPages || execution["actions"] is not JObject recordActions
                || execution["samples"] is not JArray recordSamples
                || personal != recordPages.ContainsKey("used_units") || personal != recordActions.ContainsKey("open_used")
                || personal != recordActions.ContainsKey("close_used")
                || personal != recordSamples.OfType<JObject>().Any(row => (string?)row["page"] == "used_units")
                || personal && (execution["used_units"] is not JObject || recordPages["used_units"] is not JObject recordPage
                    || recordPage["fields"] is not JObject recordFields || !Keys(recordFields, "game_day") || !Roi(recordFields["game_day"])))
                throw new InvalidDataException("个人记录校准必须整组保留或省略。[clan_battle_daily_personal_record_group_invalid]");
            VerifyDailyScope(daily, execution, ownUnitsOnly, capabilities.Contains("carryover"));
            foreach (var name in optional)
            {
                if (execution[name] is not JObject overlay || !Keys(overlay, "labels", "target", "samples")
                    || overlay["labels"] is not JArray labels || labels.Count != (name == "result_reward" ? 2 : 3)
                    || !labels.All(DailyNode) || !Roi(overlay["target"])
                    || overlay["samples"] is not JArray { Count: 1 } overlaySamples || overlaySamples[0] is not JObject sample
                    || sample["page"]?.Type != JTokenType.String || (string?)sample["page"] != name)
                    throw new InvalidDataException("正式确认或奖励页校准无效。[clan_battle_daily_overlay_calibration_invalid:" + name + "]");
                sections.Add(overlay);
            }
        }
        foreach (var section in sections)
        {
            if (section["samples"] is not JArray { Count: > 0 } samples) throw new InvalidDataException("校准样本缺失。[clan_battle_calibration_samples_missing]");
            foreach (var token in samples)
            {
                if (token is not JObject sample || !Keys(sample, "page", "path", "sha256") || sample["page"]?.Type != JTokenType.String
                    || sample["path"]?.Type != JTokenType.String || sample["sha256"]?.Type != JTokenType.String)
                    throw new InvalidDataException("校准样本引用无效。[clan_battle_calibration_sample_invalid]");
                _ = ReadVerified(root, (string)sample["path"]!, (string)sample["sha256"]!, 32 * 1024 * 1024);
            }
        }
        foreach (var spec in profile.Descendants().OfType<JObject>().Prepend(profile).Where(value => (string?)value["recognition"] == "TemplateMatch"))
        {
            if ((spec["template"] is JArray) != (spec["sha256"] is JArray))
                throw new InvalidDataException("校准模板摘要数量不一致。[clan_battle_calibration_template_invalid]");
            var templates = spec["template"] as JArray ?? new JArray(spec["template"]);
            var hashes = spec["sha256"] as JArray ?? new JArray(spec["sha256"]);
            if (templates.Count is < 1 or > 16 || templates.Count != hashes.Count
                || templates.Any(value => value.Type != JTokenType.String) || hashes.Any(value => value.Type != JTokenType.String))
                throw new InvalidDataException("校准模板引用无效。[clan_battle_calibration_template_invalid]");
            for (var index = 0; index < templates.Count; index++)
                _ = ReadVerified(root, "assets/resource/image/" + (string)templates[index]!, (string)hashes[index]!, 4 * 1024 * 1024);
        }
        if (capabilities.Contains("timed"))
        {
            if (profile["timing_measurements"] is not JObject binding || !Keys(binding, "path", "sha256")
                || binding["path"]?.Type != JTokenType.String || binding["sha256"]?.Type != JTokenType.String)
                throw new InvalidDataException("秒数轴测量文件缺失。[clan_battle_calibration_timing_missing]");
            var measured = Json(ReadVerified(root, (string)binding["path"]!, (string)binding["sha256"]!, 1024 * 1024));
            if ((string?)measured["kind"] != "owner_native_clan_timing" || !JToken.DeepEquals(measured["calibration_id"], profile["calibration_id"])
                || !JToken.DeepEquals(measured["battle_hud"], profile["battle_hud"]) || (string?)measured["observation_scope"] != "full_read_tick"
                || (string?)measured["action_scope"] != "toggle_dispatch_and_full_team_readback" || measured["battle_speed"]?.Type != JTokenType.Integer || (int?)measured["battle_speed"] != 1
                || new[] { "observation_intervals_ms", "action_latencies_ms", "game_second_ms" }.Any(key => measured[key] is not JArray { Count: >= 20 } values
                    || values.Any(value => value.Type != JTokenType.Integer || (long)value <= 0)))
                throw new InvalidDataException("秒数轴测量字段或绑定不完整。[clan_battle_timing_not_calibrated]");
        }
    }
    private static void VerifyDailyScope(JObject daily, JObject execution, bool ownUnitsOnly, bool carryover)
    {
        var dailyFields = new Dictionary<string, string[]> {
            ["lobby"] = new[] { "remaining_count", "carry_count", "cp", "period", "phase" },
            ["target"] = new[] { "remaining_count", "cp", "health", "boss", "phase" }, ["carry_empty"] = Array.Empty<string>() };
        var fields = new Dictionary<string, string[]> {
            ["adventure"] = Array.Empty<string>(), ["boss_detail"] = new[] { "boss", "phase" }, ["formation"] = Array.Empty<string>(),
            ["support"] = Array.Empty<string>(), ["battle"] = new[] { "remaining_seconds" }, ["result"] = new[] { "damage" },
            ["used_units"] = new[] { "game_day" }, ["carry"] = Array.Empty<string>() };
        var actionPages = new Dictionary<string, string> {
            ["open_clan"] = "adventure", ["open_boss"] = "lobby", ["open_formal"] = "boss_detail",
            ["open_formation"] = "target", ["cancel_formation"] = "formation", ["back_to_lobby"] = "boss_detail",
            ["back_to_adventure"] = "lobby", ["open_support"] = "formation", ["support_back"] = "support",
            ["characters_top"] = "formation", ["characters_next"] = "formation", ["support_top"] = "support",
            ["support_next"] = "support", ["start"] = "formation", ["open_used"] = "lobby", ["close_used"] = "used_units",
            ["open_carry"] = "lobby", ["close_carry"] = "carry", ["close_empty_carry"] = "carry_empty",
            ["open_carry_for_target"] = "target", ["select_carry"] = "carry", ["return_to_lobby"] = "result" };
        if (execution["pages"] is not JObject pages || execution["actions"] is not JObject actions
            || execution["samples"] is not JArray samples || daily["pages"] is not JObject dailyPages || daily["samples"] is not JArray dailySamples)
            throw new InvalidDataException("正式执行校准字段不完整。[clan_battle_daily_execution_calibration_missing]");
        var support = pages.ContainsKey("support"); var carry = pages.ContainsKey("carry");
        var selection = execution.ContainsKey("carry_selection");
        if (selection != pages.ContainsKey("carry_selection") || selection && !carry)
            throw new InvalidDataException("补偿选择校准必须整组保留或省略。[clan_battle_daily_execution_scope_invalid]");
        if (selection)
        {
            fields["carry_selection"] = Array.Empty<string>();
            actionPages["select_carry"] = "carry_selection";
            actionPages["enable_carry_team"] = "carry_selection";
            actionPages["close_carry_selection"] = "carry_selection";
            if (execution["carry_selection"] is not JObject selected || !Keys(selected, "slots", "reuse_on", "reuse_off")
                || selected["slots"] is not JArray { Count: >= 1 and <= 3 } selectionSlots
                || selectionSlots.Any(value => !DailyCarryStock(value, ownUnitsOnly))
                || !DailyNode(selected["reuse_on"]) || !DailyNode(selected["reuse_off"]))
                throw new InvalidDataException("补偿选择校准无效。[clan_battle_daily_execution_scope_invalid]");
        }
        var regularOnly = ownUnitsOnly && !carryover && !support && !carry;
        if (!support && !ownUnitsOnly || !carry && (!ownUnitsOnly || carryover))
            throw new InvalidDataException("借人或补偿校准未整组提供。[clan_battle_daily_execution_scope_missing]");
        if (!support)
        {
            fields.Remove("support");
            foreach (var name in new[] { "open_support", "support_back", "support_top", "support_next" }) actionPages.Remove(name);
            if (execution["formation_owner"]?.Type != JTokenType.Null)
                throw new InvalidDataException("借人校准必须整组保留或省略。[clan_battle_daily_execution_scope_invalid]");
        }
        else if (!Roi(execution["formation_owner"]))
            throw new InvalidDataException("借人校准无效。[clan_battle_daily_execution_scope_invalid]");
        if (!carry)
        {
            fields.Remove("carry");
            foreach (var name in new[] { "close_carry", "open_carry_for_target", "select_carry" }) actionPages.Remove(name);
        }
        if (!execution.ContainsKey("used_units"))
        {
            fields.Remove("used_units"); actionPages.Remove("open_used"); actionPages.Remove("close_used");
        }
        if (!Keys(daily, "version", "cp_semantics", "pages", "samples", "execution") || (string?)daily["cp_semantics"] != "daily_earned"
            || !Keys(dailyPages, dailyFields.Keys.ToArray()) || !Keys(pages, fields.Keys.ToArray()) || !Keys(actions, actionPages.Keys.ToArray())
            || !DailySamples(dailySamples, dailyFields.Keys) || !DailySamples(samples, fields.Keys)
            || dailyFields.Any(item => !DailyPage(dailyPages[item.Key], item.Value, false))
            || fields.Any(item => !DailyPage(pages[item.Key], item.Value, true))
            || actionPages.Any(item => !DailyAction(actions[item.Key], item.Key, item.Value)))
            throw new InvalidDataException("正式页面或操作校准未整组提供。[clan_battle_daily_execution_calibration_missing]");
        var selectedFormation = ownUnitsOnly && selection && execution["formation_mode"] is JObject selectionMode
            && new[] { "carryover", "stock_index", "time" }.All(key => selectionMode[key]?.Type == JTokenType.Null);
        if (execution["boss_slots"] is not JObject bosses || !Keys(bosses, "1", "2", "3", "4", "5")
            || bosses.Properties().Any(property => property.Value is not JObject slot || !Keys(slot, "round", "defeated")
                || !Roi(slot["round"]) || !DailyNode(slot["defeated"]))
            || execution.ContainsKey("used_units") && !DailyMembers(execution["used_units"], 15)
            || execution["carry_slots"] is not JArray stocks || carry && stocks.Count is < 1 or > 3 || !carry && stocks.Count != 0
            || stocks.Any(value => !DailyCarryStock(value, ownUnitsOnly))
            || execution["formation_mode"] is not JObject mode || !Keys(mode, "regular", "carryover", "stock_index", "time")
            || !DailyNode(mode["regular"])
            || !DailyNode(mode["carryover"]) && !selectedFormation && (!regularOnly || mode["carryover"]?.Type != JTokenType.Null)
            || carry && !selectedFormation && (!Roi(mode["stock_index"]) || !Roi(mode["time"]))
            || !carry && (mode["stock_index"]?.Type != JTokenType.Null || mode["time"]?.Type != JTokenType.Null)
            || !DailyNode(execution["speed_one"]))
            throw new InvalidDataException("正式队伍或补偿校准不完整。[clan_battle_daily_execution_scope_invalid]");
    }
    private static bool DailySamples(JArray samples, IEnumerable<string> pages) => samples.OfType<JObject>()
        .Select(row => (string?)row["page"]).ToHashSet(StringComparer.Ordinal).SetEquals(pages);
    private static bool DailyPage(JToken? value, string[] fields, bool rejectRequired) => value is JObject page
        && Keys(page, "labels", "rejects", "fields") && page["labels"] is JArray { Count: >= 2 } labels
        && page["rejects"] is JArray rejects && (!rejectRequired || rejects.Count > 0)
        && labels.All(DailyNode) && rejects.All(DailyNode) && page["fields"] is JObject areas && Keys(areas, fields)
        && areas.Properties().All(field => Roi(field.Value));
    private static bool DailyAction(JToken? value, string name, string page)
    {
        var scrolling = name is "characters_top" or "characters_next" or "support_top" or "support_next";
        return value is JObject action && Keys(action, scrolling ? new[] { "page", "label", "target", "begin", "end" } : new[] { "page", "label", "target" })
            && (string?)action["page"] == page && DailyNode(action["label"]) && Roi(action["target"])
            && (!scrolling || Roi(action["begin"]) && Roi(action["end"]));
    }
    private static bool DailyCarryStock(JToken? value, bool ownUnitsOnly)
    {
        if (value is not JObject slot || !Roi(slot["stock_index"]) || !Roi(slot["time"]) || !Roi(slot["target"])) return false;
        if (Keys(slot, "stock_index", "time", "members", "target"))
            return ownUnitsOnly && slot["members"] is JObject members && Keys(members, "slots")
                && members["slots"] is JArray { Count: 5 } slots && slots.All(Roi);
        return Keys(slot, "empty", "occupied", "stock_index", "time", "members", "support_owner", "target")
            && DailyNode(slot["empty"]) && DailyNode(slot["occupied"]) && DailyMembers(slot["members"], 5) && Roi(slot["support_owner"]);
    }
    private static bool DailyMembers(JToken? value, int count) => value is JObject members && Keys(members, "slots", "empty", "borrowed")
        && members["slots"] is JArray slots && slots.Count == count && slots.All(Roi)
        && members["empty"] is JArray empty && empty.Count == count && empty.All(DailyNode)
        && members["borrowed"] is JArray borrowed && borrowed.Count == count && borrowed.All(DailyNode);
    private static bool DailyNode(JToken? value) => value is JObject node && Roi(node["roi"])
        && ((string?)(node.ContainsKey("recognition") ? node["recognition"] : new JValue("OCR")) == "TemplateMatch"
            || (string?)(node.ContainsKey("recognition") ? node["recognition"] : new JValue("OCR")) == "OCR"
                && node["expected"]?.Type == JTokenType.String && ((string)node["expected"]!).StartsWith('^') && ((string)node["expected"]!).EndsWith('$'));
    private static bool Roi(JToken? value) => value is JArray { Count: 4 } roi
        && roi.All(item => item is JValue { Type: JTokenType.Integer, Value: not System.Numerics.BigInteger })
        && (long)roi[0] is >= 0 and < 1280 && (long)roi[1] is >= 0 and < 720
        && (long)roi[2] is > 0 and <= 1280 && (long)roi[3] is > 0 and <= 720
        && (long)roi[0] + (long)roi[2] <= 1280 && (long)roi[1] + (long)roi[3] <= 720;
    private static bool OpeningNode(JToken? value) => value is JObject node && Roi(node["roi"])
        && ((string?)node["recognition"] == "TemplateMatch" || (string?)node["recognition"] == "OCR"
            && node["expected"]?.Type == JTokenType.String && ((string)node["expected"]!).StartsWith('^') && ((string)node["expected"]!).EndsWith('$'));
    private static string Local(string root, string relative)
    {
        if (string.IsNullOrWhiteSpace(relative) || relative.Contains('\\') || relative.Contains(':') || Path.IsPathRooted(relative)
            || relative.Split('/').Any(part => part is ".." or "." or "")) throw new InvalidDataException("校准路径无效");
        var path = Path.GetFullPath(Path.Combine(root, relative));
        for (var current = path; current != root; current = Path.GetDirectoryName(current)!)
            if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("校准路径不能包含链接");
        return path;
    }
    private static byte[] ReadVerified(string root, string relative, string digest, int limit)
    {
        var path = Local(root, relative);
        if (!Digest(digest)) throw new InvalidDataException("校准文件摘要格式无效。[clan_battle_calibration_digest_invalid]");
        var info = new FileInfo(path);
        if (!info.Exists || info.Length is < 1 || info.Length > limit)
            throw new InvalidDataException("随包校准文件缺失或大小无效：" + relative + " [clan_battle_calibration_file_missing]");
        var bytes = File.ReadAllBytes(path);
        if (bytes.Length > limit || PcrClanGuide.Hash(bytes) != digest) throw new InvalidDataException("随包校准文件校验失败：" + relative + " [clan_battle_calibration_file_changed]");
        return bytes;
    }
}
