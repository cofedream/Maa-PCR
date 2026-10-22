using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MFAAvalonia.Helper;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// Private normalized data only; switching a guide never logs in or refreshes its timestamp.
public sealed class PcrGuideAccountStore(string directory)
{
    private string PathFor(PcrGuideCatalog catalog, string accountId)
    {
        PcrGuideData.ValidateCatalog(catalog);
        if (accountId.Length is < 1 or > 30 || !accountId.All(char.IsAsciiDigit) || accountId.All(c => c == '0'))
            throw new InvalidDataException("请选择有效账号。");
        var version = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(catalog.ContentVersion)));
        return Path.Combine(directory, "guide-accounts", catalog.Server, accountId, version + ".json");
    }
    public string? Stamp(PcrGuideCatalog catalog, string accountId)
    {
        var path = PathFor(catalog, accountId);
        return File.Exists(path) ? Convert.ToHexString(SHA256.HashData(PcrGuideData.ReadLocal(path))) : null;
    }
    public void Save(byte[] bytes, PcrGuideCatalog catalog, string accountId, DateTimeOffset now, string? expectedHash)
    {
        var incoming = PcrGuideData.AccountSnapshot(bytes, catalog, accountId, now);
        var path = PathFor(catalog, accountId); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        using var gate = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        if (Stamp(catalog, accountId) != expectedHash) throw new IOException("账号缓存已改变，本次未覆盖；请重新载入。");
        if (File.Exists(path))
        {
            var previous = PcrGuideData.AccountSnapshot(PcrGuideData.ReadLocal(path), catalog, accountId, now);
            if (PcrGuideData.Observation(previous["observed_at"]!.ToString()) > PcrGuideData.Observation(incoming["observed_at"]!.ToString())
                || previous["ownership_complete"]?.Value<bool>() == true && incoming["ownership_complete"]?.Value<bool>() != true)
                throw new InvalidDataException("不能用更早或部分资料覆盖已有完整快照。");
        }
        JsonHelper.SaveJsonTextAtomicallyOrThrow(path, new UTF8Encoding(false, true).GetString(bytes));
    }
    public PcrGuideInput Reuse(PcrGuideInput input, string? accountId, DateTimeOffset now)
    {
        if (input.Catalog == null || string.IsNullOrEmpty(accountId)) return input;
        if (input.Snapshot != null && input.Snapshot.AccountId != accountId) throw new InvalidDataException("当前资料属于另一账号，请先清空面板资料。");
        var path = PathFor(input.Catalog, accountId);
        if (!File.Exists(path)) return input;
        var bytes = PcrGuideData.ReadLocal(path);
        _ = PcrGuideData.AccountSnapshot(bytes, input.Catalog, accountId, now);
        var result = PcrGuideData.Import(input, bytes, Path.GetFileName(path), now);
        result.SnapshotConfirmed = input.SnapshotConfirmed && input.Snapshot?.Sha256 == result.Snapshot?.Sha256;
        return result;
    }
    public string Describe(PcrGuideInput input, DateTimeOffset now)
    {
        if (input.Snapshot is not { } snapshot) return "缺项：尚无账号快照。";
        var coverage = "部分资料";
        var progress = "属性等级与职能明细：需复用匹配的本机快照。";
        if (input.Catalog != null)
        {
            var path = PathFor(input.Catalog, snapshot.AccountId);
            if (File.Exists(path))
            {
                var bytes = PcrGuideData.ReadLocal(path);
                if (Convert.ToHexString(SHA256.HashData(bytes)) == snapshot.Sha256)
                {
                    var root = PcrGuideData.AccountSnapshot(bytes, input.Catalog, snapshot.AccountId, now);
                    coverage = (root["ownership_complete"]?.Value<bool>() == true ? "完整持有名单" : "部分资料") + $"（{root["units"]!.Count()} 人）";
                    progress = PcrGuideData.DescribeProgress(root);
                }
            }
        }
        var stale = now - PcrGuideData.Observation(snapshot.ObservedAt) > TimeSpan.FromHours(24);
        var missing = input.Facts.Count(f => !f.Confirmed || string.IsNullOrEmpty(f.Value));
        return $"{(stale ? "过期，需空闲时刷新" : "可复用")} · {coverage} · 本队缺项 {missing}\n账号 {snapshot.AccountId}；采集于 {snapshot.ObservedAt}；内容版本 {snapshot.ContentVersion}。\n{progress}";
    }
}
