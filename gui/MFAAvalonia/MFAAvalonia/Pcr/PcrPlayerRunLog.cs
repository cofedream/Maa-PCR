using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Newtonsoft.Json;
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
    internal const int FeedbackNoteLimit = 2000;
    private DateTimeOffset? startedAt;
    private string? version;
    private string? outcome;
    private string? summary;
    private long revision;
    internal string Function { get; }
    internal string RunId { get; }
    internal string? Source { get; private set; }
    internal long Revision => Interlocked.Read(ref revision);
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
    internal static PcrPlayerRunLog Begin(string root, string function, string source = "task")
    {
        lock (Gate)
        {
            var log = new PcrPlayerRunLog(root, function, Guid.NewGuid().ToString("N"))
            {
                startedAt = DateTimeOffset.UtcNow,
                Source = source,
                version = PcrSoftwareUpdateService.InstalledVersion(AppContext.BaseDirectory),
                outcome = "running"
            };
            var parent = log.DirectoryPath;
            while (!Directory.Exists(parent)) parent = Path.GetDirectoryName(parent)!;
            if (!PcrLogFiles.IsRegularPath(Path.GetPathRoot(parent)!, parent)) throw new IOException("Linked diagnostic directory rejected");
            Directory.CreateDirectory(log.DirectoryPath);
            log.RequireRegular();
            Active.Add(log.DirectoryPath);
            log.WriteManifest();
            log.Trim();
            return log;
        }
    }
    internal static PcrPlayerRunLog? Latest(string root, string function)
    {
        lock (Gate)
        {
            var folder = Path.Combine(root, ".runtime", "player-logs", function);
            if (!Directory.Exists(folder)) return null;
            var log = Directory.EnumerateDirectories(folder)
                .Where(p => Id.IsMatch(Path.GetFileName(p)) && File.Exists(Path.Combine(p, "manifest.json")))
                .OrderByDescending(Directory.GetLastWriteTimeUtc)
                .Select(p => new PcrPlayerRunLog(root, function, Path.GetFileName(p)))
                .FirstOrDefault(p => PcrLogFiles.IsRegularPath(root, p.DirectoryPath));
            try { log?.ReadManifest(); }
            catch (JsonException) { /* The other diagnostic files are still available. */ }
            return log;
        }
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
        const string secretLabel = @"(?i)((?:password|passwd|authorization|access[_-]?key|token|ticket|credential|cookie|账号|密码|令牌)[""']?\s*[=:：]\s*)";
        const string secretValue = @"(?:""(?:[^""\\\r\n]|\\[^\r\n])*""|'(?:[^'\\\r\n]|\\[^\r\n])*'|[""']?(?:Bearer[ \t]+)?[^""'\s,;}]+)";
        text = Regex.Replace(text, secretLabel + secretValue, "$1[已移除]");
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
            Interlocked.Increment(ref revision);
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
    internal void Finish(string outcome, string? summary = null)
    {
        lock (Gate)
        {
            if (IsFinished) return;
            RequireRegular();
            this.outcome = outcome;
            this.summary = Limit(Sanitize(summary ?? ""), 1200).Trim();
            WriteManifest();
            Interlocked.Increment(ref revision);
            Active.Remove(DirectoryPath); Trim();
            IsFinished = true;
        }
    }
    private void WriteManifest()
    {
        var path = Path.Combine(DirectoryPath, "manifest.json");
        RequireFile(path);
        File.WriteAllText(path, new JObject { ["schema"] = "pcr-player-log-v1", ["function"] = Function,
            ["run_id"] = RunId, ["source"] = Source, ["started_at"] = startedAt?.ToString("O"),
            ["updated_at"] = DateTimeOffset.UtcNow.ToString("O"), ["outcome"] = outcome, ["summary"] = summary,
            ["screenshot_note"] = "截图来源或缺图原因见 events.log，实际随包导出的图片以 ZIP 内文件为准。",
            ["version"] = version }.ToString(), new UTF8Encoding(false));
    }
    private void ReadManifest()
    {
        var path = Path.Combine(DirectoryPath, "manifest.json");
        RequireFile(path);
        using var file = File.OpenText(path);
        using var reader = new JsonTextReader(file) { DateParseHandling = DateParseHandling.None };
        var manifest = JObject.Load(reader);
        string? Text(string key) => manifest[key]?.Type == JTokenType.String ? (string?)manifest[key] : null;
        if (Text("schema") != "pcr-player-log-v1" || Text("run_id") != RunId || Text("function") != Function) return;
        Source = Text("source");
        if (DateTimeOffset.TryParse(Text("started_at"), CultureInfo.InvariantCulture, DateTimeStyles.None, out var time))
            startedAt = time;
        version = Text("version");
        outcome = Text("outcome");
        summary = Limit(Sanitize(Text("summary") ?? ""), 1200).Trim();
        IsFinished = outcome is not null and not "running";
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
                is "failed" or "worker_no_result" or "cleanup_unconfirmed" or "not_started"; }
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
                Interlocked.Increment(ref revision);
                Trim();
            }
        }
        return null;
    }
    internal (byte[] Bytes, int PictureCount, long Revision) Export(IReadOnlyCollection<string> pictures, string? notes)
    {
        lock (Gate)
        {
            RequireRegular(); Trim();
            using var output = new MemoryStream();
            var candidates = Files(DirectoryPath).Where(p => !ImageName.IsMatch(Path.GetFileName(p)) || pictures.Contains(p))
                .OrderBy(File.GetLastWriteTimeUtc).ToList();
            var feedback = FeedbackText(notes);
            // Reserve ZIP headers. Rebuild without older pictures if incompressible input reaches the limit.
            while (true)
            {
                output.SetLength(0);
                using (var zip = new ZipArchive(output, ZipArchiveMode.Create, true))
                {
                    foreach (var path in candidates)
                    {
                        var entry = zip.CreateEntry(Path.GetFileName(path), CompressionLevel.Optimal);
                        using var stream = entry.Open();
                        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                        input.CopyTo(stream);
                    }
                    using var writer = new StreamWriter(zip.CreateEntry("feedback.txt", CompressionLevel.Optimal).Open(), new UTF8Encoding(false));
                    writer.Write(feedback);
                }
                if (output.Length <= Quota)
                    return (output.ToArray(), candidates.Count(p => ImageName.IsMatch(Path.GetFileName(p))), revision);
                var image = candidates.FirstOrDefault(p => ImageName.IsMatch(Path.GetFileName(p)));
                if (image == null) throw new IOException("诊断包超过 16 MiB，请缩小记录后重试。");
                candidates.Remove(image);
            }
        }
    }
    private static string Limit(string text, int limit) => text[..Math.Min(text.Length, limit)];
    internal string RunDetails()
    {
        var sourceName = Source switch { "task" => "任务", "connection" => "设备连接", _ => "未记录" };
        var time = startedAt?.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss zzz") ?? "未记录";
        var end = string.IsNullOrEmpty(outcome) ? "未记录" : outcome == "running" ? "未确认结束" : outcome;
        return $"功能：{Title}\n运行编号：{RunId}\n版本：{version ?? "未记录"}\n来源：{sourceName}\n开始时间：{time}\n结果：{end}"
            + (string.IsNullOrEmpty(summary) ? "" : "\n摘要：" + summary);
    }
    internal string FeedbackText(string? notes)
    {
        var text = Limit(Sanitize(notes ?? ""), FeedbackNoteLimit).Trim();
        return RunDetails() + (text.Length == 0 ? "" : "\n\n复现说明\n" + text);
    }
    private string AttachPrompt => $"运行编号：{RunId}\n请附上已保存的日志 ZIP。提交前请检查日志包和勾选的截图。";
    internal string GitHubLink() => IssueUrl + "?title=" + Uri.EscapeDataString($"[{Title}] 运行问题")
        + "&body=" + Uri.EscapeDataString(AttachPrompt);
    internal string MailLink() => "mailto:" + FeedbackEmail + "?subject=" + Uri.EscapeDataString($"Maa-PCR {Title} 问题反馈")
        + "&body=" + Uri.EscapeDataString(AttachPrompt);
}
