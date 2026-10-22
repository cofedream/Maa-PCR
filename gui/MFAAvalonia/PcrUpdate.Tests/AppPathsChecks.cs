using System;
using System.IO;
using System.Linq;
using MFAAvalonia.Helper;

internal static class AppPathsChecks
{
    internal static void Run()
    {
        using var output = new StringWriter();
        var stdout = Console.Out;
        string initialized;
        try
        {
            Console.SetOut(output);
            LoggerHelper.InitializeLogger();
            initialized = output.ToString();
            AppPaths.Initialize();
            AppPaths.Initialize();
            if (output.ToString() != initialized)
                throw new InvalidOperationException("app_paths_repeated_initialization_logged_again");
        }
        finally
        {
            LoggerHelper.DisposeLogger();
            Console.SetOut(stdout);
        }
        if (initialized.Split('\n').Count(line => line.Contains("应用安装目录：")) != 1)
            throw new InvalidOperationException("app_paths_configuration_logging_reentered_initialization");
        if (initialized.Contains("备份文件数量"))
            throw new InvalidOperationException("app_paths_startup_still_counts_backups");
        if (!new[] { AppPaths.ConfigDirectory, AppPaths.InstancesDirectory, AppPaths.ResourceDirectory,
                AppPaths.AgentDirectory, AppPaths.LogsDirectory, AppPaths.TempDirectory, AppPaths.BackupDirectory }
            .All(Directory.Exists))
            throw new InvalidOperationException("app_paths_required_directory_missing");
        Console.WriteLine("PCR app path initialization checks passed: 4");
    }
}
