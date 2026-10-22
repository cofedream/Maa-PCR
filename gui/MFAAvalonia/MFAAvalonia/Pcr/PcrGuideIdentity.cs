using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System.Buffers.Binary;
using System.Globalization;
using System.Text.RegularExpressions;

namespace MFAAvalonia.Pcr;

public sealed class PcrGuidePortrait
{
    public int UnitId { get; set; }
    public string Name { get; set; } = "";
    public string Attribute { get; set; } = "";
    public int IconUnitId { get; set; }
    public double Score { get; set; }
    public double Margin { get; set; }
    [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
    public string? ConfirmedFor { get; set; }
    [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
    public string? MaterialSha256 { get; set; }
    [JsonIgnore] public bool Automatic => Score >= .82 && Margin >= .08;
}

public sealed record PcrGuideSourceEvidence(string OriginalSha256, string Locator, string RetrievedOn);

public sealed class PcrGuideIdentity
{
    public const string SourceHash = "2ded6c8e580975fd1f7af315bce861aaa222e995be2cf7838eb6821ed8c740b5";
    public const string Source74Hash = "c67f76df9c787027ba06c9f446b084fa519eaf774b59ffe2508f6bc90bc5bfc6";
    public const string Source75Hash = "4d5b349e0c064707a36cf7a16a0ee66b4d55c1afe9d76ac2292bc80b8509059d";
    public const string Source82Hash = "8e934e10e418ed9e9fb9ac7220d13b26811023eb2910dc056fcfcef8067674aa";
    public const string Source83Hash = "ab4c8ee7030a03b056219b87284c69690136b5c356e2e954b755843fc5e7c5ac";
    public const string Source84Hash = "ad2f6046d4e4d8d99706ed49e6045e7502d3209db3a263621453f36a39cd0a66";
    public const string Source85Hash = "338466d43f78bcdc394ce70e1a8d589bdbc0be114008151052e75eaeff500da5";
    public const string Source86Hash = "983a27faa7edd3ef167aca5709cc4dd555dfff66d951dfeeb4167558d0807842";
    public const string Source88Hash = "3a7bdf2e2ae0f1b841b428fa60d58d9c991dcfcbb43e7815f90baa0b9e70fbc3";
    public static readonly PcrGuideSourceEvidence Source88Evidence = new("e0b31de83020c03ea36b310004b75ee99368849ed69cbb33c3b78dcc110f6119",
        "墨夕公开表格火6~8图，8-8第一队; crop=885,402,385,74; layout=fire8-344x66", "2026-10-19");
    public const string Url = "https://docs.qq.com/sheet/DQnFpRmNYWVVuWWFk?tab=m70z7u";
    public static readonly IReadOnlyList<int> Team = Array.AsReadOnly(new[] {129701,116701,134701,135501,104701});
    public string SourceUrl { get; set; } = "";
    public string Server { get; set; } = "";
    public string Stage { get; set; } = "";
    public string ImageSha256 { get; set; } = "";
    public string ImageBase64 { get; set; } = "";
    public List<PcrGuidePortrait> Members { get; set; } = new();
    [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
    public PcrGuideSourceEvidence? ImportedSource { get; set; }
    public static bool Supports(string stage) => stage is "红焰深域 7-3" or "红焰深域 7-4" or "红焰深域 7-5" or "红焰深域 8-2" or "红焰深域 8-3" or "红焰深域 8-4" or "红焰深域 8-5" or "红焰深域 8-6" or "红焰深域 8-8";
    public static bool Required(PcrGuideInput input) => input.RequiresImageReview || input.Identity?.ImportedSource != null || Supports(input.GuideStage) || Supports(input.TargetStage);
    public bool NeedsConfirmation(PcrGuidePortrait member) => ImportedSource != null || !member.Automatic;

    public static string MaterialHash(PcrGuideInput input) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(input.Material))).ToLowerInvariant();
    public string ConfirmationBinding(PcrGuidePortrait member, string materialHash) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
        string.Join("|", SourceUrl, Server, Stage, ImageSha256, member.UnitId, member.IconUnitId, materialHash)
        + (ImportedSource == null ? "" : "|" + JsonConvert.SerializeObject(ImportedSource))))).ToLowerInvariant();
    public void Confirm(int index, PcrGuideInput input)
    {
        if (!Reviewable() || index < 0 || index >= Members.Count || input.SourceUrl != SourceUrl || input.GuideStage != Stage || input.GuideServer != Server
            || !input.Conditions.Any(c => c.UnitId == Members[index].UnitId)) throw new InvalidDataException("先将原图、关卡与角色编号对应，再逐人确认。");
        var member=Members[index]; member.MaterialSha256=MaterialHash(input); member.ConfirmedFor=ConfirmationBinding(member,member.MaterialSha256);
    }
    public bool Reviewable() => ImportedSource != null ? ImportedReviewable() : PackagedReviewable();
    // An imported row becomes executable only when its exact preserved source is supported.
    public bool Executable() => PackagedReviewable() && (Stage == "红焰深域 8-8"
        ? ImportedSource == Source88Evidence : ImportedSource == null);
    private bool PackagedReviewable()
    {
        var expectedHash = Stage switch { "红焰深域 7-4" => Source74Hash, "红焰深域 7-5" => Source75Hash,
            "红焰深域 8-2" => Source82Hash, "红焰深域 8-3" => Source83Hash, "红焰深域 8-4" => Source84Hash,
            "红焰深域 8-5" => Source85Hash, "红焰深域 8-6" => Source86Hash, "红焰深域 8-8" => Source88Hash, _ => SourceHash };
        if (SourceUrl != Url || Server != "国服 B 站" || !Supports(Stage) || ImageSha256 != expectedHash
            || Members == null || Members.Count != 5 || Members.Any(m => m == null)
            || Members.Select(m => m.UnitId).Distinct().Count() != 5
            || Members.Any(m => m.UnitId <= 0 || m.Attribute != "FIRE" || m.IconUnitId / 100 != m.UnitId / 100
                || !double.IsFinite(m.Score) || m.Score < 0 || m.Score > 1
                || !double.IsFinite(m.Margin) || m.Margin < 0 || m.Margin > 1)) return false;
        // Bind the packaged source proposals independently of editable guide conditions.
        var proposals = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join("|", Members.Select(m => $"{m.UnitId}:{m.IconUnitId}:{m.Attribute}"))))).ToLowerInvariant();
        if (proposals != (Stage == "红焰深域 8-8" ? "97874d79c7e8dd5fddf322042945da66e7e922a7d0da8dcf73f85fa9c5b98088" : Stage == "红焰深域 8-6" ? "85c58c394c0fd3c0dc6d8817a7de52f9acc653741d3771b831c985b92b10ffcb" : Stage == "红焰深域 8-3" ? "e4688a475e8075af7d35d8660ad468adb2b8cb4ffd5d09ffb16d3391a13c02d1" : "2beaddd4177a9b43fef3e69368823918a8bb4adfd15f7b0089151c71fb2aaaf0")) return false;
        try { return Convert.ToHexString(SHA256.HashData(Convert.FromBase64String(ImageBase64))).ToLowerInvariant() == expectedHash; }
        catch (FormatException) { return false; }
        catch (ArgumentNullException) { return false; }
    }
    private bool ImportedReviewable()
    {
        if (!PcrGuideReview.IsWebSource(SourceUrl) || Server != "国服 B 站" || !Regex.IsMatch(Stage ?? "", @"^红焰深域 8-([1-9]|10)$")
            || ImportedSource == null || !Regex.IsMatch(ImportedSource.OriginalSha256 ?? "", "^[0-9a-f]{64}$")
            || string.IsNullOrWhiteSpace(ImportedSource.Locator) || ImportedSource.Locator.Length > 500
            || !DateOnly.TryParseExact(ImportedSource.RetrievedOn, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out _)
            || Members == null || Members.Count != 5 || Members.Any(m => m == null)
            || Members.Select(m => m.UnitId).Distinct().Count() != 5
            || Members.Any(m => m.UnitId <= 0 || string.IsNullOrWhiteSpace(m.Name) || m.Name.Length > 100 || m.Attribute != "FIRE"
                || m.IconUnitId / 100 != m.UnitId / 100 || !double.IsFinite(m.Score) || m.Score < 0 || m.Score > 1
                || !double.IsFinite(m.Margin) || m.Margin < 0 || m.Margin > 1
                || !File.Exists(Path.Combine(PcrAlphaOwner.Repository,"assets","labyrinth","character-pack","icons","characters",$"icon_unit_{m.IconUnitId}.png")))) return false;
        try
        {
            var bytes = Convert.FromBase64String(ImageBase64);
            // shortcut: the calibrated fire-8 row is 344x66; other image layouts need a separate calibration.
            return bytes.Length is > 24 and <= 80_000 && bytes.AsSpan(0,8).SequenceEqual(new byte[]{137,80,78,71,13,10,26,10})
                && BinaryPrimitives.ReadInt32BigEndian(bytes.AsSpan(16,4)) == 344
                && BinaryPrimitives.ReadInt32BigEndian(bytes.AsSpan(20,4)) == 66
                && Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant() == ImageSha256;
        }
        catch (Exception e) when (e is FormatException or ArgumentNullException) { return false; }
    }
    public static PcrGuideIdentity Import(byte[] bytes, PcrGuideInput input)
    {
        if (bytes.Length is <= 0 or > 150_000) throw new InvalidDataException("原图核对文件为空或超过 150 KB。");
        PcrGuideIdentity? source;
        try
        {
            using var text = new StringReader(new UTF8Encoding(false,true).GetString(bytes));
            using var reader = new JsonTextReader(text) { MaxDepth=8, DateParseHandling=DateParseHandling.None };
            var value = JObject.Load(reader,new JsonLoadSettings { DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error });
            if (reader.Read() || value.DescendantsAndSelf().OfType<JObject>().Any(o => o.Properties().GroupBy(p => p.Name,StringComparer.OrdinalIgnoreCase).Any(g => g.Count()!=1)))
                throw new JsonReaderException();
            source = value.ToObject<PcrGuideIdentity>(JsonSerializer.Create(new JsonSerializerSettings
                { MissingMemberHandling=MissingMemberHandling.Error,TypeNameHandling=TypeNameHandling.None }));
        }
        catch (Exception e) when (e is JsonException or DecoderFallbackException)
        { throw new InvalidDataException("原图核对文件格式无效或含重复字段。",e); }
        if (source?.ImportedSource == null || !source.Reviewable() || input.Catalog == null)
            throw new InvalidDataException("原图、布局或目录形态图无效；请保留原文件核对。");
        if (source.SourceUrl != input.SourceUrl)
            throw new InvalidDataException($"来源链接不同。核对文件来源：{source.SourceUrl}；面板来源：{input.SourceUrl}。请先对应实际原图来源。");
        if (source.ImportedSource.RetrievedOn != input.RetrievedOn || source.Server != input.GuideServer || source.Server != input.TargetServer
            || source.Stage != input.GuideStage || source.Stage != input.TargetStage
            || source.Members.Any(m => !input.Catalog.Units.Any(u => u.UnitId == m.UnitId && u.Name == m.Name)))
            throw new InvalidDataException("原图取得日期、区服、关卡或角色目录不对应；原资料保持。");
        foreach (var member in source.Members) { member.ConfirmedFor=null; member.MaterialSha256=null; }
        return source;
    }
    public bool Valid() => Reviewable() && Members.All(m => !NeedsConfirmation(m) || m.MaterialSha256?.Length == 64
        && m.ConfirmedFor == ConfirmationBinding(m, m.MaterialSha256));
    public bool Matches(PcrGuideInput input) => Valid() && input.SourceUrl == SourceUrl && input.GuideServer == Server
        && input.TargetServer == Server && input.GuideStage == Stage && input.TargetStage == Stage
        && input.Conditions.Where(c => c.Character != "账号").All(c => c.UnitId.HasValue)
        && Members.All(m => !NeedsConfirmation(m) || m.MaterialSha256 == MaterialHash(input))
        && input.Conditions.Where(c => c.UnitId.HasValue).Select(c => c.UnitId!.Value).Distinct().Order().SequenceEqual(Members.Select(m => m.UnitId).Order());

    public static PcrGuideIdentity Load(string root, string stage = "红焰深域 7-3")
    {
        if (!Supports(stage)) throw new InvalidDataException("该关卡原图尚未核对。");
        var number = stage.Split(' ')[^1].Replace("-", "");
        var path = Path.Combine(root, "assets", "guide", $"fire{number}-source-identity.json");
        if (new FileInfo(path).Length > 100_000) throw new InvalidDataException("原图核对资料过大。");
        var identity = JsonConvert.DeserializeObject<PcrGuideIdentity>(File.ReadAllText(path), new JsonSerializerSettings
            { MissingMemberHandling = MissingMemberHandling.Error, MaxDepth = 8, TypeNameHandling = TypeNameHandling.None });
        if (identity?.Reviewable() != true || identity.Stage != stage) throw new InvalidDataException("原图核对资料无效，请保留原文件并重新核对。");
        foreach(var member in identity.Members) { member.ConfirmedFor=null; member.MaterialSha256=null; }
        return identity;
    }
}
