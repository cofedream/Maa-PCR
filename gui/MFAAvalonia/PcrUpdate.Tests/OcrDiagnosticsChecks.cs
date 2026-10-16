using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class OcrDiagnosticsChecks
{
    internal static void Run(string directory)
    {
        var type = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrOcrDiagnostics", true)!;
        var sink = RuntimeHelpers.GetUninitializedObject(type);
        void Set(string name, object value) => type.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(sink, value);
        var root = Path.Combine(directory, "ocr-diagnostics");
        Set("root", root); Set("run", "synthetic"); Set("gate", new object()); Set("hashes", new JObject());
        var ids = new Queue<(long Task, long Reco)>(); Set("ids", ids);
        var onEvent = type.GetMethod("Event", BindingFlags.Instance | BindingFlags.NonPublic)!;
        for (var i = 1; i <= 100; i++)
            onEvent.Invoke(sink, ["Node.Recognition.Failed", new JObject { ["task_id"] = 12, ["reco_id"] = i }]);
        if (ids.Count != 16 || Directory.Exists(root)) throw new Exception("callback_wrote_or_unbounded");
        type.GetMethod("Complete", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(sink, [null, 12L, false]);
        if (Directory.Exists(root)) throw new Exception("success_wrote_diagnostics");
        using var bitmap = new SkiaSharp.SKBitmap(4, 4);
        bitmap.Erase(SkiaSharp.SKColors.Blue);
        using var encoded = bitmap.Encode(SkiaSharp.SKEncodedImageFormat.Png, 100);
        var frame = encoded.ToArray();
        JObject Row() => new() { ["schema"] = "pcr-ocr-failure-v1", ["purpose"] = "diagnostic_only",
            ["roi"] = new JArray(1, 1, 2, 2), ["image_status"] = "missing" };
        var write = type.GetMethod("Write", BindingFlags.Instance | BindingFlags.NonPublic)!;
        write.Invoke(sink, [new List<(JObject, byte[]?)> { (Row(), frame) }]);
        var json = Directory.GetFiles(root, "*.json", SearchOption.AllDirectories);
        var row = JObject.Parse(File.ReadAllText(json[0]));
        if ((string?)row["image_status"] != "saved" || row["images"]?["roi"] == null)
            throw new Exception("raw_frame_or_roi_missing");
        var quota = Path.Combine(root, ".quota-lock"); File.WriteAllText(quota, "synthetic_other_writer");
        write.Invoke(sink, [new List<(JObject, byte[]?)> { (Row(), frame) }]);
        if (Directory.GetFiles(root, "*.json", SearchOption.AllDirectories).Length != 2 || !File.Exists(quota))
            throw new Exception("quota_contention_lost_metadata_or_deleted_other_lock");
        Console.WriteLine("PCR OCR diagnostics checks passed: bounded callback, success quiet, exact frame+ROI, writer contention; game inputs=0");
    }
}
