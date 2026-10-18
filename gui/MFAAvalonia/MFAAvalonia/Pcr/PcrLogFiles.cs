using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace MFAAvalonia.Pcr;

internal static class PcrLogFiles
{
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
        (OrdinaryFiles(dataRoot).Concat(DiagnosticImages(dataRoot)).Sum(file => new FileInfo(file).Length),
            LabyrinthFiles(labyrinthRoot, DateTime.MinValue).Sum(file => new FileInfo(file).Length));

    private static IEnumerable<string> DiagnosticImages(string root) =>
        new[] { "debug/on_error", "debug/vision" }.SelectMany(folder => Files(root, folder, true))
            .Where(file => Path.GetExtension(file).ToLowerInvariant() is ".png" or ".jpg" or ".jpeg" or ".bmp" or ".webp" or ".gif");
}
