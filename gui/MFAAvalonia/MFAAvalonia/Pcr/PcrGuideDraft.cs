using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed class PcrGuideDraftFile
{
    public string Format { get; set; } = "pcr-guide-draft-v1";
    public PcrGuideInput Input { get; set; } = new();
}

// Offline preparation only; a draft never grants a trial or edits the active configuration.
public static class PcrGuideDraft
{
    private static readonly HashSet<string> Columns = new(StringComparer.Ordinal)
    { "星级", "等级", "Rank", "装备件数", "突破", "专武1等级", "专武2等级", "UB等级", "技能1等级",
        "技能2等级", "EX技能等级", "EX装备1", "EX装备2", "EX装备3", "SET标记" };
    private static PcrGuideInput Clone(PcrGuideInput input) =>
        PcrGuideReview.Parse(PcrGuideReview.Serialize(new PcrGuideDocument { Input = input })).Input;
    private static bool Relevant(PcrGuideFact fact, PcrGuideCondition condition) =>
        fact.UnitId == condition.UnitId && fact.Character == condition.Character && fact.Field == condition.Field
        && (fact.UnitId.HasValue || fact.Form == condition.Form);

    public static PcrGuideInput ApplyTable(PcrGuideInput original, string text)
    {
        if (string.IsNullOrWhiteSpace(text) || text.Length > 20_000) throw new InvalidDataException("请粘贴带表头的五人表格，最多 20,000 字符。");
        var input = Clone(original);
        var catalog = input.Catalog ?? throw new InvalidDataException("请先导入静态角色表。");
        var lines = text.Split('\n').Select(s => s.TrimEnd('\r')).Where(s => !string.IsNullOrWhiteSpace(s)).ToArray();
        if (lines.Length != 6) throw new InvalidDataException("表格须包含一行表头和五行角色。");
        var headers = lines[0].Split('\t').Select(s => s.Trim()).ToArray();
        if (headers.Length is < 1 or > 16 || headers[0] != "角色" || headers.Distinct().Count() != headers.Length
            || headers.Skip(1).Any(h => !Columns.Contains(h)))
            throw new InvalidDataException("首列须为“角色”，其后使用支持的字段名，列之间用制表符分隔，不能重复列。");
        var conditions = new List<PcrGuideCondition>();
        var ids = new HashSet<int>();
        for (var row = 1; row <= 5; row++)
        {
            var cells = lines[row].Split('\t').Select(s => s.Trim()).ToArray();
            if (cells.Length != headers.Length) throw new InvalidDataException($"第 {row} 位角色的列数与表头不一致。");
            var matches = catalog.Units.Where(u => u.Name == cells[0] || u.UnitId.ToString() == cells[0]).ToArray();
            if (matches.Length != 1 || !ids.Add(matches[0].UnitId))
                throw new InvalidDataException($"第 {row} 位角色名称或编号不明确，或与其他行重复；请使用静态表完整名称或编号。");
            var unit = matches[0];
            void Add(string field, string expected, string comparison)
            {
                var c = new PcrGuideCondition { Field = field, Expected = expected, Comparison = comparison, Evidence = lines[row] };
                PcrGuideData.Bind(c,unit); conditions.Add(c);
            }
            Add("持有","是","=");
            for (var col = 1; col < headers.Length; col++)
            {
                var value = cells[col] == "未知" ? "" : cells[col];
                if (value.Length > 1000) throw new InvalidDataException("单个表格字段过长。");
                var numeric = headers[col] is "星级" or "等级" or "Rank" or "装备件数" or "专武1等级" or "专武2等级"
                    or "UB等级" or "技能1等级" or "技能2等级" or "EX技能等级";
                if (numeric && value.Length > 0 && (!value.All(char.IsAsciiDigit) || !int.TryParse(value,out var number)
                    || number > 100_000 || (headers[col] is "星级" or "装备件数") && number > 6 || headers[col] == "星级" && number < 1))
                    throw new InvalidDataException($"第 {row} 位角色的 {headers[col]} 不是有效数值，未知项请留空。");
                Add(headers[col],value,"参考");
            }
        }
        input.Conditions = conditions.Concat(input.Conditions.Where(c => c.Character == "账号")).ToList();
        input.Facts = input.Facts.Where(f => input.Conditions.Any(c => Relevant(f,c))).ToList();
        var material = "五人表格（逐行原文；展示值均为参考，SET 标记保留原文）：\n" + string.Join('\n',lines);
        if (!(input.Material ?? "").Contains(material,StringComparison.Ordinal)) input.Material = (input.Material ?? "").TrimEnd() + "\n\n" + material;
        input.SourceConfirmed = input.CoverageConfirmed = input.SnapshotConfirmed = false;
        input.RequiresImageReview |= input.Identity?.ImportedSource != null;
        input.Identity = null;
        input.OpeningSets = PcrGuideTrial.SuggestedSets(input); input.OpeningConfirmed = false;
        PcrGuideReview.Validate(input);
        return input;
    }

    public static PcrGuideInput FromSource(PcrGuideInput current, PcrGuideIdentity source)
    {
        if (!source.Reviewable() || current.SourceUrl != source.SourceUrl || current.GuideServer != source.Server
            || current.TargetServer != source.Server || current.GuideStage != source.Stage || current.TargetStage != source.Stage)
            throw new InvalidDataException("请先将原图来源、区服与目标关卡对应。");
        // Only the independently checked identities are known; never infer reference stats or marks.
        var table = "角色\t星级\t等级\tRank\t专武1等级\t专武2等级\tEX装备1\tEX装备2\tEX装备3\tSET标记\n"
            + string.Join('\n',source.Members.Select(m => m.UnitId + new string('\t',9)));
        var input = ApplyTable(current,table);
        input.Identity = JsonConvert.DeserializeObject<PcrGuideIdentity>(JsonConvert.SerializeObject(source))!;
        input.RequiresImageReview |= source.ImportedSource != null;
        foreach (var member in input.Identity.Members) { member.ConfirmedFor=null; member.MaterialSha256=null; }
        return input;
    }

    private static PcrGuideInput PublicInput(PcrGuideInput input)
    {
        var result = Clone(new PcrGuideInput {
        Title=input.Title, SourceUrl=input.SourceUrl, Material=input.Material,
        RetrievedOn=input.RetrievedOn, ValidThrough=input.ValidThrough,
        GuideServer=input.GuideServer, GuideStage=input.GuideStage, GuideVersion=input.GuideVersion, Mode=input.Mode,
        TargetServer=input.TargetServer, TargetStage=input.TargetStage, TargetVersion=input.TargetVersion,
        Conditions=input.Conditions, Catalog=input.Catalog, Identity=input.Identity, OpeningSets=input.OpeningSets,
        RequiresImageReview=input.RequiresImageReview || input.Identity?.ImportedSource != null
        });
        if (result.Identity != null) foreach (var member in result.Identity.Members) { member.ConfirmedFor=null; member.MaterialSha256=null; }
        return result;
    }
    public static string Export(PcrGuideInput input) => PcrGuideReview.Serialize(new PcrGuideDraftFile { Input=PublicInput(input) });

    public static PcrGuideInput Import(PcrGuideInput current, byte[] bytes)
    {
        PcrGuideReview.Validate(current);
        if (bytes.Length is <= 0 or > 1_000_000) throw new InvalidDataException("攻略草稿为空或超过 1 MB。");
        PcrGuideDraftFile draft;
        try
        {
            using var stream = new MemoryStream(bytes);
            using var text = new StreamReader(stream,new UTF8Encoding(false,true));
            using var reader = new JsonTextReader(text) { MaxDepth=16,DateParseHandling=DateParseHandling.None };
            var value = JObject.Load(reader,new JsonLoadSettings { DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error });
            if (reader.Read()) throw new JsonReaderException();
            if (value["Format"]?.Type != JTokenType.String || value["Input"] is not JObject
                || value.Properties().Any(p => p.Name is not ("Format" or "Input"))
                || value.DescendantsAndSelf().OfType<JObject>().Any(o => o.Properties().GroupBy(p => p.Name,StringComparer.OrdinalIgnoreCase).Any(g => g.Count() != 1)))
                throw new JsonReaderException();
            draft = value.ToObject<PcrGuideDraftFile>(JsonSerializer.Create(new JsonSerializerSettings
                { MissingMemberHandling=MissingMemberHandling.Error,TypeNameHandling=TypeNameHandling.None }))!;
        }
        catch (Exception e) when (e is JsonException or DecoderFallbackException)
        { throw new InvalidDataException("攻略草稿格式无效、含重复或不支持的字段。",e); }
        if (draft?.Format != "pcr-guide-draft-v1" || draft.Input == null) throw new InvalidDataException("不是受支持的攻略草稿。");
        var input = draft.Input;
        PcrGuideReview.Validate(input);
        if (input.Snapshot != null || input.SnapshotConfirmed || input.Facts.Count != 0
            || new[] {input.AccountLabel,input.AccountServer,input.AccountVersion,input.AccountObservedOn}.Any(s => !string.IsNullOrEmpty(s)))
            throw new InvalidDataException("攻略草稿不能携带账号字段；请使用“导出攻略草稿”生成文件。");
        input = PublicInput(input);
        // Keep only facts already known in this panel, with the same catalog and account scope.
        if (current.Snapshot != null && current.Catalog != null && input.Catalog != null
            && input.TargetServer == current.AccountServer && current.AccountServer == "国服 B 站"
            && current.Snapshot.ContentVersion == current.AccountVersion
            && current.Snapshot.ContentVersion == current.Catalog.ContentVersion && current.Snapshot.Server == current.Catalog.Server
            && current.Catalog.Sha256 == input.Catalog.Sha256 && current.Catalog.ContentVersion == input.Catalog.ContentVersion
            && current.Catalog.Server == input.Catalog.Server
            && current.Catalog.Units.OrderBy(u => u.UnitId).SequenceEqual(input.Catalog.Units.OrderBy(u => u.UnitId)))
        {
            var local = Clone(current);
            input.Snapshot=local.Snapshot; input.AccountLabel=local.AccountLabel; input.AccountServer=local.AccountServer;
            input.AccountVersion=local.AccountVersion; input.AccountObservedOn=local.AccountObservedOn;
            input.Facts=local.Facts.Where(f => input.Conditions.Any(c => Relevant(f,c))).ToList();
        }
        PcrGuideReview.Validate(input);
        return input;
    }
}
