using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Threading.Tasks;
using System.Threading.Channels;
using MaaFramework.Binding;
using MaaFramework.Binding.Buffers;
using MFAAvalonia.Extensions;
using MFAAvalonia.Extensions.MaaFW;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// Diagnostic data never participates in ACK, settlement, or retry decisions.
internal sealed class PcrOcrDiagnostics
{
    private static readonly Channel<(PcrOcrDiagnostics Owner, JObject Row, byte[]? Frame)> Writer =
        Channel.CreateBounded<(PcrOcrDiagnostics, JObject, byte[]?)>(2);
    static PcrOcrDiagnostics()
    {
        _ = Task.Run(async () =>
        {
            await foreach (var item in Writer.Reader.ReadAllAsync())
                item.Owner.Write([(item.Row, item.Frame)]);
        });
    }
    private readonly object gate = new();
    private readonly Queue<(long Task, long Reco)> ids = new();
    private readonly string run = Guid.NewGuid().ToString("N");
    private readonly string root = Path.Combine(AppContext.BaseDirectory, ".runtime", "ocr-failures");
    private readonly JObject hashes = new();

    internal PcrOcrDiagnostics(MaaTasker tasker)
    {
        if (!tasker.Global.SetOption_RecoImageCacheLimit(16) || !tasker.Global.SetOption_DebugMode(true))
            throw new InvalidOperationException("Native image options unavailable");
        var resources = Path.Combine(AppContext.BaseDirectory, "assets", "resource");
        foreach (var folder in new[] { "model/ocr", "pipeline" })
        {
            var directory = Path.Combine(resources, folder);
            if (!Directory.Exists(directory)) continue;
            foreach (var file in Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories))
            {
                using var stream = File.OpenRead(file);
                hashes[Path.GetRelativePath(resources, file).Replace('\\', '/')] = Convert.ToHexStringLower(SHA256.HashData(stream));
            }
        }
    }

    internal void Event(string message, JObject detail)
    {
        // Called under native locks: scalar IDs only, no reverse SDK calls.
        if (message != "Node.Recognition.Succeeded" && message != "Node.Recognition.Failed") return;
        if (detail["task_id"]?.Type != JTokenType.Integer || detail["reco_id"]?.Type != JTokenType.Integer
            || !long.TryParse(detail["task_id"]?.ToString(), out var task)
            || !long.TryParse(detail["reco_id"]?.ToString(), out var reco)) return;
        lock (gate)
        {
            ids.Enqueue((task, reco));
            while (ids.Count > 16) ids.Dequeue();
        }
    }

    internal void Complete(MaaTasker tasker, long task, bool failed)
    {
        if (!failed) return;
        (long Task, long Reco)[] selected;
        lock (gate) selected = ids.Where(x => x.Task == task).Distinct().TakeLast(2).ToArray();
        if (selected.Length == 0) selected = [(task, 0)];
        var copies = new List<(JObject Row, byte[]? Frame)>();
        foreach (var id in selected)
        {
            var row = new JObject { ["schema"] = "pcr-ocr-failure-v1", ["owner"] = "daily",
                ["run_id"] = run, ["task_id"] = task, ["reco_id"] = id.Reco,
                ["purpose"] = "diagnostic_only", ["resource_hashes"] = hashes.DeepClone(),
                ["image_status"] = "missing", ["image_reason"] = "no_recognition_id" };
            byte[]? data = null;
            try
            {
                using var rect = new MaaRectBuffer();
                using var image = new MaaImageBuffer();
                using var draws = new MaaImageListBuffer();
                if (id.Reco > 0 && tasker.GetRecognitionDetail(id.Reco, out var name, out var algorithm,
                    out var hit, rect, out var raw, image, draws))
                {
                    row["node"] = name; row["algorithm"] = algorithm; row["hit"] = hit;
                    row["recognition"] = JToken.Parse(raw);
                    if (tasker.Resource?.GetNodeData(name, out var pipeline) == true)
                    {
                        row["pipeline"] = JToken.Parse(pipeline);
                        row["roi"] = row["pipeline"]?["recognition"] is JObject
                            ? row["pipeline"]?["recognition"]?["param"]?["roi"]?.DeepClone()
                            : row["pipeline"]?["roi"]?.DeepClone();
                    }
                    // Only the recognition's cached raw image, never a new screenshot.
                    if (image.TryGetEncodedData(out Stream encoded))
                    {
                        using var original = encoded;
                        using var stream = new MemoryStream();
                        encoded.CopyTo(stream); data = stream.ToArray();
                        row["image_reason"] = null;
                    }
                    else row["image_reason"] = "native_image_evicted_or_unavailable";
                }
            }
            catch (Exception ex) { row["image_reason"] = "native_query_" + ex.GetType().Name; }
            copies.Add((row, data));
        }
        // At most two copied frames; this writer owns no SDK objects.
        foreach (var (row, frame) in copies)
        {
            if (Writer.Writer.TryWrite((this, row, frame))) continue;
            row["image_status"] = "missing"; row["image_reason"] = "writer_queue_full";
            _ = Task.Run(() => Write([(row, null)]));
        }
    }

    internal void Missing(long task, string reason)
    {
        _ = Task.Run(() => Write([(new JObject { ["schema"] = "pcr-ocr-failure-v1",
            ["owner"] = "daily", ["run_id"] = run, ["task_id"] = task,
            ["purpose"] = "diagnostic_only", ["resource_hashes"] = hashes.DeepClone(),
            ["image_status"] = "missing", ["image_reason"] = reason }, null)]));
    }

    private void Write(List<(JObject Row, byte[]? Frame)> copies)
    {
        long used = 0;
        foreach (var (row, frame) in copies)
        {
            try
            {
                Directory.CreateDirectory(root);
                var directory = Path.Combine(root, run);
                Directory.CreateDirectory(directory);
                var stem = Guid.NewGuid().ToString("N");
                try
                {
                    var quota = Path.Combine(root, ".quota-lock");
                    // The handle owns the lock; a stale file survives safely after exit.
                    using var held = new FileStream(quota, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
                    var total = Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories).Sum(p => new FileInfo(p).Length);
                    used = Directory.EnumerateFiles(directory).Sum(p => new FileInfo(p).Length);
                    if (frame != null && used + frame.Length <= 16 * 1024 * 1024 && total + frame.Length + 65536 <= 512L * 1024 * 1024)
                    {
                        var name = stem + "-frame.png";
                        File.WriteAllBytes(Path.Combine(directory, name), frame);
                        row["images"] = new JObject { ["frame"] = new JObject { ["path"] = name,
                            ["sha256"] = Convert.ToHexStringLower(SHA256.HashData(frame)) } };
                        row["image_status"] = "saved"; used += frame.Length;
                        var roi = row["roi"] as JArray;
                        if (roi?.Count == 4 && roi.All(x => x.Type == JTokenType.Integer))
                        {
                            var r = roi.Values<int>().ToArray();
                            using var bitmap = SkiaSharp.SKBitmap.Decode(frame);
                            if (bitmap != null && r[0] >= 0 && r[1] >= 0 && r[2] > 0 && r[3] > 0
                                && r[0] + r[2] <= bitmap.Width && r[1] + r[3] <= bitmap.Height)
                            {
                                using var crop = new SkiaSharp.SKBitmap();
                                if (bitmap.ExtractSubset(crop, new SkiaSharp.SKRectI(r[0], r[1], r[0]+r[2], r[1]+r[3])))
                                {
                                    using var encoded = crop.Encode(SkiaSharp.SKEncodedImageFormat.Png, 100);
                                    var bytes = encoded.ToArray();
                                    if (used + bytes.Length <= 16 * 1024 * 1024 && total + frame.Length + bytes.Length + 65536 <= 512L * 1024 * 1024)
                                    {
                                        var cropName = stem + "-roi.png";
                                        File.WriteAllBytes(Path.Combine(directory, cropName), bytes);
                                        row["images"]!["roi"] = new JObject { ["path"] = cropName,
                                            ["sha256"] = Convert.ToHexStringLower(SHA256.HashData(bytes)) };
                                        used += bytes.Length;
                                    }
                                }
                            }
                        }
                    }
                    else if (frame != null) row["image_reason"] = "quota_exhausted";
                }
                catch (Exception ex) { row["image_status"] = "missing"; row["image_reason"] = "write_" + ex.GetType().Name; }
                File.WriteAllText(Path.Combine(directory, stem + ".json"), row.ToString());
            }
            catch (Exception ex) { System.Diagnostics.Trace.TraceWarning("OCR diagnostics missing: " + ex.GetType().Name); }
        }
    }
}
