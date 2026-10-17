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

    protected override object? Invoke(MethodInfo? method, object?[]? arguments) => method!.Name switch
    {
        "get_Path" => PathValue,
        "get_Name" => Path.GetFileName(PathValue.LocalPath),
        "SaveFilePickerAsync" => Task.FromResult<IStorageFile?>(null),
        "Dispose" => null,
        _ => throw new NotSupportedException("Fixture must not access " + method.Name)
    };
}
