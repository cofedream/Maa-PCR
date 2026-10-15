using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed record PcrChannelPreset(string Id, string Label, string Package, string Activity);

// Curated data ships with the program; selecting a preset never grants input.
public static class PcrChannelCatalog
{
    public static IReadOnlyList<PcrChannelPreset> All { get; } = Load();

    private static IReadOnlyList<PcrChannelPreset> Load()
    {
        using var stream = typeof(PcrChannelCatalog).Assembly.GetManifestResourceStream("MFAAvalonia.Pcr.Channels.json")
            ?? throw new InvalidOperationException("内置渠道配置缺失，请重新解压完整程序包。");
        using var reader = new StreamReader(stream, System.Text.Encoding.UTF8);
        var rows = JArray.Parse(reader.ReadToEnd(), new JsonLoadSettings {
            DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        string Text(JToken row, string name) => row[name]?.Type == JTokenType.String
            && !string.IsNullOrWhiteSpace((string?)row[name]) ? (string)row[name]!
            : throw new InvalidOperationException("内置渠道字段无效：" + name);
        var channels = rows.Select(row => new PcrChannelPreset(Text(row, "id"), Text(row, "label"),
            Text(row, "package"), Text(row, "activity"))).ToArray();
        if (channels.Length == 0 || channels.Select(row => row.Id).Distinct().Count() != channels.Length
            || channels.Select(row => row.Package).Distinct().Count() != channels.Length)
            throw new InvalidOperationException("内置渠道配置为空或重复。");
        foreach (var channel in channels) _ = PcrMuMuDisplay.ComponentPattern(channel.Package, channel.Activity);
        return Array.AsReadOnly(channels);
    }

    public static PcrChannelPreset? Find(string package, string activity)
    {
        if (string.IsNullOrEmpty(package) || string.IsNullOrEmpty(activity)) return null;
        string Full(string p, string a) => a.StartsWith('.') ? p + a : a;
        return All.FirstOrDefault(row => row.Package == package
            && Full(row.Package, row.Activity) == Full(package, activity));
    }
}
