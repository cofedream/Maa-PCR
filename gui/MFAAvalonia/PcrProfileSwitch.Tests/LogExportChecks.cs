using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.UsersControls;
using MFAAvalonia.ViewModels.Windows;
using MFAAvalonia.Views.UserControls;

internal static class LogExportChecks
{
    internal static void Run(string fixtureDirectory)
    {
        var count = 0;
        void Check(bool ok, string name) { if (!ok) throw new InvalidOperationException(name); count++; }
        var root = Path.Combine(fixtureDirectory, "log-export", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        RunPublishChecks(Path.Combine(root, "publish"), Check);
        RunSingleArchiveChecks(Path.Combine(root, "single-archive"), Check);
        RunRotationChecks(Path.Combine(root, "rotation"), Check);
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
        var guiRecent = Put("logs/log-20200102_001.log", "ordinary recent\n", 6);
        var exactBoundary = Put("logs/log-20200103.log", "boundary\n", 7);
        var locked = Put("logs/log-20200104.log", "busy\n", 45);
        var maaOld = Put("debug/maafw.bak.2026.01.01-01.02.03.4.log", "ordinary maa backup\n", 31);
        var active = Put("debug/maafw.log", "active never removed\n", 90);
        var unrelated = Put("logs/player-notes.log", "unrelated do not delete\n", 90);
        var screenshot = Put("debug/on_error/frame.png", "recent screenshot do not delete", 6);
        var oldScreenshot = Put("debug/vision/old.png", "expired diagnostic image", 8);
        var protectedFiles = Directory.GetFiles(Path.Combine(root, ".runtime"), "*", SearchOption.AllDirectories)
            .Concat(new[] { guiOld, guiRecent, exactBoundary, locked, maaOld, active, unrelated, screenshot, oldScreenshot }).ToDictionary(path => path, Hash);

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
        var combined = (List<FileInfoEx>)select.Invoke(null, [root, root, defaults])!;
        Check(combined.Any(file => file.FullName == guiRecent) && combined.Any(file => file.FullName == active)
            && combined.Any(file => file.FullName == maaOld) && combined.Any(file => file.FullName == ledger), "combined_export_keeps_gui_current_and_backup_maa_logs");
        var selected = (List<FileInfoEx>)select.Invoke(null, [root, root, options])!;
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
        Check(((List<FileInfoEx>)select.Invoke(null, [root, root, none])!).Count == 0, "labyrinth_opt_out");

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
        var usage = (ValueTuple<long, long>)Call("Usage", root, root)!;
        Check(usage.Item2 == all.Sum(file => new FileInfo(file).Length), "usage_includes_protected_old_trace");

        var separateRoot = Path.Combine(fixtureDirectory, "separate-log-roots", Guid.NewGuid().ToString("N"));
        var appRoot = Path.Combine(separateRoot, "app");
        var businessRoot = Path.Combine(separateRoot, "business");
        var separateGui = Path.GetFullPath(Path.Combine(appRoot, "logs/log-20261018.log"));
        var separateImage = Path.GetFullPath(Path.Combine(appRoot, "debug/on_error/frame.png"));
        var separateLedger = Path.GetFullPath(Path.Combine(businessRoot, area + "ledger/transactions.jsonl"));
        var separateCheckpoint = Path.GetFullPath(Path.Combine(businessRoot, area + "ledger/batch-checkpoint.json"));
        var separateSecret = Path.GetFullPath(Path.Combine(businessRoot, area + "ledger/credentials.json"));
        var separateTrace = Path.GetFullPath(Path.Combine(businessRoot, area + $"runs/{run}/native/maa/maafw.log"));
        foreach (var path in new[] { separateGui, separateImage, separateLedger, separateCheckpoint, separateSecret, separateTrace })
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, path == separateLedger ? "{\"state\":\"in_flight\",\"unknown\":true}\n" : "fixture " + Path.GetFileName(path));
        }
        var separateBefore = Directory.GetFiles(separateRoot, "*", SearchOption.AllDirectories).ToDictionary(path => path, Hash);
        var separateOptions = new ExportLogPackageOptions
        {
            IncludeMaaLog = false, IncludeGuiLog = true, IncludeCustomLog = false, IncludeLabyrinthLog = true,
            IncludeOnErrorImages = true, IncludeOtherImages = false
        };
        var separateFiles = (List<FileInfoEx>)select.Invoke(null, [appRoot, businessRoot, separateOptions])!;
        Check(separateFiles.Count == 5 && separateFiles.Any(file => file.FullName == separateLedger)
            && !separateFiles.Any(file => file.FullName == separateSecret), "separate_roots_keep_unknown_ledger_without_credentials");
        Check(separateFiles.All(file => !(file.RelativePath ?? "").Split(Path.DirectorySeparatorChar).Contains("..")
            && (bool)Call("IsRegularPath", file.Root!, file.FullName!)!), "separate_roots_have_safe_archive_paths");
        var separateUsage = (ValueTuple<long, long>)Call("Usage", appRoot, businessRoot)!;
        Check(separateUsage.Item1 == new FileInfo(separateGui).Length + new FileInfo(separateImage).Length
            && separateUsage.Item2 == new FileInfo(separateLedger).Length + new FileInfo(separateCheckpoint).Length + new FileInfo(separateTrace).Length,
            "separate_roots_usage_includes_images_and_business_records");
        var separateSnapshot = Path.Combine(separateRoot, "snapshot");
        foreach (var file in separateFiles)
        {
            var destination = Path.Combine(separateSnapshot, file.RelativePath!, Path.GetFileName(file.FullName)!);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            typeof(FileLogExporter).GetMethod("CopyFileSnapshot", hidden)!.Invoke(null, [file.FullName, destination]);
        }
        var separateArchives = (List<string>)typeof(FileLogExporter).GetMethod("CreateArchiveVolumes", hidden)!.Invoke(null, [separateSnapshot, false])!;
        using (var archive = ZipFile.OpenRead(separateArchives.Single()))
        {
            Check(archive.Entries.Count == 5 && !archive.Entries.Any(entry => entry.FullName.Contains("credentials")), "separate_roots_zip_allowlist");
            foreach (var entry in archive.Entries)
            {
                var sourceRoot = entry.FullName.StartsWith(".runtime/", StringComparison.Ordinal) ? businessRoot : appRoot;
                using var stream = entry.Open();
                Check(Convert.ToHexString(SHA256.HashData(stream)) == Hash(Path.Combine(sourceRoot, entry.FullName)), "separate_roots_zip_source_bytes_match");
            }
        }
        Check(separateBefore.All(file => Hash(file.Key) == file.Value), "separate_roots_export_preserves_all_source_bytes");

        Check(protectedFiles.All(file => File.Exists(file.Key) && Hash(file.Key) == file.Value), "export_preserves_all_source_and_business_files");
        Check(usage.Item1 == ((IEnumerable<string>)Call("OrdinaryFiles", root)!).Sum(file => new FileInfo(file).Length)
            + new FileInfo(screenshot).Length + new FileInfo(oldScreenshot).Length, "usage_includes_diagnostic_images");

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
            Check(!((IEnumerable<string>)Call("OrdinaryFiles", linkRoot)!).Any(), "linked_log_directory_not_scanned");
            Check(File.ReadAllText(sentinel) == "outside sentinel", "junction_target_not_cleaned");
            // Removing the junction itself never recurses into the target.
            Directory.Delete(link);
        }
        var language = new MFAAvalonia.Localization.MFAResxLangPlugin();
        Lang.Avalonia.I18nManager.Instance.Register(language, defaultCulture: new System.Globalization.CultureInfo("zh-CN"), error: out var languageError);
        if (!language.IsLoaded) language.Load(new System.Globalization.CultureInfo("zh-CN"));
        Application.Current!.Styles.Insert(0, new Avalonia.Themes.Fluent.FluentTheme());
        ExportLogPackageOptions Options(ExportLogDialogViewModel model) => (ExportLogPackageOptions)typeof(ExportLogDialogViewModel)
            .GetMethod("BuildOptions", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(model, null)!;
        var dailyVm = new ExportLogDialogViewModel(null!, isLabyrinthProfile: false);
        var vm = new ExportLogDialogViewModel(null!, isLabyrinthProfile: true);
        Check(!dailyVm.IsLabyrinthProfile && !dailyVm.IncludeLabyrinthLog && dailyVm.IncludeGuiLog && dailyVm.IncludeMaaLog,
            "daily_defaults_exclude_labyrinth_and_keep_gui_maa");
        Check(vm.IsLabyrinthProfile && vm.IncludeLabyrinthLog && vm.IncludeGuiLog && vm.IncludeMaaLog
            && vm.SelectedLabyrinthLogTimeRange.Value == ExportLogTimeRange.All, "labyrinth_defaults_include_all_diagnostics_gui_maa");
        foreach (var model in new[] { dailyVm, vm })
        {
            var profile = model.IsLabyrinthProfile ? "labyrinth" : "daily";
            var files = (List<FileInfoEx>)select.Invoke(null, [root, root, Options(model)])!;
            Check(files.Any(file => file.FullName == guiRecent) && files.Any(file => file.FullName == active), profile + "_keeps_gui_maa");
            Check(model.IsLabyrinthProfile ? all.All(path => files.Any(file => file.FullName == path))
                : !files.Any(file => file.FullName!.StartsWith(Path.Combine(root, ".runtime"))), profile + "_diagnostic_scope");
            Check(!files.Any(file => file.FullName == secret || file.FullName!.EndsWith("login.json")), profile + "_excludes_credentials");
            var profileSnapshot = Path.Combine(root, profile + "-snapshot");
            foreach (var file in files)
            {
                var target = Path.Combine(profileSnapshot, file.RelativePath!, Path.GetFileName(file.FullName)!);
                Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                typeof(FileLogExporter).GetMethod("CopyFileSnapshot", hidden)!.Invoke(null, [file.FullName, target]);
            }
            var profileArchives = (List<string>)typeof(FileLogExporter).GetMethod("CreateArchiveVolumes", hidden)!
                .Invoke(null, [profileSnapshot, false])!;
            using var archive = ZipFile.OpenRead(profileArchives.Single());
            Check(archive.Entries.Count == files.Count, profile + "_zip_contains_all_selected_files");
            foreach (var entry in archive.Entries)
            {
                using var stream = entry.Open();
                Check(Convert.ToHexString(SHA256.HashData(stream)) == Hash(Path.Combine(root, entry.FullName)), profile + "_zip_source_bytes_match");
            }
        }
        vm.IncludeMaaLog = vm.IncludeGuiLog = vm.IncludeCustomLog = vm.IncludeOnErrorImages = vm.IncludeOtherImages = vm.IncludeVisionImages = false;
        Check(vm.CanStartExport, "labyrinth_only_enables_export");
        vm.IncludeLabyrinthLog = false;
        Check(!vm.CanStartExport, "all_options_off_disables_export");
        var view = new ExportLogDialogView { DataContext = vm, Background = Avalonia.Media.Brushes.White };
        Check(view.Content != null, "compiled_export_view_loaded");
        vm.IncludeMaaLog = true;
        vm.IsExporting = true;
        Check(!vm.CanStartExport, "export_disables_repeat_export");
        vm.IsExporting = false;
        Check(vm.CanStartExport, "export_reenabled");
        vm.IncludeMaaLog = vm.IncludeGuiLog = vm.IncludeCustomLog = vm.IncludeOnErrorImages = vm.IncludeOtherImages = vm.IncludeLabyrinthLog = true;
        var window = new Window { Width = 580, Height = 740, Content = view, Background = Avalonia.Media.Brushes.White,
            Foreground = Avalonia.Media.Brushes.Black, RequestedThemeVariant = Avalonia.Styling.ThemeVariant.Light };
        window.Show();
        System.Threading.Thread.Sleep(250);
        Dispatcher.UIThread.RunJobs();
        vm.StorageUsage = $"普通日志和诊断截图：{usage.Item1 / 1048576d:F1} MiB；黎明界独立记录：{usage.Item2 / 1048576d:F1} MiB（隔离夹具）。";
        window.UpdateLayout();
        Check(view.GetVisualDescendants().OfType<CheckBox>().Any(box => box.Content?.ToString() == "黎明界运行日志" && box.IsChecked == true), "labyrinth_checkbox_bound");
        var labyrinthCheckbox = view.GetVisualDescendants().OfType<CheckBox>().Single(box => box.Content?.ToString() == "黎明界运行日志");
        var labyrinthTimeRange = view.GetVisualDescendants().OfType<ComboBox>()
            .Single(box => Avalonia.Automation.AutomationProperties.GetName(box) == "黎明界原生日志时间范围");
        Check(labyrinthCheckbox.IsEffectivelyVisible && labyrinthTimeRange.IsEffectivelyVisible
            && ((ExportLogTimeRangeOption)labyrinthTimeRange.SelectedItem!).Value == ExportLogTimeRange.All, "labyrinth_options_visible_with_all_time_selected");
        Check(!view.GetVisualDescendants().OfType<Button>().Any(button => button.Content?.ToString()?.Contains("清理") == true), "no_manual_cleanup_button");
        using (var frame = new RenderTargetBitmap(new PixelSize(580, 740)))
        {
            frame.Render(view);
            frame.Save(Path.Combine(root, "export-dialog.png"));
        }
        view.DataContext = dailyVm;
        dailyVm.StorageUsage = $"普通日志和诊断截图：{usage.Item1 / 1048576d:F1} MiB（隔离夹具）。";
        Dispatcher.UIThread.RunJobs();
        window.UpdateLayout();
        Check(!labyrinthCheckbox.IsEffectivelyVisible && !labyrinthTimeRange.IsEffectivelyVisible, "daily_hides_labyrinth_checkbox_and_time_range");
        Check(view.GetVisualDescendants().OfType<CheckBox>().Count(box => box.IsEffectivelyVisible && box.IsChecked == true
            && (box.Content?.ToString() == "gui.log" || box.Content?.ToString() == "maa.log")) == 2, "daily_gui_maa_defaults_visible");
        using (var frame = new RenderTargetBitmap(new PixelSize(580, 740)))
        {
            frame.Render(view);
            frame.Save(Path.Combine(root, "export-dialog-daily.png"));
        }
        dailyVm.IncludeMaaLog = dailyVm.IncludeGuiLog = dailyVm.IncludeCustomLog = dailyVm.IncludeOnErrorImages = dailyVm.IncludeOtherImages = dailyVm.IncludeVisionImages = false;
        dailyVm.IncludeLabyrinthLog = true;
        Check(!dailyVm.CanStartExport && !Options(dailyVm).IncludeLabyrinthLog, "daily_ignores_hidden_labyrinth_option");
        window.Close();
        Console.WriteLine($"PCR log export/retention checks passed: {count}; synthetic fixtures only, no player cleanup");
    }

    private static void RunRotationChecks(string root, Action<bool, string> check)
    {
        AppPaths.Initialize();
        const BindingFlags hidden = BindingFlags.Static | BindingFlags.NonPublic;
        var logDirectory = typeof(AppPaths).GetField("_logsDirectory", hidden)!;
        var logger = typeof(LoggerHelper).GetField("_logger", hidden)!;
        var cache = (System.Collections.IList)typeof(LoggerHelper).GetField("_logCache", hidden)!.GetValue(null)!;
        var savedDirectory = logDirectory.GetValue(null);
        var savedLogger = logger.GetValue(null);
        var savedCache = cache.Cast<object>().ToArray();
        var savedConsole = Console.Out;
        Directory.CreateDirectory(root);
        var first = Path.Combine(root, $"log-{DateTime.Now:yyyyMMdd}.log");
        var oldest = Path.Combine(root, "log-20000101.log");
        var previous = Path.Combine(root, "log-20000102.log");
        File.WriteAllText(oldest, "oldest GUI log");
        File.WriteAllText(previous, "previous GUI log");
        var unrelated = Path.Combine(root, "player-notes.log");
        File.WriteAllText(unrelated, "preserve");
        var nested = Path.Combine(root, "business", "log-20200101.log");
        Directory.CreateDirectory(Path.GetDirectoryName(nested)!);
        File.WriteAllText(nested, "unknown-do-not-resend");
        string[] Logs() => Directory.GetFiles(root, "log-*.log").OrderBy(path => path).ToArray();
        void Size(string path, long bytes)
        {
            using var file = new FileStream(path, FileMode.OpenOrCreate, FileAccess.Write);
            file.SetLength(bytes);
        }
        try
        {
            cache.Clear();
            logger.SetValue(null, null);
            logDirectory.SetValue(null, root);
            Console.SetOut(TextWriter.Null);
            Size(first, 10 * 1024 * 1024 + 1024);
            LoggerHelper.InitializeLogger();
            LoggerHelper.Info("below-64-MiB");
            LoggerHelper.DisposeLogger();
            check(Logs().Contains(first) && new FileInfo(first).Length > 10 * 1024 * 1024 + 1024,
                "logger_keeps_writing_above_old_10_MiB_limit");
            check(Logs().Length == 2 && !File.Exists(oldest) && File.Exists(previous),
                "logger_applies_two_file_limit_to_existing_dated_logs");
            Size(first, 64 * 1024 * 1024 - 1024);
            LoggerHelper.InitializeLogger();
            LoggerHelper.Info(new string('x', 2048));
            LoggerHelper.Info("after-first-roll");
            LoggerHelper.DisposeLogger();
            var second = Logs().Single(path => path != first);
            check(Logs().Length == 2 && new FileInfo(first).Length >= 64 * 1024 * 1024
                && new FileInfo(first).Length < 64 * 1024 * 1024 + 4096 && !File.Exists(previous)
                && File.ReadAllText(second).Contains("after-first-roll"), "logger_rolls_at_64_MiB_during_writing_and_retains_previous_file");
            Size(second, 64 * 1024 * 1024);
            LoggerHelper.InitializeLogger();
            LoggerHelper.Info("after-second-roll");
            LoggerHelper.DisposeLogger();
            var remaining = Logs();
            check(remaining.Length == 2 && !File.Exists(first) && remaining.Contains(second)
                && File.ReadAllText(remaining.Single(path => path != second)).Contains("after-second-roll"),
                "logger_retains_only_two_files_after_restart_and_rotation");
            check(File.ReadAllText(unrelated) == "preserve" && File.ReadAllText(nested) == "unknown-do-not-resend",
                "logger_retention_preserves_unrelated_and_nested_business_files");
        }
        finally
        {
            LoggerHelper.DisposeLogger();
            logger.SetValue(null, savedLogger);
            logDirectory.SetValue(null, savedDirectory);
            cache.Clear();
            foreach (var item in savedCache) cache.Add(item);
            Console.SetOut(savedConsole);
        }
    }

    private static void RunSingleArchiveChecks(string root, Action<bool, string> check)
    {
        var input = Path.Combine(root, "input");
        var images = Path.Combine(input, "debug", "on_error");
        var output = Path.Combine(root, "output");
        Directory.CreateDirectory(images);
        Directory.CreateDirectory(output);
        var bytes = new byte[12_000_000];
        new Random(42).NextBytes(bytes);
        for (var index = 0; index < 3; index++)
            File.WriteAllBytes(Path.Combine(images, $"frame{index}.png"), bytes);
        File.WriteAllText(Path.Combine(input, "debug", "maafw.log"), "complete log\n");
        var expected = Directory.GetFiles(input, "*", SearchOption.AllDirectories).ToDictionary(
            path => Path.GetRelativePath(input, path).Replace('\\', '/'),
            path => SHA256.HashData(File.ReadAllBytes(path)));
        var dataRoot = typeof(AppPaths).GetProperty(nameof(AppPaths.DataRoot))!;
        AppPaths.Initialize();
        var savedRoot = AppPaths.DataRoot;
        var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
        cache.TryGetValue(typeof(RootViewModel), out var savedViewModel);
        cache.TryGetValue(typeof(SukiUI.Toasts.ISukiToastManager), out var savedToasts);
        cache[typeof(SukiUI.Toasts.ISukiToastManager)] = new Lazy<object>(() => new SukiUI.Toasts.SukiToastManager());
        cache[typeof(RootViewModel)] = new Lazy<object>(() => RuntimeHelpers.GetUninitializedObject(typeof(RootViewModel)));
        dataRoot.SetValue(null, input);
        try
        {
            foreach (var local in new[] { true, false })
            {
                var chosen = Path.Combine(output, local ? "local.zip" : "document.zip");
                var sibling = Path.Combine(output, Path.GetFileNameWithoutExtension(chosen) + "-part99.zip");
                File.WriteAllText(sibling, "previous export must remain");
                var file = DispatchProxy.Create<IStorageFile, LogExportStorageProxy>();
                var storageFile = (LogExportStorageProxy)(object)file;
                storageFile.PathValue = local ? new Uri(chosen) : new Uri("content://fixture/document.zip");
                storageFile.WritePath = chosen;
                var provider = DispatchProxy.Create<IStorageProvider, LogExportStorageProxy>();
                ((LogExportStorageProxy)(object)provider).SaveFile = file;
                var export = Task.Run(() => FileLogExporter.CompressRecentLogs(provider));
                Pump(export);
                var result = export.GetAwaiter().GetResult();
                check(result == ExportLogResult.Success, $"single_zip_export_success_{local}");
                check(File.Exists(chosen) && new FileInfo(chosen).Length > 23_750_000, $"one_large_zip_at_selected_path_{local}");
                check(Directory.GetFiles(output, "*-part*.zip").All(path => File.ReadAllText(path) == "previous export must remain"), "existing_sibling_archives_preserved");
                check(Directory.GetFiles(output).Length == (local ? 2 : 4), "no_extra_export_files");
                using var zip = ZipFile.OpenRead(chosen);
                check(zip.Entries.Count == expected.Count, "single_zip_contains_all_files");
                foreach (var entry in zip.Entries)
                {
                    using var stream = entry.Open();
                    check(expected[entry.FullName].SequenceEqual(SHA256.HashData(stream)), "single_zip_preserves_file_bytes");
                }
            }
            check(expected.All(pair => pair.Value.SequenceEqual(SHA256.HashData(File.ReadAllBytes(Path.Combine(input, pair.Key))))), "export_preserves_source_files");
            var gate = (System.Threading.SemaphoreSlim)typeof(FileLogExporter)
                .GetField("ExportSemaphore", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
            gate.Wait();
            try
            {
                var blockedExport = Task.Run(() => FileLogExporter.CompressRecentLogs(null));
                Pump(blockedExport);
                check(blockedExport.Result == ExportLogResult.InProgress, "concurrent_export_is_rejected");
            }
            finally { gate.Release(); }
        }
        finally
        {
            dataRoot.SetValue(null, savedRoot);
            if (savedViewModel is null) cache.TryRemove(typeof(RootViewModel), out _);
            else cache[typeof(RootViewModel)] = savedViewModel;
            if (savedToasts is null) cache.TryRemove(typeof(SukiUI.Toasts.ISukiToastManager), out _);
            else cache[typeof(SukiUI.Toasts.ISukiToastManager)] = savedToasts;
        }
    }

    private static void Pump(Task task)
    {
        var watch = System.Diagnostics.Stopwatch.StartNew();
        while (!task.IsCompleted)
        {
            if (watch.Elapsed > TimeSpan.FromSeconds(30)) throw new TimeoutException("log_operation");
            Dispatcher.UIThread.RunJobs();
            System.Threading.Thread.Sleep(5);
        }
        task.GetAwaiter().GetResult();
    }

    private static void RunPublishChecks(string root, Action<bool, string> check)
    {
        var publish = typeof(FileLogExporter).GetMethod("PublishArchiveVolumesAsync", BindingFlags.Static | BindingFlags.NonPublic)!;
        string Put(string directory, string name, string content)
        {
            Directory.CreateDirectory(directory);
            var path = Path.Combine(directory, name);
            File.WriteAllText(path, content);
            return path;
        }
        List<string> Archives(string directory, bool missingSecond = false)
        {
            var source = Path.Combine(directory, "source");
            return [Put(source, "one.zip", "new-first-volume"), missingSecond ? Path.Combine(source, "missing.zip") : Put(source, "two.zip", "new-second-volume")];
        }
        Exception? Publish(string chosen, IReadOnlyList<string> archives)
        {
            var file = DispatchProxy.Create<IStorageFile, LogExportStorageProxy>();
            ((LogExportStorageProxy)(object)file).PathValue = new Uri(chosen);
            try
            {
                Task.Run(async () => await (Task)publish.Invoke(null, [file, archives])!).GetAwaiter().GetResult();
                return null;
            }
            catch (Exception error) { return error.GetBaseException(); }
        }
        void Preserved(string path, string content, string name) =>
            check(File.Exists(path) && File.ReadAllBytes(path).SequenceEqual(System.Text.Encoding.UTF8.GetBytes(content)), name);

        foreach (var existingPart in new[] { 1, 2 })
        {
            var directory = Path.Combine(root, "existing-part" + existingPart);
            var chosen = Put(directory, "chosen.zip", "original-selected");
            var original = Put(directory, $"chosen-part{existingPart:D2}.zip", "original-volume");
            check(Publish(chosen, Archives(directory)) is IOException, $"existing_part{existingPart}_refused");
            Preserved(original, "original-volume", $"existing_part{existingPart}_bytes_preserved");
            Preserved(chosen, "original-selected", $"existing_part{existingPart}_selected_bytes_preserved");
            check(Directory.GetFiles(directory, "chosen-part*.zip").Length == 1, $"existing_part{existingPart}_preflight_before_any_write");
        }

        {
            var directory = Path.Combine(root, "second-target-directory");
            var chosen = Put(directory, "chosen.zip", "original-selected");
            Directory.CreateDirectory(Path.Combine(directory, "chosen-part02.zip"));
            check(Publish(chosen, Archives(directory)) is IOException, "directory_target_refused");
            check(!File.Exists(Path.Combine(directory, "chosen-part01.zip")), "directory_target_preflight_before_first_write");
            Preserved(chosen, "original-selected", "directory_target_selected_bytes_preserved");
        }

        {
            var directory = Path.Combine(root, "second-volume-failure");
            var chosen = Put(directory, "chosen.zip", "original-selected");
            var unrelated = Put(directory, "chosen-part03.zip", "original-unrelated-volume");
            check(Publish(chosen, Archives(directory, missingSecond: true)) is IOException, "second_volume_failure_propagated");
            var first = Path.Combine(directory, "chosen-part01.zip");
            check(OperatingSystem.IsWindows() ? !File.Exists(first) : File.ReadAllText(first) == "new-first-volume", "failed_first_volume_cleanup_matches_platform");
            check(!File.Exists(Path.Combine(directory, "chosen-part02.zip")), "failed_second_volume_not_created");
            Preserved(chosen, "original-selected", "second_volume_failure_selected_bytes_preserved");
            Preserved(unrelated, "original-unrelated-volume", "second_volume_failure_other_bytes_preserved");
        }

        {
            var directory = Path.Combine(root, "target-created-after-preflight");
            var chosen = Put(directory, "chosen.zip", "original-selected");
            var original = Path.Combine(directory, "chosen-part02.zip");
            var archives = new LogExportArchiveList(Archives(directory), index =>
            {
                if (index == 1) Put(directory, "chosen-part02.zip", "concurrent-original");
            });
            check(Publish(chosen, archives) is IOException, "target_created_after_preflight_refused");
            var first = Path.Combine(directory, "chosen-part01.zip");
            check(OperatingSystem.IsWindows() ? !File.Exists(first) : File.ReadAllText(first) == "new-first-volume", "race_failure_cleanup_matches_platform");
            Preserved(original, "concurrent-original", "race_target_bytes_preserved");
            Preserved(chosen, "original-selected", "race_selected_bytes_preserved");
        }

        {
            var directory = Path.Combine(root, "completed-volume-replacement");
            var chosen = Put(directory, "chosen.zip", "original-selected");
            var first = Path.Combine(directory, "chosen-part01.zip");
            var replaced = false;
            var denied = false;
            var archives = new LogExportArchiveList(Archives(directory, missingSecond: true), index =>
            {
                if (index != 1) return;
                try
                {
                    File.Delete(first);
                    Put(directory, "chosen-part01.zip", "replacement-original");
                    replaced = true;
                }
                catch (IOException) { denied = true; }
            });
            check(Publish(chosen, archives) is IOException, "replacement_then_second_volume_failure_propagated");
            check(replaced ? File.Exists(first) && File.ReadAllText(first) == "replacement-original" : !File.Exists(first), "rollback_does_not_delete_replacement");
            if (OperatingSystem.IsWindows()) check(denied && !replaced, "completed_volume_held_until_publish_outcome");
            Preserved(chosen, "original-selected", "replacement_failure_selected_bytes_preserved");
        }

        {
            var directory = Path.Combine(root, "repeat-publish");
            var chosen = Put(directory, "chosen.zip", "original-selected");
            check(Publish(chosen, Archives(directory)) is null, "fresh_split_export_succeeds");
            var before = Directory.GetFiles(directory, "chosen-part*.zip").ToDictionary(path => path, File.ReadAllBytes);
            check(before.Count == 2, "fresh_split_export_all_volumes_present");
            foreach (var path in before.Keys)
            {
                using var released = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
                check(released.Length > 0, "successful_split_volume_handle_released");
            }
            Preserved(chosen, "original-selected", "successful_split_selected_bytes_preserved");
            check(Publish(chosen, Archives(directory)) is IOException, "repeated_split_export_refused");
            check(before.All(pair => File.Exists(pair.Key) && File.ReadAllBytes(pair.Key).SequenceEqual(pair.Value)), "repeated_split_export_old_bytes_preserved");
        }

        {
            var directory = Path.Combine(root, "picker-cancel");
            var chosen = Put(directory, "chosen.zip", "original-selected");
            var original = Put(directory, "chosen-part01.zip", "original-volume");
            var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!;
            cache.TryGetValue(typeof(RootViewModel), out var saved);
            cache[typeof(RootViewModel)] = new Lazy<object>(() => RuntimeHelpers.GetUninitializedObject(typeof(RootViewModel)));
            try
            {
                var provider = DispatchProxy.Create<IStorageProvider, LogExportStorageProxy>();
                check(Task.Run(() => FileLogExporter.CompressRecentLogs(provider)).GetAwaiter().GetResult() == ExportLogResult.Cancelled, "picker_cancel_returns_cancelled");
                check(Task.Run(() => FileLogExporter.CompressRecentLogs(provider)).GetAwaiter().GetResult() == ExportLogResult.Cancelled, "picker_cancel_releases_export_lock");
                Preserved(chosen, "original-selected", "picker_cancel_selected_bytes_preserved");
                Preserved(original, "original-volume", "picker_cancel_volume_bytes_preserved");
            }
            finally
            {
                if (saved is null) cache.TryRemove(typeof(RootViewModel), out _);
                else cache[typeof(RootViewModel)] = saved;
            }
        }
    }
}

internal sealed class LogExportArchiveList(IReadOnlyList<string> files, Action<int> beforeRead) : IReadOnlyList<string>
{
    public int Count => files.Count;
    public string this[int index] { get { beforeRead(index); return files[index]; } }
    public IEnumerator<string> GetEnumerator() => files.GetEnumerator();
    System.Collections.IEnumerator System.Collections.IEnumerable.GetEnumerator() => GetEnumerator();
}

public class LogExportStorageProxy : DispatchProxy
{
    public Uri PathValue { get; set; } = new("file:///unused.zip");
    public string? WritePath { get; set; }
    public IStorageFile? SaveFile { get; set; }

    protected override object? Invoke(MethodInfo? method, object?[]? arguments) => method!.Name switch
    {
        "get_Path" => PathValue,
        "get_Name" => Path.GetFileName(PathValue.LocalPath),
        "SaveFilePickerAsync" => Task.FromResult(SaveFile),
        "OpenWriteAsync" => Task.FromResult<Stream>(new FileStream(WritePath!, FileMode.Create, FileAccess.Write, FileShare.None)),
        "Dispose" => null,
        _ => throw new NotSupportedException("Fixture must not access " + method.Name)
    };
}
