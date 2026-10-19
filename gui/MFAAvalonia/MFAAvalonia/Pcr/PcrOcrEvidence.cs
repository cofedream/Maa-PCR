using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using MFAAvalonia.Helper;
using Newtonsoft.Json.Linq;
using SkiaSharp;

namespace MFAAvalonia.Pcr;

// Derived copies only: raw recognition, full frames and pipeline parameters stay local.
internal static class PcrOcrEvidence
{
    private static readonly Regex Id = new("^[0-9a-f]{32}$");
    private static readonly Regex Hash = new("^[0-9a-f]{64}$", RegexOptions.IgnoreCase);
    private const int MaxMetadataBytes = 1024 * 1024;
    private const int MaxFrameBytes = 16 * 1024 * 1024;

    internal static List<(string Root, string Path, string Owner)> Sources(string dataRoot, string businessRoot, ExportLogPackageOptions options)
    {
        var result = new List<(string, string, string)>();
        if (!options.IncludeOnErrorImages) return result;
        var days = options.OnErrorImageTimeRange switch {
            ExportLogTimeRange.Last24Hours => 1, ExportLogTimeRange.Last3Days => 3,
            ExportLogTimeRange.Last7Days => 7, _ => 0 };
        foreach (var root in new[] { dataRoot, businessRoot }.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            var area = System.IO.Path.Combine(root, ".runtime", "ocr-failures");
            if (!Directory.Exists(area) || !PcrLogFiles.IsRegularPath(root, area)) continue;
            foreach (var directory in Directory.EnumerateDirectories(area))
            {
                if (!Id.IsMatch(System.IO.Path.GetFileName(directory)) || !PcrLogFiles.IsRegularPath(root, directory)) continue;
                foreach (var path in Directory.EnumerateFiles(directory, "*.json"))
                {
                    if (!Id.IsMatch(System.IO.Path.GetFileNameWithoutExtension(path)) || !PcrLogFiles.IsRegularPath(root, path)
                        || days != 0 && File.GetLastWriteTimeUtc(path) < DateTime.UtcNow.AddDays(-days)) continue;
                    try
                    {
                        var row = JObject.Parse(System.Text.Encoding.UTF8.GetString(Read(path, MaxMetadataBytes)));
                        if ((string?)row["schema"] != "pcr-ocr-failure-v1" || (string?)row["purpose"] != "diagnostic_only"
                            || (string?)row["run_id"] != System.IO.Path.GetFileName(directory)) continue;
                        var owner = (string?)row["owner"];
                        if (owner == "daily" && string.Equals(root, dataRoot, StringComparison.OrdinalIgnoreCase)
                            || owner == "labyrinth" && options.IncludeLabyrinthLog
                                && string.Equals(root, businessRoot, StringComparison.OrdinalIgnoreCase)) result.Add((root, path, owner!));
                    }
                    catch (Exception ex) when (ex is IOException or Newtonsoft.Json.JsonException or UnauthorizedAccessException
                        or ArgumentException or InvalidOperationException or FormatException) { }
                }
            }
        }
        return result;
    }

    private static byte[] Read(string path, int limit)
    {
        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        if (input.Length > limit) throw new IOException("Diagnostic file exceeds export limit");
        var bytes = new byte[(int)input.Length];
        input.ReadExactly(bytes);
        if (input.Position != input.Length) throw new IOException("Diagnostic file changed during export");
        return bytes;
    }

    internal static void WriteSnapshot(string root, string source, string destination, string owner)
    {
        if (!PcrLogFiles.IsRegularPath(root, source)) throw new IOException("Linked evidence rejected");
        var bytes = Read(source, MaxMetadataBytes);
        var row = JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
        var run = System.IO.Path.GetFileName(System.IO.Path.GetDirectoryName(source));
        var stem = System.IO.Path.GetFileNameWithoutExtension(source);
        if (!Id.IsMatch(run!) || !Id.IsMatch(stem) || (string?)row["schema"] != "pcr-ocr-failure-v1"
            || (string?)row["purpose"] != "diagnostic_only" || (string?)row["run_id"] != run
            || (string?)row["owner"] is not ("daily" or "labyrinth") || (string?)row["owner"] != owner)
            throw new IOException("Invalid or changed evidence identity");
        var node = (string?)row["node"] ?? (string?)row["entry"] ?? "unknown";
        var safe = new JObject { ["schema"] = "pcr-ocr-export-v1", ["run_id"] = run, ["owner"] = row["owner"],
            ["source_metadata_sha256"] = Convert.ToHexStringLower(SHA256.HashData(bytes)),
            ["node"] = Regex.IsMatch(node, "^[A-Za-z][A-Za-z0-9_]{0,127}$") ? node : "unknown",
            ["image_status"] = "missing", ["image_reason"] = "page_not_qualified_for_export" };
        foreach (var key in new[] { "task_id", "reco_id" })
            if (row[key]?.Type == JTokenType.Integer && long.TryParse(row[key]!.ToString(), out var id) && id > 0) safe[key] = id;
        var fingerprints = new JObject();
        if (row["resource_hashes"] is JObject hashes)
            foreach (var item in hashes.Properties().Take(2048))
                if (Regex.IsMatch(item.Name, "^(?:model/ocr|pipeline)/[A-Za-z0-9_./-]{1,200}$")
                    && !item.Name.Contains("..", StringComparison.Ordinal) && item.Value.Type == JTokenType.String
                    && Hash.IsMatch(item.Value.ToString())) fingerprints[item.Name] = item.Value.DeepClone();
        safe["resource_hashes"] = fingerprints;
        var directory = System.IO.Path.Combine(destination, ".runtime", "ocr-export", row["owner"]!.ToString(), run!);
        Directory.CreateDirectory(directory);
        var exportImage = System.IO.Path.Combine(directory, stem + "-joined.png");
        File.Delete(exportImage);
        // The sampled joined modal is the only account-free image region qualified here.
        var joined = row["hit"]?.Type == JTokenType.Boolean && row["hit"]!.Value<bool>()
            && (Regex.IsMatch(node, "^Laby(?:Opening(?:Any)?JoinedObserved|PortraitJoined[123]Observed)$")
                && row["recognition"]?.SelectTokens("$..text").Any(x => (string?)x == "角色加入") == true
                || Regex.IsMatch(node, "^LabyCharacters[0-9a-f]{32}Read$")
                    && row["recognition"]?.SelectTokens("$..page").Any(x => x.Type == JTokenType.String && (string?)x == "joined") == true);
        if (joined)
        {
            try
            {
                var image = row["images"]?["frame"];
                var filename = (string?)image?["path"];
                var hash = (string?)image?["sha256"];
                if (filename != stem + "-frame.png" || hash == null || !Hash.IsMatch(hash)) throw new IOException("Missing frame binding");
                var path = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(source)!, filename);
                if (!PcrLogFiles.IsRegularPath(root, path)) throw new IOException("Linked frame rejected");
                var frame = Read(path, MaxFrameBytes);
                if (!string.Equals(hash, Convert.ToHexStringLower(SHA256.HashData(frame)), StringComparison.OrdinalIgnoreCase)
                    || frame.Length < 24 || !frame.AsSpan(0, 8).SequenceEqual(new byte[] { 137,80,78,71,13,10,26,10 })
                    || BinaryPrimitives.ReadUInt32BigEndian(frame.AsSpan(16,4)) != 1280
                    || BinaryPrimitives.ReadUInt32BigEndian(frame.AsSpan(20,4)) != 720) throw new IOException("Invalid frame hash or dimensions");
                using var bitmap = SKBitmap.Decode(frame);
                using var crop = new SKBitmap();
                if (bitmap == null || !bitmap.ExtractSubset(crop, new SKRectI(340,90,940,620))) throw new IOException("Invalid joined crop");
                using var encoded = crop.Encode(SKEncodedImageFormat.Png, 100);
                var output = encoded.ToArray();
                File.WriteAllBytes(exportImage, output);
                safe["image_status"] = "saved"; safe["image_reason"] = null;
                safe["source_frame_sha256"] = hash; safe["crop"] = new JArray(340,90,600,530);
                safe["image"] = new JObject { ["path"] = stem + "-joined.png",
                    ["sha256"] = Convert.ToHexStringLower(SHA256.HashData(output)) };
            }
            catch (Exception ex) when (ex is IOException or ArgumentException or UnauthorizedAccessException)
            { safe["image_reason"] = "frame_missing_changed_or_invalid"; }
        }
        File.WriteAllText(System.IO.Path.Combine(directory, stem + ".json"), safe.ToString());
    }
}
