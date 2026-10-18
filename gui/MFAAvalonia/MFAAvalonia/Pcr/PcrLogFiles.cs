using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MFAAvalonia.Pcr;

internal static class PcrLogFiles
{
    internal const int RetentionDays = 30;
    private static readonly Regex GuiName = new(@"^log-\d{8}(?:_\d{3,})?\.log$", RegexOptions.IgnoreCase);
    private static readonly Regex MaaBackupName = new(@"^(?:maa|maafw)\.bak\.[0-9.-]+\.log$", RegexOptions.IgnoreCase);
    private static readonly Regex RunId = new(@"^[0-9a-f]{32}$");

    // Refuse links at every component, including the root of a recursive scan.
    internal static bool IsRegularPath(string root, string path)
    {
        root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
        path = Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));
        var relative = Path.GetRelativePath(root, path);
        if (Path.IsPathRooted(relative) || relative == ".." || relative.StartsWith(".." + Path.DirectorySeparatorChar)) return false;
        for (var current = path; ; current = Path.GetDirectoryName(current)!)
        {
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) return false;
            if (string.Equals(current, root, StringComparison.OrdinalIgnoreCase)) return true;
        }
    }

    private static IEnumerable<string> Files(string root, string relative, bool recursive = false)
    {
        var directory = Path.GetFullPath(Path.Combine(root, relative));
        if (!Directory.Exists(directory) || !IsRegularPath(root, directory)) return [];
        return Directory.EnumerateFiles(directory, "*", new EnumerationOptions
        {
            RecurseSubdirectories = recursive,
            AttributesToSkip = FileAttributes.ReparsePoint,
            IgnoreInaccessible = false
        });
    }

    internal static IEnumerable<string> LabyrinthFiles(string root, DateTime nativeCutoffUtc)
    {
        const string area = ".runtime/labyrinth-work/";
        foreach (var name in new[] { "ledger/transactions.jsonl", "ledger/restart/transactions.jsonl", "ledger/batch-checkpoint.json" })
        {
            var path = Path.GetFullPath(Path.Combine(root, area + name));
            if (File.Exists(path) && IsRegularPath(root, path)) yield return path;
        }
        foreach (var file in Files(root, area + "ledger/runs", true))
        {
            var relative = Path.GetRelativePath(Path.Combine(root, area + "ledger/runs"), file).Split(Path.DirectorySeparatorChar);
            if (relative.Length == 2 && RunId.IsMatch(relative[0]) && relative[1] == "events.jsonl") yield return file;
        }
        foreach (var file in Files(root, area + "runs", true))
        {
            var relative = Path.GetRelativePath(Path.Combine(root, area + "runs"), file).Split(Path.DirectorySeparatorChar);
            if (relative.Length == 4 && RunId.IsMatch(relative[0]) && relative[1] == "native" && relative[2] == "maa"
                && (relative[3].Equals("maafw.log", StringComparison.OrdinalIgnoreCase) || MaaBackupName.IsMatch(relative[3]))
                && File.GetLastWriteTimeUtc(file) >= nativeCutoffUtc) yield return file;
        }
    }

    internal static IEnumerable<string> OrdinaryFiles(string root) =>
        Files(root, "logs").Where(file => GuiName.IsMatch(Path.GetFileName(file)))
            .Concat(Files(root, "debug").Where(file => MaaBackupName.IsMatch(Path.GetFileName(file))
                || Path.GetFileName(file).Equals("maafw.log", StringComparison.OrdinalIgnoreCase)
                || Path.GetFileName(file).Equals("maa.log", StringComparison.OrdinalIgnoreCase)));

    // 普通日志在安装目录，黎明界账本/原生日志在业务目录（开发模式下是仓库），两者必须分开算。
    internal static (long Ordinary, long Labyrinth) Usage(string dataRoot, string labyrinthRoot) =>
        (OrdinaryFiles(dataRoot).Sum(file => new FileInfo(file).Length),
            LabyrinthFiles(labyrinthRoot, DateTime.MinValue).Sum(file => new FileInfo(file).Length));

    internal static void CleanupOrdinary(string root, DateTime nowUtc, Action<string> info, Action<string> warning)
    {
        try
        {
            var cutoff = nowUtc.AddDays(-RetentionDays);
            var candidates = OrdinaryFiles(root).Where(file =>
                !Path.GetFileName(file).Equals("maafw.log", StringComparison.OrdinalIgnoreCase)
                && !Path.GetFileName(file).Equals("maa.log", StringComparison.OrdinalIgnoreCase))
                .Select(file => new FileInfo(file)).Where(file => file.LastWriteTimeUtc < cutoff).ToArray();
            if (candidates.Length == 0) return;
            var audit = Path.Combine(root, ".runtime", "log-maintenance");
            var existingParent = Directory.Exists(audit) ? audit : Directory.Exists(Path.GetDirectoryName(audit)) ? Path.GetDirectoryName(audit)! : root;
            if (!IsRegularPath(root, existingParent)) return;
            Directory.CreateDirectory(audit);
            if (!IsRegularPath(root, audit)) return;
            var receipt = Path.Combine(audit, $"cleanup-{Guid.NewGuid():N}.jsonl");
            // Save the bounded allowlist before deleting; never traverse a business evidence directory.
            using var record = new StreamWriter(new FileStream(receipt, FileMode.CreateNew, FileAccess.Write, FileShare.Read));
            record.WriteLine(JsonSerializer.Serialize(new { retention_days = RetentionDays, cutoff_utc = cutoff,
                candidates = candidates.Select(file => new { path = Path.GetRelativePath(root, file.FullName), bytes = file.Length, modified_utc = file.LastWriteTimeUtc }) }));
            record.Flush();
            foreach (var file in candidates)
            {
                try
                {
                    if (!IsRegularPath(root, file.FullName)) continue;
                    // An active writer prevents this exclusive read; leave that file for a later startup.
                    using (var held = new FileStream(file.FullName, FileMode.Open, FileAccess.Read, FileShare.None))
                    {
                        if (held.Length != file.Length || File.GetLastWriteTimeUtc(file.FullName) != file.LastWriteTimeUtc) continue;
                    }
                    File.Delete(file.FullName);
                    record.WriteLine(JsonSerializer.Serialize(new { deleted = Path.GetRelativePath(root, file.FullName), bytes = file.Length }));
                    record.Flush();
                    info($"已清理超过 {RetentionDays} 天的普通日志：{Path.GetFileName(file.FullName)}");
                }
                catch (IOException) { warning("旧日志正在使用或无法访问，本次保留。"); }
                catch (UnauthorizedAccessException) { warning("旧日志无删除权限，本次保留。"); }
            }
        }
        catch (Exception) { warning("普通日志清理未完成，原业务记录保持不变。"); }
    }
}
