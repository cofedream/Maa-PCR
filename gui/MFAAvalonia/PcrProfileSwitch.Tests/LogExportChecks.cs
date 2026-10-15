using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.UsersControls;
using MFAAvalonia.Views.UserControls;

internal static class LogExportChecks
{
    internal static void Run(string fixtureDirectory)
    {
        var count = 0;
        void Check(bool ok, string name) { if (!ok) throw new InvalidOperationException(name); count++; }
        var root = Path.Combine(fixtureDirectory, "log-export", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var now = DateTime.UtcNow;
        string Put(string relative, string content, int age = 0)
        {
            var path = Path.GetFullPath(Path.Combine(root, relative));
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, content);
            File.SetLastWriteTimeUtc(path, now.AddDays(-age));
            return path;
        }
        string Hash(string path) => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path)));
        const string area = ".runtime/labyrinth-work/";
        var run = new string('a', 32);
        var old = new string('b', 32);
        var ledger = Put(area + "ledger/transactions.jsonl", "{\"state\":\"in_flight\",\"operation_id\":\"unknown-do-not-resend\"}\n", 90);
        Put(area + "ledger/restart/transactions.jsonl", "{\"state\":\"historical_unknown\"}\n", 90);
        Put(area + "ledger/batch-checkpoint.json", "{\"stage\":\"ready\"}", 90);
        Put(area + $"ledger/runs/{run}/events.jsonl", "{\"stage\":\"finished\",\"outcome\":\"role_reward_readback_unknown\"}\n", 90);
        var trace = Put(area + $"runs/{run}/native/maa/maafw.log", "native recent\n");
        var oldTrace = Put(area + $"runs/{old}/native/maa/maafw.log", "native old protected\n", 90);
        var secret = Put(area + "ledger/credentials.json", "PRIVATE_CONFIG_MUST_NOT_BE_EXPORTED", 90);
        Put(area + $"runs/{run}/native/maa/login.json", "PRIVATE_SDK_MUST_NOT_BE_EXPORTED");
        Put(area + $"runs/{run}/native/maa/on_error/frame.png", "private-image");
        var guiOld = Put("logs/log-20200101.log", "ordinary old\n", 31);
        var guiRecent = Put("logs/log-20200102_001.log", "ordinary recent\n", 29);
        var exactBoundary = Put("logs/log-20200103.log", "boundary\n", 30);
        var locked = Put("logs/log-20200104.log", "busy\n", 45);
        var maaOld = Put("debug/maafw.bak.2026.01.01-01.02.03.4.log", "ordinary maa backup\n", 31);
        var active = Put("debug/maafw.log", "active never removed\n", 90);
        var unrelated = Put("logs/player-notes.log", "unrelated do not delete\n", 90);
        var screenshot = Put("debug/on_error/frame.png", "old screenshot do not delete", 90);
        var protectedFiles = Directory.GetFiles(Path.Combine(root, ".runtime"), "*", SearchOption.AllDirectories)
            .Concat(new[] { guiRecent, exactBoundary, locked, active, unrelated, screenshot }).ToDictionary(path => path, Hash);

        var type = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrLogFiles", true)!;
        const BindingFlags hidden = BindingFlags.Static | BindingFlags.NonPublic;
        object? Call(string name, params object[] args) => type.GetMethod(name, hidden)!.Invoke(null, args);
        var defaults = new ExportLogPackageOptions();
        Check(defaults.IncludeLabyrinthLog && defaults.LabyrinthLogTimeRange == ExportLogTimeRange.Last7Days, "labyrinth_defaults");
        var options = new ExportLogPackageOptions
        {
            IncludeMaaLog = false, IncludeGuiLog = false, IncludeCustomLog = false,
            IncludeOnErrorImages = false, IncludeOtherImages = false
        };
        var select = typeof(FileLogExporter).GetMethod("GetEligibleFiles", hidden)!;
        var combined = (List<FileInfoEx>)select.Invoke(null, [root, defaults])!;
        Check(combined.Any(file => file.FullName == guiRecent) && combined.Any(file => file.FullName == active)
            && combined.Any(file => file.FullName == maaOld) && combined.Any(file => file.FullName == ledger), "combined_export_keeps_gui_current_and_backup_maa_logs");
        var selected = (List<FileInfoEx>)select.Invoke(null, [root, options])!;
        Check(selected.Count == 5, "recent_trace_and_complete_durable_records");
        Check(selected.Any(file => file.FullName == ledger) && selected.Any(file => file.FullName == trace), "unknown_and_trace_present");
        Check(!selected.Any(file => file.FullName == oldTrace || file.FullName == secret || file.IsImage), "no_old_trace_credentials_or_images");
        var all = ((IEnumerable<string>)Call("LabyrinthFiles", root, DateTime.MinValue)!).ToArray();
        Check(all.Length == 6 && all.Contains(oldTrace), "all_time_includes_old_native_trace");
        var none = new ExportLogPackageOptions
        {
            IncludeMaaLog = false, IncludeGuiLog = false, IncludeCustomLog = false, IncludeLabyrinthLog = false,
            IncludeOnErrorImages = false, IncludeOtherImages = false
        };
        Check(((List<FileInfoEx>)select.Invoke(null, [root, none])!).Count == 0, "labyrinth_opt_out");

        var snapshot = Path.Combine(root, "snapshot");
        foreach (var file in selected)
        {
            var target = Path.Combine(snapshot, file.RelativePath!, Path.GetFileName(file.FullName)!);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            typeof(FileLogExporter).GetMethod("CopyFileSnapshot", hidden)!.Invoke(null, [file.FullName, target]);
        }
        var archives = (List<string>)typeof(FileLogExporter).GetMethod("CreateArchiveVolumes", hidden)!.Invoke(null, [snapshot, false])!;
        using (var archive = ZipFile.OpenRead(archives.Single()))
        {
            Check(archive.Entries.Count == 5, "real_zip_entries");
            foreach (var entry in archive.Entries)
            {
                using var stream = entry.Open();
                Check(Convert.ToHexString(SHA256.HashData(stream)) == Hash(Path.Combine(root, entry.FullName)), "zip_source_bytes_match");
            }
        }
        File.Delete(archives.Single());
        Directory.Delete(Path.GetDirectoryName(archives.Single())!);
        var usage = (ValueTuple<long, long>)Call("Usage", root)!;
        Check(usage.Item2 == all.Sum(file => new FileInfo(file).Length), "usage_includes_protected_old_trace");

        using (var busy = new FileStream(locked, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            Call("CleanupOrdinary", root, now, (Action<string>)(_ => { }), (Action<string>)(_ => { }));
        Check(!File.Exists(guiOld) && !File.Exists(maaOld), "ordinary_older_than_30_days_removed");
        Check(protectedFiles.All(file => File.Exists(file.Key) && Hash(file.Key) == file.Value), "all_business_unknown_current_busy_and_other_files_preserved");
        var receipts = Directory.GetFiles(Path.Combine(root, ".runtime/log-maintenance"), "*.jsonl");
        var rows = File.ReadAllLines(receipts.Single());
        Check(rows.Length == 3 && rows[0].Contains("candidates") && rows.Skip(1).All(row => row.Contains("deleted")), "allowlist_and_delete_receipt");

        var outside = Path.Combine(fixtureDirectory, "log-export-outside", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(outside);
        Check(!(bool)Call("IsRegularPath", root, outside)!, "outside_root_rejected");
        Check((bool)Call("IsRegularPath", root + Path.DirectorySeparatorChar, ledger)!, "trailing_root_separator_supported");
        if (OperatingSystem.IsWindows())
        {
            var target = Path.Combine(outside, "target");
            Directory.CreateDirectory(target);
            var sentinel = Path.Combine(target, "log-20200101.log");
            File.WriteAllText(sentinel, "outside sentinel");
            File.SetLastWriteTimeUtc(sentinel, now.AddDays(-90));
            var linkRoot = Path.Combine(root, "linked-root");
            Directory.CreateDirectory(linkRoot);
            var link = Path.Combine(linkRoot, "logs");
            var command = new System.Diagnostics.ProcessStartInfo("cmd.exe")
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true
            };
            foreach (var argument in new[] { "/c", "mklink", "/J", link, target }) command.ArgumentList.Add(argument);
            using var process = System.Diagnostics.Process.Start(command)!;
            process.WaitForExit();
            Check(process.ExitCode == 0, "isolated_junction_created");
            Check(!(bool)Call("IsRegularPath", linkRoot, Path.Combine(link, Path.GetFileName(sentinel)))!, "linked_file_rejected");
            Call("CleanupOrdinary", linkRoot, now, (Action<string>)(_ => { }), (Action<string>)(_ => { }));
            Check(File.ReadAllText(sentinel) == "outside sentinel", "junction_target_not_cleaned");
            // Removing the junction itself never recurses into the target.
            Directory.Delete(link);
        }
        var language = new MFAAvalonia.Localization.MFAResxLangPlugin();
        Lang.Avalonia.I18nManager.Instance.Register(language, defaultCulture: new System.Globalization.CultureInfo("zh-CN"), error: out var languageError);
        if (!language.IsLoaded) language.Load(new System.Globalization.CultureInfo("zh-CN"));
        Application.Current!.Styles.Insert(0, new Avalonia.Themes.Fluent.FluentTheme());
        var vm = new ExportLogDialogViewModel(null!);
        vm.IncludeMaaLog = vm.IncludeGuiLog = vm.IncludeCustomLog = vm.IncludeOnErrorImages = vm.IncludeOtherImages = vm.IncludeVisionImages = false;
        Check(vm.CanStartExport, "labyrinth_only_enables_export");
        vm.IncludeLabyrinthLog = false;
        Check(!vm.CanStartExport, "all_options_off_disables_export");
        var view = new ExportLogDialogView { DataContext = vm, Background = Avalonia.Media.Brushes.White };
        Check(view.Content != null, "compiled_export_view_loaded");
        vm.IncludeLabyrinthLog = true;
        var window = new Window { Width = 580, Height = 740, Content = view, Background = Avalonia.Media.Brushes.White,
            Foreground = Avalonia.Media.Brushes.Black, RequestedThemeVariant = Avalonia.Styling.ThemeVariant.Light };
        window.Show();
        System.Threading.Thread.Sleep(250);
        Dispatcher.UIThread.RunJobs();
        vm.StorageUsage = $"普通日志：{usage.Item1 / 1048576d:F1} MiB；黎明界独立记录：{usage.Item2 / 1048576d:F1} MiB（隔离夹具）。";
        window.UpdateLayout();
        Check(view.GetVisualDescendants().OfType<CheckBox>().Any(box => box.Content?.ToString() == "黎明界运行日志" && box.IsChecked == true), "labyrinth_checkbox_bound");
        using (var frame = new RenderTargetBitmap(new PixelSize(580, 740)))
        {
            frame.Render(view);
            frame.Save(Path.Combine(root, "export-dialog.png"));
        }
        window.Close();
        Console.WriteLine($"PCR log export/retention checks passed: {count}; synthetic fixtures only, no player cleanup");
    }
}
