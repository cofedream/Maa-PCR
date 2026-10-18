using System;
using System.IO;

namespace MFAAvalonia.Pcr;

/// <summary>Business files and interpreter belonging to this app or its development checkout.</summary>
public static class PcrRuntimePaths
{
    public static string ResolveRepository(string appDirectory)
    {
        var home = Path.GetFullPath(appDirectory);
        var binding = Path.Combine(home, "pcr-repository.txt");
        var root = home;
        if (File.Exists(binding))
        {
            var value = File.ReadAllText(binding).Trim();
            if (string.IsNullOrWhiteSpace(value))
                throw new InvalidOperationException("Maa PCR 启动目录配置为空，请重新解压完整程序包。");
            // A relative binding belongs to the executable, regardless of the shortcut's cwd.
            root = Path.GetFullPath(value, home);
        }
        foreach (var file in new[] { "scripts/validate-alpha-config.py", "scripts/start-alpha-owned-agent.py", "src/pcr_maa/agent_entry.py" })
            if (!File.Exists(Path.Combine(root, file)))
                throw new InvalidOperationException("Maa PCR 程序文件不完整，请重新解压完整程序包。缺少：" + file);
        return root;
    }

    /// <summary>业务数据所在目录：开发模式取自 pcr-repository.txt，打包模式就是安装目录。
    /// 不校验程序完整性，也不抛异常：导出/清理这类只读场景需要即使程序不完整也能定位。</summary>
    public static string ResolveDataRepository(string appDirectory)
    {
        var home = Path.GetFullPath(appDirectory);
        try
        {
            var binding = Path.Combine(home, "pcr-repository.txt");
            if (!File.Exists(binding)) return home;
            var value = File.ReadAllText(binding).Trim();
            return string.IsNullOrWhiteSpace(value) ? home : Path.GetFullPath(value, home);
        }
        catch
        {
            return home;
        }
    }

    // A interrupted update may leave mixed program files. Only its updater or
    // explicit backup restoration can resolve this marker; Start never removes it.
    internal static void RequireUpdateComplete(string appDirectory, string repository)
    {
        foreach (var directory in new[] { appDirectory, repository })
        {
            if (string.IsNullOrWhiteSpace(directory)) continue;
            var marker = Path.Combine(Path.GetFullPath(directory), ".runtime", "player-maintenance", "update-in-progress.json");
            if (File.Exists(marker) || Directory.Exists(marker))
                throw new InvalidOperationException("程序更新尚未完成，请按更新备份恢复程序；保留设置和账本。[player_update_incomplete]");
        }
    }

    public static string ResolvePython(string repository)
    {
        var root = Path.GetFullPath(repository);
        var bundled = Path.Combine(root, "runtime", "python", "python.exe");
        if (File.Exists(bundled)) return bundled;
        var development = Path.Combine(root, ".venv", "Scripts", "python.exe");
        if (File.Exists(development)) return development;
        throw new InvalidOperationException("Maa PCR 的 Python 运行环境缺失，请重新解压完整程序包。");
    }
}
