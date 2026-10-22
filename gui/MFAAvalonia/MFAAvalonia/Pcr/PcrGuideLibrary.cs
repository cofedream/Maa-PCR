using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace MFAAvalonia.Pcr;

public sealed record PcrGuideLibraryEntry(string Key, string Label)
{
    public override string ToString() => Label;
}

// Uses the existing atomic, concurrent-change checked store; entries contain guide material only.
public sealed class PcrGuideLibrary(string directory)
{
    private Dictionary<string, PcrGuideStore> stores = new();
    public IReadOnlyList<PcrGuideLibraryEntry> Entries { get; private set; } = Array.Empty<PcrGuideLibraryEntry>();
    private static string Key(PcrGuideInput input) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
        PcrGuideReview.Serialize(new { input.Title, input.SourceUrl, input.GuideServer, input.GuideStage,
            Units = input.Conditions.Where(c => c.UnitId.HasValue).Select(c => c.UnitId!.Value).Distinct().Order().ToArray() }))));
    private static PcrGuideInput Public(PcrGuideInput input) => PcrGuideDraft.Import(new(), Encoding.UTF8.GetBytes(PcrGuideDraft.Export(input)));
    public void Reload()
    {
        var next = new Dictionary<string, PcrGuideStore>();
        var entries = new List<PcrGuideLibraryEntry>();
        if (Directory.Exists(directory)) foreach (var path in Directory.EnumerateFiles(directory, "*.json"))
        {
            var store = new PcrGuideStore(path);
            var input = store.Load().Input;
            // Re-import through the same boundary as external drafts, including rejection of account data.
            input = PcrGuideDraft.Import(new(), Encoding.UTF8.GetBytes(PcrGuideReview.Serialize(new PcrGuideDraftFile { Input = input })));
            var key = Key(input);
            if (Path.GetFileNameWithoutExtension(path) != key) throw new InvalidDataException("攻略库文件与队伍标识不符，请保留原文件并核对。");
            next.Add(key, store);
            entries.Add(new(key, $"{input.GuideStage} · {input.Title}"));
        }
        stores = next; Entries = entries.OrderBy(e => e.Label, StringComparer.Ordinal).ToList();
    }
    public bool Save(PcrGuideInput input)
    {
        if (input.Conditions.Where(c => c.Field == "持有" && c.UnitId.HasValue).Select(c => c.UnitId).Distinct().Count() != 5)
            return false;
        var material = Public(input); var key = Key(material);
        if (!stores.TryGetValue(key, out var store))
        {
            var path = Path.Combine(directory, key + ".json");
            if (File.Exists(path)) throw new IOException("攻略库已有其他窗口保存的同队资料，请重开后再保存。");
            store = new(path); store.Load();
            if (File.Exists(path)) throw new IOException("攻略库已改变，请重开后再保存。");
        }
        store.Save(new() { Input = material });
        Reload(); return true;
    }
    public byte[] Read(string key)
    {
        if (!stores.TryGetValue(key, out var store)) throw new InvalidDataException("请选择本机攻略库中的队伍。");
        var input = store.Load().Input;
        if (Key(input) != key) throw new InvalidDataException("攻略库内容已改变，请重开后核对队伍。");
        return Encoding.UTF8.GetBytes(PcrGuideReview.Serialize(new PcrGuideDraftFile { Input = input }));
    }
}
