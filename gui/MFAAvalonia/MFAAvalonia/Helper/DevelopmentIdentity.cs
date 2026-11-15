using System;
using System.IO;

namespace MFAAvalonia.Helper;

/// <summary>
/// Development checkouts (and their worktrees) bind the app to a source tree with
/// pcr-repository.txt. Append the checkout's branch to the window title so the
/// main checkout and parallel worktrees can be told apart. Installed packages have
/// no pcr-repository.txt (or no .git) and keep the original title.
/// </summary>
public static class DevelopmentIdentity
{
    private const string BranchPrefix = "ref: refs/heads/";
    private static readonly Lazy<string> CachedTitleSuffix = new(() => Resolve(AppContext.BaseDirectory));

    public static string TitleSuffix => CachedTitleSuffix.Value;

    public static string Resolve(string appDirectory)
    {
        try
        {
            var home = Path.GetFullPath(appDirectory);
            var binding = Path.Combine(home, "pcr-repository.txt");
            if (!File.Exists(binding)) return "";
            var value = File.ReadAllText(binding).Trim();
            if (string.IsNullOrWhiteSpace(value)) return "";
            var repository = Path.GetFullPath(value, home);
            var gitDirectory = ResolveGitDirectory(repository);
            if (gitDirectory is null) return "";
            var branch = ReadHead(gitDirectory);
            if (!string.IsNullOrWhiteSpace(branch)) return " · " + branch;
            // A detached HEAD has no branch; the checkout directory still identifies the window.
            var name = new DirectoryInfo(repository).Name;
            return string.IsNullOrWhiteSpace(name) ? "" : " · " + name;
        }
        catch
        {
            return "";
        }
    }

    private static string? ResolveGitDirectory(string repository)
    {
        var dotGit = Path.Combine(repository, ".git");
        if (Directory.Exists(dotGit)) return dotGit;
        if (!File.Exists(dotGit)) return null;
        // Linked worktrees store "gitdir: <common .git/worktrees/...>" in a .git file.
        var text = File.ReadAllText(dotGit).Trim();
        const string prefix = "gitdir:";
        if (!text.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return null;
        var path = text[prefix.Length..].Trim();
        return path.Length == 0 ? null : Path.GetFullPath(path, repository);
    }

    private static string? ReadHead(string gitDirectory)
    {
        var head = Path.Combine(gitDirectory, "HEAD");
        if (!File.Exists(head)) return null;
        var value = File.ReadAllText(head).Trim();
        if (value.StartsWith(BranchPrefix, StringComparison.Ordinal))
        {
            var branch = value[BranchPrefix.Length..];
            return branch.Length == 0 ? null : branch;
        }
        // Detached HEAD: a short commit id still distinguishes parallel checkouts.
        return value.Length >= 7 ? value[..7] : null;
    }
}
