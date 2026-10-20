using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;
using SkiaSharp;

internal static class OcrEvidenceChecks
{
    internal static string CreateJoinedFailure(string source, string owner)
    {
        var run = new string('c',32); var stem = new string('d',32);
        var directory = Path.Combine(source, ".runtime", "ocr-failures", run);
        Directory.CreateDirectory(directory);
        var metadata = Path.Combine(directory, stem + ".json");
        var frame = Path.Combine(directory, stem + "-frame.png");
        using (var image = new SKBitmap(1280,720))
        {
            using var canvas = new SKCanvas(image); canvas.Clear(SKColors.Red);
            using var paint = new SKPaint { Color = SKColors.Blue };
            canvas.DrawRect(new SKRect(340,90,940,620),paint);
            using var encoded = image.Encode(SKEncodedImageFormat.Png,100);
            File.WriteAllBytes(frame,encoded.ToArray());
        }
        string Hash(string path) => Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(path)));
        var row = new JObject { ["schema"] = "pcr-ocr-failure-v1", ["purpose"] = "diagnostic_only",
            ["owner"] = owner, ["run_id"] = run, ["task_id"] = 1, ["reco_id"] = 2,
            ["node"] = "LabyCharacters" + new string('a',32) + "Read", ["hit"] = true,
            ["recognition"] = new JObject { ["page"] = "joined", ["password"] = "PRIVATE_SECRET" },
            ["pipeline"] = new JObject { ["player_id"] = "1234567890123" },
            ["resource_hashes"] = new JObject { ["model/ocr/rec.onnx"] = new string('a',64), ["../credentials"] = "PRIVATE_SECRET" },
            ["images"] = new JObject { ["frame"] = new JObject { ["path"] = Path.GetFileName(frame), ["sha256"] = Hash(frame) } } };
        File.WriteAllText(metadata,row.ToString());
        return metadata;
    }

    internal static void Run(string fixtures, Action<bool, string> check)
    {
        var root = Path.Combine(fixtures, "ocr-evidence", Guid.NewGuid().ToString("N"));
        var source = Path.Combine(root, "source");
        var output = Path.Combine(root, "snapshot");
        var run = new string('c',32); var stem = new string('d',32);
        var metadata = CreateJoinedFailure(source,"labyrinth");
        var frame = Path.Combine(Path.GetDirectoryName(metadata)!,stem+"-frame.png");
        var row=JObject.Parse(File.ReadAllText(metadata));
        string Hash(string path) => Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(path)));
        var type = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrOcrEvidence",true)!;
        const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
        var collect = type.GetMethod("Sources",flags)!; var write = type.GetMethod("WriteSnapshot",flags)!;
        var options = new ExportLogPackageOptions { IncludeOnErrorImages=true, IncludeLabyrinthLog=true,
            OnErrorImageTimeRange=ExportLogTimeRange.All };
        List<(string Root,string Path,string Owner)> Collect() => (List<(string,string,string)>)collect.Invoke(null,[source,source,options])!;
        void Write() => write.Invoke(null,[source,metadata,output,row["owner"]!.ToString()]);
        var selected=Collect().Single();
        var changed=(JObject)row.DeepClone();changed["owner"]="daily";File.WriteAllText(metadata,changed.ToString());
        var refused=false;
        try { write.Invoke(null,[source,metadata,output,selected.Owner]); }
        catch(TargetInvocationException ex) when (ex.InnerException is IOException) { refused=true; }
        check(refused,"ocr_owner_changed_after_selection_is_rejected");
        row["node"]="LabyGamePortOpeningObserved";row["hit"]=false;
        row["recognition"]=new JObject { ["all"]=new JArray(new JObject { ["text"]="BOSS" }), ["password"]="PRIVATE_SECRET" };
        File.WriteAllText(metadata,row.ToString());
        var before = Directory.GetFiles(source,"*",SearchOption.AllDirectories).ToDictionary(x=>x,Hash);
        check(Collect().Count==1,"ocr_sources_deduplicate_roots");
        options=new ExportLogPackageOptions { IncludeLabyrinthLog=false,IncludeOnErrorImages=true,OnErrorImageTimeRange=ExportLogTimeRange.All };
        check(Collect().Count==0,"daily_export_excludes_labyrinth_evidence");
        options=new ExportLogPackageOptions { IncludeLabyrinthLog=true,IncludeOnErrorImages=false };
        check(Collect().Count==0,"ocr_evidence_image_opt_out");
        options=new ExportLogPackageOptions { IncludeLabyrinthLog=true,IncludeOnErrorImages=true,OnErrorImageTimeRange=ExportLogTimeRange.All };
        Write();
        var target = Path.Combine(output,".runtime","ocr-export","labyrinth",run);
        var safe = JObject.Parse(File.ReadAllText(Path.Combine(target,stem+".json")));
        var exported = Path.Combine(target,stem+"-failure.png");
        check(File.Exists(exported),"labyrinth_unknown_page_exports_full_failure_frame");
        using(var full=SKBitmap.Decode(exported))
            check(full.Width==1280 && full.Height==720 && full.GetPixel(0,0)==SKColors.Red
                && full.GetPixel(500,200)==SKColors.Blue,"labyrinth_failure_preserves_full_frame");
        var text=safe.ToString();
        check(!text.Contains("PRIVATE_SECRET") && !text.Contains("1234567890123") && safe["recognition"]==null
            && safe["pipeline"]==null && (string?)safe["source_metadata_sha256"]==Hash(metadata),"ocr_metadata_allowlist_and_source_binding");
        check((string?)safe["image"]?["sha256"]==Hash(frame) && Hash(exported)==Hash(frame)
            && safe["crop"]==null,"labyrinth_failure_png_bytes_and_hash_preserved");
        check((string?)safe["resource_hashes"]?["model/ocr/rec.onnx"]==new string('a',64)
            && safe["resource_hashes"]?["../credentials"]==null,"ocr_export_keeps_only_asset_fingerprints");
        // Repeat PNG decoding and snapshot writes; source bytes stay unchanged.
        for(var i=0;i<100;i++) Write();
        check(before.All(x=>Hash(x.Key)==x.Value),"ocr_100_snapshots_preserve_source_bytes");
        row["images"]!["frame"]!["path"]="../credentials.png"; File.WriteAllText(metadata,row.ToString());
        Write(); safe=JObject.Parse(File.ReadAllText(Path.Combine(target,stem+".json")));
        check((string?)safe["image_status"]=="missing" && safe["image"]==null && !File.Exists(exported),"ocr_traversal_refused");
        row["images"]!["frame"]!["path"]=Path.GetFileName(frame); row["images"]!["frame"]!["sha256"]=new string('0',64);
        File.WriteAllText(metadata,row.ToString()); Write();
        safe=JObject.Parse(File.ReadAllText(Path.Combine(target,stem+".json")));
        check((string?)safe["image_status"]=="missing","ocr_changed_frame_refused");
        var originalFrame=File.ReadAllBytes(frame);
        var corrupt=new byte[24];
        new byte[] {137,80,78,71,13,10,26,10}.CopyTo(corrupt,0);
        corrupt[18]=5;corrupt[22]=2;corrupt[23]=208;
        File.WriteAllBytes(frame,corrupt);row["images"]!["frame"]!["sha256"]=Hash(frame);
        File.WriteAllText(metadata,row.ToString());Write();
        safe=JObject.Parse(File.ReadAllText(Path.Combine(target,stem+".json")));
        check((string?)safe["image_status"]=="missing" && !File.Exists(exported),"labyrinth_corrupt_png_refused_after_matching_hash");
        File.WriteAllBytes(frame,originalFrame);
        row["images"]!["frame"]!["sha256"]=Hash(frame);
        row["owner"]="daily";row["node"]="LabyCharacters"+new string('a',32)+"Read";row["hit"]=true;
        row["recognition"]=new JObject { ["page"]="joined",["password"]="PRIVATE_SECRET" };
        File.WriteAllText(metadata,row.ToString()); Write();
        target=Path.Combine(output,".runtime","ocr-export","daily",run);
        exported=Path.Combine(target,stem+"-joined.png");
        using(var crop=SKBitmap.Decode(exported))
            check(crop.Width==600 && crop.Height==530 && crop.Pixels.All(p=>p==SKColors.Blue),"daily_crop_still_excludes_private_outside_region");
        row["hit"]=false;File.WriteAllText(metadata,row.ToString());Write();
        safe=JObject.Parse(File.ReadAllText(Path.Combine(target,stem+".json")));
        check((string?)safe["image_status"]=="missing" && !File.Exists(exported),"daily_unconfirmed_page_never_exports_image");
        row["owner"]="daily";row["node"]="ClientTitlePlayerId";row["hit"]=true;
        File.WriteAllText(metadata,row.ToString());Write();
        target=Path.Combine(output,".runtime","ocr-export","daily",run);
        safe=JObject.Parse(File.ReadAllText(Path.Combine(target,stem+".json")));
        check((string?)safe["image_status"]=="missing","ocr_account_page_exports_metadata_only");
        File.WriteAllText(metadata,"{\"schema\":{},\"password\":\"PRIVATE_SECRET\"}");
        check(Collect().Count==0,"ocr_corrupt_metadata_does_not_break_scan");
        File.WriteAllText(metadata,row.ToString()); File.SetLastWriteTimeUtc(metadata,DateTime.UtcNow.AddDays(-10));
        options=new ExportLogPackageOptions { IncludeLabyrinthLog=true,IncludeOnErrorImages=true,OnErrorImageTimeRange=ExportLogTimeRange.Last24Hours };
        check(Collect().Count==0,"ocr_export_time_filter");
        var archive=Path.Combine(root,"evidence.zip");ZipFile.CreateFromDirectory(output,archive);
        using(var zip=ZipFile.OpenRead(archive))
            check(zip.Entries.All(e=>!e.FullName.Contains("ocr-failures") && !e.FullName.EndsWith("-frame.png")),"ocr_zip_excludes_raw_metadata_and_source_paths");
    }
}
