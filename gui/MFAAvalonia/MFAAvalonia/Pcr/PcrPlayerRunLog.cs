using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// Player diagnostics only. Business ledgers and execution authority never live here.
internal sealed class PcrPlayerRunLog
{
    internal const long Quota = 16 * 1024 * 1024;
    internal const string FeedbackEmail = "a877236828@gmail.com";
    internal const string IssueUrl = "https://github.com/shiwandehongbai/Maa-PCR/issues/new";
    private static readonly object Gate = new();
    private static readonly Regex Id = new("^[0-9a-f]{32}$");
    private static readonly Regex ImageName = new("^image-[0-9a-f]{32}\\.png$");
    private static readonly HashSet<string> Active = new();
    internal string Function { get; }
    internal string RunId { get; }
    internal bool IsFinished { get; private set; }
    internal string DirectoryPath { get; }
    private string FeatureDirectory => Path.GetDirectoryName(DirectoryPath)!;
    internal string Title => Function switch { "labyrinth" => "黎明界", "clan" => "公会战", _ => "日常" };
    private PcrPlayerRunLog(string root, string function, string run)
    {
        if (function is not ("daily" or "labyrinth" or "clan") || !Id.IsMatch(run)) throw new ArgumentException("Invalid diagnostic identity");
        Function = function; RunId = run;
        DirectoryPath = Path.Combine(Path.GetFullPath(root), ".runtime", "player-logs", function, run);
    }
    internal static PcrPlayerRunLog Begin(string root, string function)
    {
        lock (Gate)
        {
            var log = new PcrPlayerRunLog(root, function, Guid.NewGuid().ToString("N"));
            var parent = log.DirectoryPath;
            while (!Directory.Exists(parent)) parent = Path.GetDirectoryName(parent)!;
            if (!PcrLogFiles.IsRegularPath(Path.GetPathRoot(parent)!, parent)) throw new IOException("Linked diagnostic directory rejected");
            Directory.CreateDirectory(log.DirectoryPath);
            log.RequireRegular();
            Active.Add(log.DirectoryPath);
            log.WriteManifest("running");
            log.Trim();
            return log;
        }
    }
    internal static PcrPlayerRunLog? Latest(string root, string function)
    {
        var folder = Path.Combine(root, ".runtime", "player-logs", function);
        if (!Directory.Exists(folder)) return null;
        return Directory.EnumerateDirectories(folder).Where(p => Id.IsMatch(Path.GetFileName(p)) && File.Exists(Path.Combine(p,"manifest.json")))
            .OrderByDescending(Directory.GetLastWriteTimeUtc)
            .Select(p => new PcrPlayerRunLog(root, function, Path.GetFileName(p)))
            .FirstOrDefault(p => PcrLogFiles.IsRegularPath(root, p.DirectoryPath));
    }
    private void RequireRegular() => _ = PcrLogFiles.IsRegularPath(Path.GetPathRoot(DirectoryPath)!, DirectoryPath)
        ? true : throw new IOException("Linked diagnostics rejected");
    private void RequireFile(string path)
    {
        var file = new FileInfo(path);
        if (file.LinkTarget != null || file.Exists && !PcrLogFiles.IsRegularPath(DirectoryPath, path))
            throw new IOException("Linked diagnostic file rejected");
    }
    internal static string Sanitize(string text)
    {
        text = Regex.Replace(text, @"(?i)(password|passwd|authorization|token|ticket|credential|cookie)[""']?(\s*[=:]\s*)[""']?[^""'\s,;}]+", "$1$2[已移除]");
        text = Regex.Replace(text, @"(?i)(account_id|viewer_id|玩家\s*ID|UID)[""']?(\s*[=:：]\s*)[""']?\d+", "$1$2[已移除]");
        return Regex.Replace(text, @"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s]+", "%USERPROFILE%");
    }
    internal void Append(string text, bool native = false)
    {
        lock (Gate)
        {
            RequireRegular();
            text = Sanitize(text);
            if (text.Length > 4096) text = text[..4096];
            var path = Path.Combine(DirectoryPath, native ? "native.log" : "events.log");
            RequireFile(path);
            File.AppendAllText(path, $"[{DateTime.Now:HH:mm:ss}] {text}\n", new UTF8Encoding(false));
            Tail(path, native ? 512 * 1024 : 2 * 1024 * 1024);
            Trim();
        }
    }
    internal void CaptureConnectionNativeLog(string root, DateTime startedAt, DateTime endedAt)
    {
        var captured = 0;
        try
        {
            var prefix = new Regex(@"^\[(?<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]\[[A-Z]+\]\[Px" + Environment.ProcessId + @"\]",
                RegexOptions.None, TimeSpan.FromSeconds(1));
            var directory = Path.Combine(root, "debug");
            // Only bounded native tails from this process and this connection window are shared.
            foreach (var path in PcrLogFiles.OrdinaryFiles(root)
                         .Where(p => string.Equals(Path.GetDirectoryName(p), directory, StringComparison.OrdinalIgnoreCase))
                         .OrderByDescending(File.GetLastWriteTimeUtc).Take(4).OrderBy(File.GetLastWriteTimeUtc))
            {
                if (!PcrLogFiles.IsRegularPath(Path.GetPathRoot(path)!, path)) continue;
                using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                var bytes = new byte[(int)Math.Min(input.Length, 512 * 1024)];
                input.Seek(-bytes.Length, SeekOrigin.End);
                input.ReadExactly(bytes);
                foreach (var line in Encoding.UTF8.GetString(bytes).Split('\n'))
                {
                    var match = prefix.Match(line);
                    if (!match.Success || !DateTime.TryParseExact(match.Groups["time"].Value, "yyyy-MM-dd HH:mm:ss.fff",
                            CultureInfo.InvariantCulture, DateTimeStyles.None, out var time)
                        || time < startedAt || time > endedAt) continue;
                    Append(line.TrimEnd('\r'), native: true);
                    captured++;
                }
            }
        }
        catch { Append("部分底层连接日志无法读取；已保留连接目标和界面报错。"); }
        Append(captured == 0 ? "未取得本次连接的底层日志；请附上报错截图。"
            : "已附上本进程在连接等待期间的底层日志片段（可能因轮换或大小限制截断）。");
    }
    private static void Tail(string path, int limit)
    {
        if (new FileInfo(path).Length <= limit) return;
        string tail;
        using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        {
            input.Seek(-limit / 2, SeekOrigin.End);
            using var reader = new StreamReader(input, Encoding.UTF8);
            _ = reader.ReadLine();
            tail = reader.ReadToEnd();
        }
        File.WriteAllText(path, "[较早日志已轮换]\n" + tail, new UTF8Encoding(false));
    }
    internal void Finish(string outcome)
    {
        lock (Gate)
        {
            if (IsFinished) return;
            RequireRegular(); WriteManifest(outcome); Active.Remove(DirectoryPath); Trim();
            IsFinished = true;
        }
    }
    private void WriteManifest(string outcome)
    {
        var path = Path.Combine(DirectoryPath, "manifest.json");
        RequireFile(path);
        File.WriteAllText(path, new JObject { ["schema"] = "pcr-player-log-v1", ["function"] = Function,
            ["run_id"] = RunId, ["updated_at"] = DateTimeOffset.UtcNow.ToString("O"), ["outcome"] = outcome,
            ["screenshot_note"] = "仅包含可用业务画面；没有截图时可能尚未进入业务页面或缓存不可用。",
            ["version"] = PcrSoftwareUpdateService.InstalledVersion(AppContext.BaseDirectory) }.ToString(), new UTF8Encoding(false));
    }
    private static bool Owned(string path) => Path.GetFileName(path) is "manifest.json" or "events.log" or "native.log"
        || ImageName.IsMatch(Path.GetFileName(path));
    private IEnumerable<string> Files(string directory) => Directory.EnumerateFiles(directory)
        .Where(p => Owned(p) && PcrLogFiles.IsRegularPath(FeatureDirectory, p));
    private void Trim()
    {
        var runs = Directory.EnumerateDirectories(FeatureDirectory).Where(p => Id.IsMatch(Path.GetFileName(p))
            && PcrLogFiles.IsRegularPath(FeatureDirectory, p)).ToArray();
        long total = runs.SelectMany(Files).Sum(p => new FileInfo(p).Length);
        bool Failed(string directory)
        {
            try { return (string?)JObject.Parse(File.ReadAllText(Path.Combine(directory,"manifest.json")))["outcome"]
                is "failed" or "cleanup_unconfirmed" or "not_started"; }
            catch { return false; }
        }
        var recentError = runs.Where(Failed).MaxBy(Directory.GetLastWriteTimeUtc);
        foreach (var run in runs.Where(p => p != DirectoryPath && !Active.Contains(p))
                     .OrderBy(p => p == recentError).ThenBy(Directory.GetLastWriteTimeUtc))
        {
            if (total <= Quota) break;
            foreach (var file in Files(run).ToArray()) { total -= new FileInfo(file).Length; File.Delete(file); }
            if (!Directory.EnumerateFileSystemEntries(run).Any()) Directory.Delete(run);
        }
        // Keep the most recent diagnostic picture when replacing older frames in a long run.
        foreach (var picture in Files(DirectoryPath).Where(p => ImageName.IsMatch(Path.GetFileName(p)))
                     .OrderBy(File.GetLastWriteTimeUtc).ToArray())
        {
            if (total <= Quota) break;
            total -= new FileInfo(picture).Length; File.Delete(picture);
        }
        if (total > Quota) throw new IOException("Player diagnostic quota exceeded");
    }
    internal string[] Screenshots()
    {
        lock (Gate) { RequireRegular(); return Files(DirectoryPath).Where(p => ImageName.IsMatch(Path.GetFileName(p))).OrderBy(File.GetLastWriteTimeUtc).ToArray(); }
    }
    internal string? Receive(JObject message)
    {
        if ((string?)message["run_id"] != RunId || (string?)message["function"] != Function) return null;
        var kind = (string?)message["event"];
        if (kind is "text" or "native")
        {
            if (message["text"]?.Type != JTokenType.String) return null;
            var text = Sanitize(message["text"]!.ToString());
            Append(text, native: kind == "native");
            return kind == "text" ? text : null;
        }
        if (kind == "image")
        {
            var name = (string?)message["name"] ?? "";
            if (!ImageName.IsMatch(name)) return null;
            lock (Gate)
            {
                RequireRegular();
                var path = Path.Combine(DirectoryPath, name);
                if (!File.Exists(path) || !PcrLogFiles.IsRegularPath(DirectoryPath, path) || new FileInfo(path).Length > 8 * 1024 * 1024) return null;
                var bytes = File.ReadAllBytes(path);
                if (!bytes.AsSpan().StartsWith(new byte[] {137,80,78,71,13,10,26,10})
                    || !string.Equals(Convert.ToHexStringLower(SHA256.HashData(bytes)), (string?)message["sha256"], StringComparison.Ordinal)) return null;
                Trim();
            }
        }
        return null;
    }
    internal byte[] Export(IReadOnlyCollection<string> pictures)
    {
        lock (Gate)
        {
            RequireRegular(); Trim();
            using var output = new MemoryStream();
            var candidates = Files(DirectoryPath).Where(p => !ImageName.IsMatch(Path.GetFileName(p)) || pictures.Contains(p)).ToList();
            // Reserve ZIP headers. Rebuild without older pictures if incompressible input reaches the limit.
            while (true)
            {
                output.SetLength(0);
                using (var zip = new ZipArchive(output, ZipArchiveMode.Create, true))
                    foreach (var path in candidates)
                    {
                        var entry = zip.CreateEntry(Path.GetFileName(path), CompressionLevel.Optimal);
                        using var stream = entry.Open();
                        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                        input.CopyTo(stream);
                    }
                if (output.Length <= Quota) return output.ToArray();
                var image = candidates.FirstOrDefault(p => ImageName.IsMatch(Path.GetFileName(p)));
                if (image == null) throw new IOException("诊断包超过 16 MiB，请缩小记录后重试。");
                candidates.Remove(image);
            }
        }
    }
    internal string FeedbackText(string reason) => $"功能：{Title}\n运行编号：{RunId}\n版本：{PcrSoftwareUpdateService.InstalledVersion(AppContext.BaseDirectory)}\n" +
        $"错误摘要：{Sanitize(reason)}\n\n复现步骤：\n1. \n\n预期结果：\n\n实际结果：\n\n请附上生成的日志 ZIP；提交前检查截图中的昵称或玩家 ID。";
    internal string GitHubLink(string reason) => IssueUrl + "?title=" + Uri.EscapeDataString($"[{Title}] 运行问题") + "&body=" + Uri.EscapeDataString(FeedbackText(reason));
    internal string MailLink(string reason) => "mailto:" + FeedbackEmail + "?subject=" + Uri.EscapeDataString($"Maa-PCR {Title} 问题反馈") + "&body=" + Uri.EscapeDataString(FeedbackText(reason));
}
