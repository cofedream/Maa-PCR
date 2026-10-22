using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed record PcrGuideUnit(int UnitId, string Name)
{
    public override string ToString() => $"{Name} · {UnitId}";
}
public sealed class PcrGuideCatalog
{
    public string Server { get; set; } = "";
    public string ContentVersion { get; set; } = "";
    public string Source { get; set; } = "";
    public string Sha256 { get; set; } = "";
    public List<PcrGuideUnit> Units { get; set; } = new();
}
public sealed record PcrGuideSnapshot(string AccountId, string Server, string ContentVersion,
    string ObservedAt, string Sha256, string FileName);

// Reads the preserved data_sync snapshot v1 / content catalog v1 contracts; never imports a login client.
public static class PcrGuideData
{
    internal const int MaxBytes = 32 * 1024 * 1024;
    private static readonly HashSet<string> SecretKeys = new(StringComparer.OrdinalIgnoreCase)
    { "password", "pwd", "access_key", "token", "sid", "request_id", "headers", "credentials", "username",
        "access_token", "refresh_token", "authorization", "session_id", "client_secret", "$type" };

    public static byte[] ReadLocal(string path)
    {
        if (!Path.IsPathFullyQualified(path) || path.StartsWith(@"\\") || path.StartsWith("//"))
            throw new InvalidDataException("请选择本机 JSON 文件。");
        using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (file.Length is <= 0 or > MaxBytes) throw new InvalidDataException("资料文件为空或超过 32 MiB。");
        var data = new byte[(int)file.Length]; file.ReadExactly(data);
        if (file.ReadByte() != -1) throw new IOException("文件读取时发生变化，请重试。");
        return data;
    }
    private static string Hash(byte[] data) => Convert.ToHexString(SHA256.HashData(data));
    private static JObject Parse(byte[] data)
    {
        if (data.Length is <= 0 or > MaxBytes) throw new InvalidDataException("资料文件为空或过大。");
        JObject root;
        try
        {
            using var stream = new MemoryStream(data);
            using var text = new StreamReader(stream, new UTF8Encoding(false, true));
            using var reader = new JsonTextReader(text) { MaxDepth = 24, DateParseHandling = DateParseHandling.None };
            root = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read()) throw new JsonReaderException();
        }
        catch (Exception ex) when (ex is JsonException or DecoderFallbackException)
        { throw new InvalidDataException("JSON 格式无效、字段重复或层级过深。"); }
        if (root.Descendants().OfType<JProperty>().Any(p => SecretKeys.Contains(p.Name)))
            throw new InvalidDataException("文件包含凭据或传输字段。请选择不含登录信息的规范化资料。");
        if (Integer(root["schema_version"], 1, 1) != 1 || Text(root["server"]) != "cn-bilibili")
            throw new InvalidDataException("仅支持已保留的国服 B 站数据格式 v1。");
        _ = Text(root["content_version"]);
        return root;
    }
    private static string Text(JToken? token, int max = 160)
    {
        if (token?.Type != JTokenType.String || string.IsNullOrWhiteSpace((string?)token)
            || ((string)token!).Length > max) throw new InvalidDataException("资料缺少有效的文本字段。");
        return ((string)token!).Trim();
    }
    private static int Integer(JToken? token, int min = 0, int max = 100_000_000)
    {
        if (token?.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out var value) || value < min || value > max)
            throw new InvalidDataException("资料含无效数值。");
        return (int)value;
    }
    private static JArray Rows(JToken? token, int max)
    {
        if (token is not JArray rows || rows.Count > max) throw new InvalidDataException("资料列表缺失或过大。");
        return rows;
    }
    public static void ValidateCatalog(PcrGuideCatalog catalog)
    {
        if (catalog.Server != "cn-bilibili" || string.IsNullOrWhiteSpace(catalog.ContentVersion)
            || catalog.Sha256?.Length != 64 || !catalog.Sha256.All(Uri.IsHexDigit)
            || string.IsNullOrWhiteSpace(catalog.Source) || catalog.Units == null || catalog.Units.Count is < 1 or > 2000
            || catalog.Units.Any(u => u == null || u.UnitId <= 0 || string.IsNullOrWhiteSpace(u.Name) || u.Name.Length > 100)
            || catalog.Units.Select(u => u.UnitId).Distinct().Count() != catalog.Units.Count)
            throw new InvalidDataException("静态角色表无效。");
    }
    public static PcrGuideCatalog Catalog(byte[] bytes, string fileName)
    {
        var root = Parse(bytes);
        if (root["source"]?.Type is not (JTokenType.String or JTokenType.Object)
            || root["source"] is JObject sourceObject && !sourceObject.HasValues
            || root["source"]?.Type == JTokenType.String && string.IsNullOrWhiteSpace((string?)root["source"]))
            throw new InvalidDataException("静态表缺少来源。");
        var source = root["source"]!.ToString(Formatting.None);
        if (source.Length > 2000) throw new InvalidDataException("静态表来源过长。");
        var result = new PcrGuideCatalog { Server = Text(root["server"]), ContentVersion = Text(root["content_version"]),
            Source = Path.GetFileName(fileName) + " · " + source, Sha256 = Hash(bytes),
            Units = Rows(root["units"], 2000).Select(u => new PcrGuideUnit(Integer(u["unit_id"], 1), Text(u["name"], 100))).ToList() };
        ValidateCatalog(result); return result;
    }
    public static DateTimeOffset Observation(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || !(value.EndsWith("Z", StringComparison.Ordinal) || value.Length >= 6 && (value[^6] == '+' || value[^6] == '-') && value[^3] == ':')
            || !DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.None, out var observed))
            throw new InvalidDataException("采集时间缺少时区或格式无效。");
        return observed;
    }
    public static void ValidateSnapshot(PcrGuideSnapshot snapshot)
    {
        if (snapshot.Server != "cn-bilibili" || string.IsNullOrWhiteSpace(snapshot.AccountId)
            || snapshot.AccountId.Length > 30 || !snapshot.AccountId.All(char.IsAsciiDigit) || snapshot.AccountId.All(c => c == '0')
            || string.IsNullOrWhiteSpace(snapshot.ContentVersion) || snapshot.ContentVersion.Length > 160
            || snapshot.Sha256?.Length != 64 || !snapshot.Sha256.All(Uri.IsHexDigit)
            || string.IsNullOrWhiteSpace(snapshot.FileName) || snapshot.FileName.Length > 260)
            throw new InvalidDataException("保存的快照来源信息无效。");
        _ = Observation(snapshot.ObservedAt);
    }
    public static void Bind(PcrGuideCondition condition, PcrGuideUnit unit)
    { condition.UnitId = unit.UnitId; condition.Character = unit.Name; condition.Form = "编号 " + unit.UnitId; }

    private static void Keys(JObject obj, params string[] allowed)
    {
        if (obj.Properties().Any(p => !allowed.Contains(p.Name)))
            throw new InvalidDataException("账号缓存只接受规范化资料，不保存原始响应或仓库。");
    }
    private static void Number(JToken? value, int min = 0, int max = 100_000)
    { if (value?.Type is not (null or JTokenType.Null)) _ = Integer(value, min, max); }
    private static void Boolean(JToken? value)
    { if (value?.Type is not (null or JTokenType.Null or JTokenType.Boolean)) throw new InvalidDataException("资料状态必须为布尔值。"); }

    // Validate the entire normalized roster before saving it, including unselected characters.
    public static JObject AccountSnapshot(byte[] bytes, PcrGuideCatalog catalog, string accountId, DateTimeOffset now)
    {
        ValidateCatalog(catalog);
        var root = Parse(bytes);
        Keys(root, "schema_version", "server", "account_id", "content_version", "observed_at", "ownership_complete", "unknown_fields", "units", "account");
        var id = Text(root["account_id"], 30);
        if (id != accountId || !id.All(char.IsAsciiDigit) || id.All(c => c == '0')) throw new InvalidDataException("账号快照与所选账号不符。");
        if (Text(root["content_version"]) != catalog.ContentVersion || Text(root["server"]) != catalog.Server)
            throw new InvalidDataException("账号快照与静态表的区服或内容版本不符。");
        if (Observation(Text(root["observed_at"], 64)) > now) throw new InvalidDataException("账号快照采集时间异常。");
        Boolean(root["ownership_complete"]);
        var unknown = Rows(root["unknown_fields"], 60_000).Select(x => Text(x, 200)).ToHashSet();
        if (root["ownership_complete"]?.Value<bool>() == true && unknown.Overlaps(new[] { "units", "unit_list", "ownership", "units.ownership" }))
            throw new InvalidDataException("完整持有范围与未知字段冲突。");
        var seen = new HashSet<int>();
        foreach (var value in Rows(root["units"], 2000))
        {
            if (value is not JObject unit) throw new InvalidDataException("角色资料格式无效。");
            Keys(unit, "unit_id", "name", "level", "rank", "rarity", "battle_rarity", "equipment_count", "limit_break", "skills", "unique_equipment", "ex_equipment");
            var uid = Integer(unit["unit_id"], 1);
            var role = catalog.Units.SingleOrDefault(u => u.UnitId == uid);
            if (!seen.Add(uid) || role == null || unit["name"] != null && Text(unit["name"], 100) != role.Name)
                throw new InvalidDataException("账号角色编号重复或与静态表不符。");
            Number(unit["level"], 1); Number(unit["rank"], 1);
            Number(unit["rarity"], 1, 6); Number(unit["battle_rarity"], 1, 6); Number(unit["equipment_count"], 0, 6); Boolean(unit["limit_break"]);
            if (unit["skills"]?.Type is not (null or JTokenType.Null))
            {
                if (unit["skills"] is not JObject skills) throw new InvalidDataException("技能资料无效。");
                Keys(skills, "ub", "s1", "s2", "ex"); foreach (var skill in skills.Properties()) Number(skill.Value);
            }
            foreach (var field in new[] { "unique_equipment", "ex_equipment" })
            {
                if (unit[field]?.Type is null or JTokenType.Null) continue;
                var slots = new HashSet<int>(); var ex = field == "ex_equipment";
                foreach (var item in Rows(unit[field], ex ? 3 : 2))
                {
                    if (item is not JObject equipment) throw new InvalidDataException("装备资料无效。");
                    if (ex) Keys(equipment, "slot", "is_equipped", "equipment_id", "name", "rarity", "rank", "enhancement_level", "enhancement_pt", "clan_battle_equipment", "effects");
                    else Keys(equipment, "slot", "is_equipped", "equipment_id", "level");
                    if (!slots.Add(Integer(item["slot"], 1, ex ? 3 : 2))) throw new InvalidDataException("装备槽重复。");
                    Boolean(item["is_equipped"]); Boolean(item["clan_battle_equipment"]);
                    Number(item["equipment_id"], 0, 100_000_000); Number(item["level"]);
                    Number(item["rarity"], 1, 5); Number(item["rank"], 0, 100); Number(item["enhancement_level"], 0, 100); Number(item["enhancement_pt"], 0, 100_000_000);
                    if (item["name"]?.Type is not (null or JTokenType.Null)) _ = Text(item["name"], 100);
                    if (item["effects"]?.Type is not (null or JTokenType.Null)) _ = Text(item["effects"], 2000);
                    if (item["is_equipped"]?.Value<bool>() == false && ((int?)item["equipment_id"] > 0 || (int?)item["level"] > 0))
                        throw new InvalidDataException("空装备槽带有装备或等级。");
                }
            }
        }
        _ = ValidateAccountProgress(root);
        return root;
    }

    private static JObject? ValidateAccountProgress(JObject root)
    {
        if (root["account"]?.Type is not (null or JTokenType.Null))
        {
            if (root["account"] is not JObject progress) throw new InvalidDataException("账号强化格式无效。");
            Keys(progress, "attribute_skill", "master_skill", "mapping_sha256", "attribute_levels", "unit_roles", "progress_mapping_sha256");
            if (progress["mapping_sha256"] != null && (Text(progress["mapping_sha256"], 64).Length != 64 || !Text(progress["mapping_sha256"]).All(Uri.IsHexDigit)))
                throw new InvalidDataException("强化映射摘要无效。");
            foreach (var field in new[] { "attribute_skill", "master_skill" })
            {
                if (progress[field]?.Type is null or JTokenType.Null) continue;
                if (progress[field] is not JObject skill) throw new InvalidDataException("账号强化值无效。");
                if (field == "attribute_skill") { Keys(skill, "page", "completed_merge"); _ = Integer(skill["page"], 1, 100); _ = Integer(skill["completed_merge"], 0, 100); }
                else { Keys(skill, "node"); _ = Integer(skill["node"], 0, 100_000); }
            }
            var hasDetails = progress["attribute_levels"]?.Type is not (null or JTokenType.Null)
                || progress["unit_roles"]?.Type is not (null or JTokenType.Null);
            if (!hasDetails && progress["progress_mapping_sha256"] == null) return null;
            const string digest = "807905b07d97bdb9755e8361ace682554d025d899adab9ee0daf421419e7fd1c";
            // shortcut: only this official content version is mapped; verify a new table before extending it.
            if ((string?)root["content_version"] != "202610141431" || (string?)progress["progress_mapping_sha256"] != digest)
                throw new InvalidDataException("属性与职能映射版本或摘要不符，原资料保持。");
            var path = Path.Combine(PcrAlphaOwner.Repository, "assets", "guide", "account-progress-202610141431.json");
            var bytes = ReadLocal(path);
            if (!Hash(bytes).Equals(digest, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("属性与职能映射文件校验失败。");
            var rules = JObject.Parse(new UTF8Encoding(false, true).GetString(bytes));
            if (progress["attribute_levels"]?.Type is not (null or JTokenType.Null))
            {
                var seen = new HashSet<int>();
                foreach (var row in Rows(progress["attribute_levels"], 5))
                {
                    if (row is not JObject item) throw new InvalidDataException("属性等级格式无效。");
                    Keys(item, "talent_id", "total_point", "level");
                    if (!seen.Add(Integer(item["talent_id"], 1, 5))) throw new InvalidDataException("属性编号重复。");
                    Number(item["total_point"], 0, 100_000_000); Number(item["level"], 1, 1000);
                    if (item["level"]?.Type == JTokenType.Integer)
                    {
                        var points = Integer(item["total_point"]);
                        var levels = (JArray)rules["attribute_levels"]!;
                        if (points > (int)levels.Last![0]! || (int)item["level"]! != (int)levels.Last(x => (int)x[0]! <= points)[1]!)
                            throw new InvalidDataException("属性等级与累计积分不对应。");
                    }
                }
            }
            if (progress["unit_roles"]?.Type is not (null or JTokenType.Null))
            {
                var seen = new HashSet<int>();
                foreach (var row in Rows(progress["unit_roles"], 8))
                {
                    if (row is not JObject item) throw new InvalidDataException("职能强化格式无效。");
                    Keys(item, "role_id", "slots");
                    var id = Integer(item["role_id"], 1, 8);
                    if (!seen.Add(id)) throw new InvalidDataException("职能编号重复。");
                    var slots = new HashSet<int>();
                    foreach (var entry in Rows(item["slots"], 4))
                    {
                        if (entry is not JObject slot) throw new InvalidDataException("职能槽位格式无效。");
                        Keys(slot, "slot", "slot_level", "enhance_level");
                        var number = Integer(slot["slot"], 1, 4);
                        if (!slots.Add(number)) throw new InvalidDataException("职能槽位重复。");
                        Number(slot["slot_level"], 0, 5); Number(slot["enhance_level"], 0, 5);
                        if (slot["slot_level"]?.Type == JTokenType.Integer && slot["enhance_level"]?.Type == JTokenType.Integer)
                        {
                            var level = (int)slot["slot_level"]!; var enhance = (int)slot["enhance_level"]!;
                            if ((level != 0 || enhance != 0) && rules["roles"]![id.ToString()]!["slots"]![number.ToString()]![level.ToString()] is not JArray)
                                throw new InvalidDataException("职能等级组合无效。");
                        }
                    }
                }
            }
            return rules;
        }
        return null;
    }

    private static bool ProgressUnknown(JObject root, string path) => ((JArray)root["unknown_fields"]!).Values<string>()
        .Any(p => p != null && (p == path || path.StartsWith(p + ".", StringComparison.Ordinal)));
    private static string? DetailValue(JObject root, string field, int id, int? slot = null)
    {
        var path = "account." + field + "." + id;
        if (ProgressUnknown(root, path) || root["account"]?[field] is not JArray rows) return null;
        var row = rows.FirstOrDefault(x => (int?)x[field == "attribute_levels" ? "talent_id" : "role_id"] == id);
        if (field == "attribute_levels")
            return !ProgressUnknown(root, path + ".level") && !ProgressUnknown(root, path + ".total_point") && row?["level"]?.Type == JTokenType.Integer
                ? row["level"]!.ToString() : null;
        path += ".slots." + slot;
        var entry = (row?["slots"] as JArray)?.FirstOrDefault(x => (int?)x["slot"] == slot);
        return !ProgressUnknown(root, path) && !ProgressUnknown(root, path + ".slot_level") && !ProgressUnknown(root, path + ".enhance_level")
            && entry?["slot_level"]?.Type == JTokenType.Integer && entry["enhance_level"]?.Type == JTokenType.Integer
            ? entry["slot_level"] + "-" + entry["enhance_level"] : null;
    }
    public static string DescribeProgress(JObject root)
    {
        var rules = ValidateAccountProgress(root);
        if (rules == null) return "属性等级：未知（快照未采集）。\n职能强化：未知（快照未采集）。";
        var levels = ((JObject)rules["attribute_names"]!).Properties().Select(p => p.Value + " " + (DetailValue(root,"attribute_levels",int.Parse(p.Name)) ?? "未知"));
        var roles = ((JObject)rules["roles"]!).Properties().Select(p => p.Value["name"] + "：" + string.Join("，", Enumerable.Range(1,4)
            .Select(slot => "槽" + slot + " " + (DetailValue(root,"unit_roles",int.Parse(p.Name),slot) ?? "未知"))));
        return "属性等级：" + string.Join("，",levels) + "。\n职能强化（各槽为等级-强化值；0-0 保留原值）：\n" + string.Join("\n",roles);
    }

    public static PcrGuideInput Import(PcrGuideInput original, byte[] bytes, string fileName, DateTimeOffset now)
    {
        PcrGuideReview.Validate(original);
        var catalog = original.Catalog ?? throw new InvalidDataException("请先导入静态角色表并关联本队角色。");
        ValidateCatalog(catalog);
        var root = Parse(bytes);
        var version = Text(root["content_version"]);
        if (version != catalog.ContentVersion) throw new InvalidDataException("快照与静态角色表的内容版本不同，请更新对应资料。");
        var account = Text(root["account_id"], 30);
        var progressRules = ValidateAccountProgress(root);
        if (!account.All(char.IsAsciiDigit) || account.All(c => c == '0')) throw new InvalidDataException("快照缺少有效账号编号。");
        if (original.Snapshot != null && original.Snapshot.AccountId != account)
            throw new InvalidDataException("快照属于另一账号。请先清空本面板的账号资料，再导入并确认。");
        var observedText = Text(root["observed_at"], 64); var observed = Observation(observedText);
        if (observed > now) throw new InvalidDataException("快照采集时间晚于当前时间。");
        var unknown = Rows(root["unknown_fields"], 60_000).Select(x => Text(x, 200)).ToHashSet(StringComparer.Ordinal);
        bool Unknown(string path) => unknown.Any(u => path == u || path.StartsWith(u + ".", StringComparison.Ordinal));
        bool ownershipUnknown = Unknown("units") || Unknown("unit_list") || Unknown("ownership") || Unknown("units.ownership");
        var completeToken = root["ownership_complete"];
        if (completeToken != null && completeToken.Type != JTokenType.Boolean) throw new InvalidDataException("持有范围必须明确。");
        bool complete = completeToken?.Value<bool>() == true;
        if (complete && ownershipUnknown) throw new InvalidDataException("持有范围与未知字段相互冲突。");
        var units = new Dictionary<int, JObject>();
        foreach (var row in Rows(root["units"], 2000))
        {
            if (row is not JObject unit || !units.TryAdd(Integer(unit["unit_id"], 1), unit))
                throw new InvalidDataException("角色编号重复或格式无效。");
        }
        var result = PcrGuideReview.Parse(PcrGuideReview.Serialize(new PcrGuideDocument { Input = original })).Input;
        result.Snapshot = new(account, Text(root["server"]), version, observedText, Hash(bytes), Path.GetFileName(fileName));
        result.SnapshotConfirmed = false;
        result.AccountServer = "国服 B 站"; result.AccountVersion = version;
        result.AccountObservedOn = observed.ToLocalTime().ToString("yyyy-MM-dd");
        if (string.IsNullOrWhiteSpace(result.AccountLabel)) result.AccountLabel = "账号 " + account;
        result.Facts.Clear();
        foreach (var c in result.Conditions.DistinctBy(c => (c.UnitId, c.Character, c.Form, c.Field)))
        {
            var fact = new PcrGuideFact { UnitId = c.UnitId, Character = c.Character, Form = c.Form, Field = c.Field };
            var path = c.UnitId.HasValue ? "units." + c.UnitId : "account";
            string reason = "快照未覆盖此条件";
            if (c.UnitId.HasValue)
            {
                var identity = catalog.Units.SingleOrDefault(u => u.UnitId == c.UnitId);
                if (identity == null || identity.Name != c.Character) throw new InvalidDataException("条件中的编号与角色表名称不一致，请重新关联。");
                if (units.TryGetValue(c.UnitId.Value, out var unit))
                {
                    if (unit["name"] != null && Text(unit["name"], 100) != identity.Name)
                        throw new InvalidDataException("快照名称与角色编号映射冲突。");
                    if (!ownershipUnknown && !Unknown(path))
                    {
                        if (c.Field == "持有") { if (!Unknown(path + ".ownership")) fact.Value = "是"; }
                        else
                        {
                            var field = c.Field switch { "等级" => "level", "Rank" => "rank", "解放星级" => "rarity", "星级" => "battle_rarity",
                                "装备件数" => "equipment_count", "突破" => "limit_break",
                                "UB等级" => "skills.ub", "技能1等级" => "skills.s1", "技能2等级" => "skills.s2", "EX技能等级" => "skills.ex", _ => null };
                            JToken? value = null;
                            if (field != null)
                            {
                                path += "." + field;
                                value = field.StartsWith("skills.") ? (unit["skills"] as JObject)?[field[7..]] : unit[field];
                            }
                            else if (c.Field is "专武1等级" or "专武2等级")
                            {
                                var slot = c.Field == "专武1等级" ? 1 : 2;
                                path += ".unique_equipment." + slot;
                                if (unit["unique_equipment"] is JArray equipment)
                                {
                                    var slots = new HashSet<int>();
                                    foreach (var item in equipment)
                                    {
                                        var currentSlot = Integer(item["slot"], 1, 2);
                                        if (!slots.Add(currentSlot)) throw new InvalidDataException("专武槽重复。");
                                        if (currentSlot != slot) continue;
                                        if (item["is_equipped"]?.Type is not (null or JTokenType.Null or JTokenType.Boolean))
                                            throw new InvalidDataException("专武装备状态无效。");
                                        if (item["is_equipped"]?.Type == JTokenType.Boolean)
                                        {
                                            if (item["is_equipped"]!.Value<bool>() && item["equipment_id"]?.Type != JTokenType.Null && item["equipment_id"] != null)
                                                _ = Integer(item["equipment_id"], 1);
                                            value = Unknown(path + ".is_equipped") ? null : item["level"];
                                            if (item["is_equipped"]!.Value<bool>() == false && value?.Type == JTokenType.Integer && Integer(value) != 0)
                                                throw new InvalidDataException("未装备专武却带有等级。");
                                        }
                                    }
                                }
                                path += ".level";
                            }
                            else if (c.Field is "EX装备1" or "EX装备2" or "EX装备3")
                            {
                                var slot = int.Parse(c.Field[^1..], CultureInfo.InvariantCulture);
                                path += ".ex_equipment." + slot;
                                if (unit["ex_equipment"] is JArray equipment)
                                {
                                    var seen = new HashSet<int>();
                                    foreach (var item in equipment)
                                    {
                                        var currentSlot = Integer(item["slot"], 1, 3);
                                        if (!seen.Add(currentSlot)) throw new InvalidDataException("EX 装备槽重复。");
                                        if (currentSlot != slot || Unknown(path) || Unknown(path + ".is_equipped")) continue;
                                        if (item["is_equipped"]?.Type != JTokenType.Boolean)
                                            throw new InvalidDataException("EX 装备状态不明确。");
                                        if (!item["is_equipped"]!.Value<bool>())
                                        {
                                            if (item["equipment_id"] != null && Integer(item["equipment_id"]) != 0)
                                                throw new InvalidDataException("空 EX 槽却带有装备编号。");
                                            fact.Value = "未装备";
                                        }
                                        else if (!new[] { "name", "equipment_id", "rarity", "rank", "enhancement_level", "clan_battle_equipment" }.Any(f => Unknown(path + "." + f)))
                                        {
                                            var id = Integer(item["equipment_id"], 1);
                                            var rarity = Integer(item["rarity"], 1, 5);
                                            var rank = Integer(item["rank"], 0, 100);
                                            var level = Integer(item["enhancement_level"], 0, 100);
                                            fact.Value = $"{Text(item["name"], 100)}（编号 {id}，{new[] { "", "铜", "银", "金", "粉", "彩" }[rarity]}，强化 {level}，升阶 {rank}）";
                                            if (item["clan_battle_equipment"] != null)
                                            {
                                                if (item["clan_battle_equipment"]!.Type != JTokenType.Boolean)
                                                    throw new InvalidDataException("EX 行会标记必须为布尔值。");
                                                if (item["clan_battle_equipment"]!.Value<bool>())
                                                    fact.Value += "（行会装备，当前为普通栏位，对深域生效）";
                                            }
                                            fact.Value += item["effects"]?.Type is not (null or JTokenType.Null) && !Unknown(path + ".effects")
                                                ? "；" + Text(item["effects"], 2000) : "；技能与附加词条未采集";
                                        }
                                    }
                                }
                            }
                            if (value != null && value.Type != JTokenType.Null && !Unknown(path))
                            {
                                if (field == "limit_break")
                                {
                                    if (value.Type != JTokenType.Boolean) throw new InvalidDataException("突破状态必须为明确布尔值。");
                                    fact.Value = value.Value<bool>() ? "是" : "否";
                                }
                                else fact.Value = Integer(value, field is "level" or "rank" or "rarity" or "battle_rarity" ? 1 : 0,
                                    field is "equipment_count" or "rarity" or "battle_rarity" ? 6 : 100_000).ToString(CultureInfo.InvariantCulture);
                            }
                        }
                    }
                }
                else if (c.Field == "持有" && complete && !Unknown(path + ".ownership")) fact.Value = "否";
            }
            else if (c.Character == "账号" && root["account"] is JObject progress && !Unknown("account"))
            {
                if (c.Field == "属性等级")
                {
                    reason = "快照未采集此属性，或条件未指定唯一属性";
                    if (progressRules != null)
                    {
                        var names = (JObject)progressRules["attribute_names"]!;
                        var ids = names.Properties().Where(p => p.Value.ToString() == (c.Form ?? "").Trim()).Select(p => int.Parse(p.Name)).ToArray();
                        if (string.IsNullOrWhiteSpace(c.Form))
                        {
                            var team = result.Conditions.Where(x => x.UnitId.HasValue).Select(x => x.UnitId!.Value).Distinct().ToArray();
                            var attributes = team.Select(id => (int?)progressRules["unit_attributes"]![id.ToString()]).ToArray();
                            if (team.Length > 0 && attributes.All(x => x.HasValue)) ids = attributes.Select(x => x!.Value).Distinct().ToArray();
                        }
                        if (ids.Length == 1) { path = "account.attribute_levels." + ids[0] + ".level"; fact.Value = DetailValue(root,"attribute_levels",ids[0]) ?? ""; }
                    }
                }
                else if (c.Field == "职能强化")
                {
                    reason = "需在形态栏指定职能与槽位（如：攻击型/槽1）；各项实际值见账号强化明细";
                    var parts = (c.Form ?? "").Split('/');
                    if (progressRules != null && parts.Length == 2 && parts[1].Length == 2 && parts[1][0] == '槽'
                        && int.TryParse(parts[1][1..],out var slot) && slot is >= 1 and <= 4)
                    {
                        var role = ((JObject)progressRules["roles"]!).Properties().SingleOrDefault(p => p.Value["name"]!.ToString() == parts[0]);
                        if (role != null) { path = "account.unit_roles." + role.Name + ".slots." + slot; fact.Value = DetailValue(root,"unit_roles",int.Parse(role.Name),slot) ?? ""; }
                    }
                }
                var field = c.Field switch { "属性技能" => "attribute_skill", "大师技能" => "master_skill", _ => null };
                if (field != null)
                {
                    path += "." + field;
                    if (!Unknown(path) && progress[field] is JObject value)
                    {
                        if (field == "attribute_skill" && !Unknown(path + ".page") && !Unknown(path + ".completed_merge"))
                            fact.Value = $"第{Integer(value["page"], 1, 100)}页第{Integer(value["completed_merge"], 0, 100)}合流点";
                        else if (field == "master_skill" && !Unknown(path + ".node"))
                            fact.Value = Integer(value["node"], 0, 100_000).ToString(CultureInfo.InvariantCulture);
                    }
                }
            }
            fact.Confirmed = fact.Value.Length != 0;
            fact.Source = $"{result.Snapshot.FileName} · SHA256 {result.Snapshot.Sha256} · 账号 {account} · {observedText} · {path}" +
                (fact.Confirmed ? "" : " · " + reason);
            if (fact.Confirmed && (c.Field is "属性等级" or "职能强化") && progressRules != null)
                fact.Source += " · 官方属性/职能映射 SHA256 " + root["account"]!["progress_mapping_sha256"];
            if (fact.Confirmed && (c.Field is "属性技能" or "大师技能" or "EX装备1" or "EX装备2" or "EX装备3")
                && root["account"] is JObject mapping && mapping["mapping_sha256"] != null)
            {
                var hash = Text(mapping["mapping_sha256"], 64);
                if (hash.Length != 64 || !hash.All(Uri.IsHexDigit)) throw new InvalidDataException("强化映射校验值无效。");
                fact.Source += " · 官方字段映射 SHA256 " + hash;
            }
            result.Facts.Add(fact);
        }
        PcrGuideReview.Validate(result); return result;
    }
}
