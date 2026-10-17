using System;
using System.Collections.Generic;
using System.Diagnostics;
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
        var blocked = Row();
        using (var held = new FileStream(quota, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            write.Invoke(sink, [new List<(JObject, byte[]?)> { (blocked, frame) }]);
        if (Directory.GetFiles(root, "*.json", SearchOption.AllDirectories).Length != 2 || !File.Exists(quota))
            throw new Exception("quota_contention_lost_metadata_or_deleted_other_lock");
        if ((string?)blocked["image_status"] != "missing" || (string?)blocked["image_reason"] != "write_IOException")
            throw new Exception("active_quota_owner_was_not_exclusive");
        var start = new ProcessStartInfo(Environment.ProcessPath!) { UseShellExecute = false, CreateNoWindow = true };
        if (Path.GetFileNameWithoutExtension(Environment.ProcessPath).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
            start.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
        start.ArgumentList.Add(root); start.ArgumentList.Add("--ocr-lock-crash");
        using (var child = Process.Start(start)!)
        {
            if (!child.WaitForExit(10000)) { child.Kill(entireProcessTree: true); child.WaitForExit(); throw new Exception("quota_test_child_timed_out"); }
            if (child.ExitCode != 17) throw new Exception("quota_test_child_did_not_exit_while_holding_lock");
        }
        var recovered = Row();
        write.Invoke(sink, [new List<(JObject, byte[]?)> { (recovered, frame) }]);
        if ((string?)recovered["image_status"] != "saved" || recovered["images"]?["roi"] == null
            || File.ReadAllText(quota) != "synthetic_other_writer")
            throw new Exception("abandoned_quota_lock_prevented_recovery_or_changed_history");
        Console.WriteLine("PCR OCR diagnostics checks passed: bounded callback, success quiet, exact frame+ROI, active writer exclusion, interrupted child recovery; game inputs=0");
    }
}
