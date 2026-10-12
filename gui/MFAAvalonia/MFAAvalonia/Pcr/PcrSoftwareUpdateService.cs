using System;
using System.Diagnostics;
using System.IO;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using MFAAvalonia.Configuration;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using Newtonsoft.Json.Linq;
using SukiUI.Controls;
using SukiUI.MessageBox;

namespace MFAAvalonia.Pcr;

/// <summary>The GUI delegates every program update to the manifest-aware PCR installer.</summary>
public static class PcrSoftwareUpdateService
{
    public const string CheckSetting = "PcrCheckStableUpdates";
    private static int operation;
    private static CancellationTokenSource? downloadCancellation;
    private static readonly CancellationTokenSource shutdown = new();
    private static string Player => Path.GetFullPath(AppContext.BaseDirectory);
    private static string Repository => PcrRuntimePaths.ResolveRepository(Player);
    private static VersionUpdateSettingsUserControlModel ViewModel => Instances.VersionUpdateSettingsUserControlModel;

    public static string InstalledVersion(string directory)
    {
        try
        {
            var manifest = JObject.Parse(File.ReadAllText(Path.Combine(directory, "package-manifest.json")));
            var update = manifest["update"];
            if (update?["revision"]?.Type != JTokenType.Integer || (long)update["revision"]! < 1
                || update["version"]?.Type != JTokenType.String)
                return "Maa-PCR（版本清单无效）";
            return $"{update["version"]} / R{update["revision"]}";
        }
        catch { return "Maa-PCR（开发模式）"; }
    }

    public static void CheckOnStartup()
    {
        RefreshRecoveryState();
        if (ViewModel.PcrRecoveryRequired) return;
        if (ConfigurationManager.Current.GetValue(CheckSetting, true)) _ = CheckAsync(false);
    }

    public static void RefreshRecoveryState()
    {
        var marker = Path.Combine(Player, ".runtime", "player-maintenance", "update-in-progress.json");
        ViewModel.PcrRecoveryRequired = File.Exists(marker) || Directory.Exists(marker);
        if (ViewModel.PcrRecoveryRequired)
            ViewModel.PcrUpdateStatus = "上次更新尚未完成，请恢复程序后再开始任务。";
        ViewModel.RefreshPcrUpdateAvailability();
    }

    public static async Task CheckAsync(bool manual = true)
    {
        if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) return;
        try
        {
            ViewModel.PcrUpdateChecking = true;
            ViewModel.PcrUpdateStatus = "正在检查稳定版…";
            var result = await RunAsync("check", CancellationToken.None);
            var status = (string?)result["status"];
            if (status == "recovery_required")
            {
                RefreshRecoveryState();
                return;
            }
            RequireCheckResponse(result);
            ViewModel.PcrUpdateAvailable = status == "update_available";
            ViewModel.PcrLatestVersion = ViewModel.PcrUpdateAvailable
                ? $"{result["version"]} / R{result["revision"]}" : "";
            ViewModel.PcrUpdateStatus = ViewModel.PcrUpdateAvailable
                ? $"发现稳定版 {ViewModel.PcrLatestVersion}" : "已是最新稳定版。";
            Instances.RootViewModel.WindowUpdateInfo = ViewModel.PcrUpdateAvailable ? ViewModel.PcrUpdateStatus : "";
            Instances.RootViewModel.TempResourceUpdateAction = ViewModel.PcrUpdateAvailable
                ? () => _ = InstallOnlineAsync() : null;
            if (manual || ViewModel.PcrUpdateAvailable)
                ToastHelper.Info("Maa-PCR 更新", ViewModel.PcrUpdateStatus);
        }
        catch (Exception ex)
        {
            ViewModel.PcrUpdateStatus = $"暂时无法检查更新，当前版本可继续使用。[{ex.Message}]";
            if (manual && !shutdown.IsCancellationRequested) ToastHelper.Warn("Maa-PCR 更新", ViewModel.PcrUpdateStatus);
        }
        finally
        {
            ViewModel.PcrUpdateChecking = false;
            Interlocked.Exchange(ref operation, 0);
            ViewModel.RefreshPcrUpdateAvailability();
        }
    }

    public static void RequireCheckResponse(JObject result)
    {
        var status = (string?)result["status"];
        if (status is not ("up_to_date" or "update_available")
            || result["current_revision"]?.Type != JTokenType.Integer || (long)result["current_revision"]! < 1)
            throw new InvalidOperationException("invalid_update_response");
        if (status == "update_available" && (result["revision"]?.Type != JTokenType.Integer
            || (long)result["revision"]! <= (long)result["current_revision"]!
            || result["version"]?.Type != JTokenType.String || string.IsNullOrWhiteSpace((string?)result["version"])
            || result["asset"] is not JObject))
            throw new InvalidOperationException("invalid_update_response");
    }

    public static async Task InstallOnlineAsync()
    {
        if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) return;
        try
        {
            if (!ViewModel.PcrUpdateAvailable || !await ConfirmInstallAsync(false)) return;
            ViewModel.PcrUpdateVisible = true;
            ViewModel.PcrUpdateDownloading = true;
            ViewModel.PcrUpdateProgress = 0;
            ViewModel.PcrUpdateIndeterminate = true;
            ViewModel.PcrUpdateStatus = "正在连接 GitHub，准备下载更新…";
            using var cancellation = new CancellationTokenSource();
            downloadCancellation = cancellation;
            var result = await RunAsync("download", cancellation.Token);
            if ((string?)result["status"] != "downloaded" || result["archive"]?.Type != JTokenType.String)
                throw new InvalidOperationException("invalid_download_response");
            var downloadedArchive = Path.GetFullPath((string)result["archive"]!);
            ViewModel.PcrUpdateProgress = 100;
            ViewModel.PcrUpdateDownloading = false;
            await LaunchInstallerAsync(downloadedArchive, false);
        }
        catch (OperationCanceledException) { ViewModel.PcrUpdateStatus = "下载已取消，当前版本未改变。"; }
        catch (Exception ex) { ReportFailure(ex); }
        finally
        {
            downloadCancellation = null;
            ViewModel.PcrUpdateDownloading = false;
            ViewModel.PcrUpdateIndeterminate = false;
            Interlocked.Exchange(ref operation, 0);
            ViewModel.RefreshPcrUpdateAvailability();
        }
    }

    public static void CancelDownload() => downloadCancellation?.Cancel();
    internal static void CancelPendingWork() => shutdown.Cancel();

    public static (string Python, string Script, string Directory) RecoveryBootstrap(string player)
    {
        player = Path.GetFullPath(player).TrimEnd(Path.DirectorySeparatorChar);
        var receiptPath = Path.Combine(player, ".runtime", "player-maintenance", "external-updater.json");
        RequirePlainPath(receiptPath);
        if (new FileInfo(receiptPath).Length > 8 * 1024 * 1024)
            throw new InvalidOperationException("invalid_external_updater_receipt");
        var receipt = JObject.Parse(File.ReadAllText(receiptPath),
            new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        if (receipt.Count != 8 || receipt["schema_version"]?.Type != JTokenType.Integer || (long)receipt["schema_version"]! != 1
            || !string.Equals((string?)receipt["player"], player, StringComparison.OrdinalIgnoreCase)
            || receipt["workspace"]?.Type != JTokenType.String
            || (string?)receipt["python"] != "runtime/python/python.exe"
            || (string?)receipt["script"] != "scripts/player-update.py" || receipt["files"] is not JArray files
            || receipt["base_revision"]?.Type != JTokenType.Integer || (long)receipt["base_revision"]! < 1
            || receipt["base_manifest_sha256"]?.Type != JTokenType.String
            || !Regex.IsMatch((string)receipt["base_manifest_sha256"]!, "^[0-9a-f]{64}$"))
            throw new InvalidOperationException("invalid_external_updater_receipt");
        var markerPath = Path.Combine(player, ".runtime", "player-maintenance", "update-in-progress.json");
        RequirePlainPath(markerPath);
        var marker = JObject.Parse(File.ReadAllText(markerPath),
            new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        if (marker["workspace"]?.Type != JTokenType.String
            || !Regex.IsMatch((string)marker["workspace"]!, "^update-[0-9a-f]{32}$")
            || (string?)marker["old_manifest_sha256"] != (string)receipt["base_manifest_sha256"]!)
            throw new InvalidOperationException("external_updater_baseline_mismatch");
        var oldManifestPath = Path.Combine(Path.GetDirectoryName(markerPath)!, (string)marker["workspace"]!, "before", "package-manifest.json");
        RequirePlainPath(oldManifestPath);
        var oldBytes = File.ReadAllBytes(oldManifestPath);
        var oldManifest = JObject.Parse(System.Text.Encoding.UTF8.GetString(oldBytes));
        if (oldManifest["update"]?["revision"]?.Type != JTokenType.Integer
            || (long)oldManifest["update"]!["revision"]! != (long)receipt["base_revision"]!
            || Convert.ToHexString(SHA256.HashData(oldBytes)).ToLowerInvariant() != (string)receipt["base_manifest_sha256"]!)
            throw new InvalidOperationException("external_updater_baseline_mismatch");
        var workspaceText = (string)receipt["workspace"]!;
        if (!Path.IsPathFullyQualified(workspaceText) || workspaceText.StartsWith(@"\\", StringComparison.Ordinal))
            throw new InvalidOperationException("invalid_external_updater_workspace");
        var workspace = Path.GetFullPath(workspaceText).TrimEnd(Path.DirectorySeparatorChar);
        if (string.Equals(workspace, player, StringComparison.OrdinalIgnoreCase)
            || workspace.StartsWith(player + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            || Path.GetFileName(Path.GetDirectoryName(workspace)) != "Maa-PCR-update"
            || !Regex.IsMatch(Path.GetFileName(workspace), "^update-[0-9a-f]{32}$"))
            throw new InvalidOperationException("invalid_external_updater_workspace");
        RequirePlainPath(workspace);
        var inventory = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var token in files)
        {
            if (token is not JObject row || row["path"]?.Type != JTokenType.String
                || row["size"]?.Type != JTokenType.Integer || (long)row["size"]! < 0
                || row["sha256"]?.Type != JTokenType.String)
                throw new InvalidOperationException("invalid_external_updater_receipt");
            var name = (string)row["path"]!;
            if (name.Contains('\\') || name.Contains(':') || name.StartsWith('/')
                || Array.Exists(name.Split('/'), part => part is "" or "." or "..") || !inventory.Add(name))
                throw new InvalidOperationException("invalid_external_updater_receipt");
            var file = Path.GetFullPath(Path.Combine(workspace, name));
            RequirePlainPath(file);
            if (new FileInfo(file).Length != (long)row["size"]!)
                throw new InvalidOperationException("external_updater_hash_mismatch");
            using var stream = File.OpenRead(file);
            if (!string.Equals(Convert.ToHexString(SHA256.HashData(stream)), (string)row["sha256"]!, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("external_updater_hash_mismatch");
        }
        foreach (var name in new[] { "runtime/python/python.exe", "scripts/player-update.py", "scripts/player-maintenance.py",
                     "src/pcr_maa/__init__.py", "src/pcr_maa/player_update.py", "src/pcr_maa/player_maintenance.py" })
            if (!inventory.Contains(name)) throw new InvalidOperationException("updater_bootstrap_missing");
        return (Path.Combine(workspace, "runtime", "python", "python.exe"),
            Path.Combine(workspace, "scripts", "player-update.py"), workspace);
    }

    private static void RequirePlainPath(string path)
    {
        for (var current = path; current != null; current = Path.GetDirectoryName(current))
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("external_updater_reparse_path");
    }

    public static async Task InstallLocalAsync(string archive)
    {
        if (!archive.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) || !File.Exists(archive))
        {
            ToastHelper.Warn("Maa-PCR 更新", "请选择 Maa-PCR 完整运行包或补丁 ZIP。");
            return;
        }
        if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) return;
        try
        {
            if (!await ConfirmInstallAsync(false)) return;
            await LaunchInstallerAsync(Path.GetFullPath(archive), false);
        }
        catch (Exception ex) { ReportFailure(ex); }
        finally
        {
            Interlocked.Exchange(ref operation, 0);
            ViewModel.RefreshPcrUpdateAvailability();
        }
    }

    public static async Task RecoverAsync()
    {
        if (Interlocked.CompareExchange(ref operation, 1, 0) != 0) return;
        try
        {
            if (!await ConfirmInstallAsync(true)) return;
            await LaunchInstallerAsync(null, true);
        }
        catch (Exception ex) { ReportFailure(ex); }
        finally
        {
            Interlocked.Exchange(ref operation, 0);
            ViewModel.RefreshPcrUpdateAvailability();
        }
    }

    private static async Task<bool> ConfirmInstallAsync(bool recovery)
    {
        if (PcrProfileRunGate.Busy)
        {
            ToastHelper.Warn("Maa-PCR 更新", "请等待所有任务和 Agent 完全退出后再安装更新。");
            return false;
        }
        var result = await SukiMessageBox.ShowDialog(new SukiMessageBoxHost
        {
            Content = recovery
                ? "软件将关闭，由维护器恢复上次更新并核验程序文件。设置和执行账本保留，成功后只重启软件。"
                : "更新将保留设置和执行账本，安装时关闭软件，成功后只重启软件，不自动开始游戏任务。",
            ActionButtonsPreset = SukiMessageBoxButtons.YesNo,
            IconPreset = SukiMessageBoxIcons.Information
        }, new SukiMessageBoxOptions { Title = recovery ? "恢复 Maa-PCR 更新" : "安装 Maa-PCR 稳定版" });
        return InstallConfirmed(result);
    }

    // Closing a Suki confirmation window returns null, which means cancel.
    public static bool InstallConfirmed(object? result) => result is SukiMessageBoxResult.Yes;

    private static async Task LaunchInstallerAsync(string? archive, bool recovery)
    {
        lock (PcrProfileRunGate.StartGate)
        {
            if (PcrProfileRunGate.Busy)
                throw new InvalidOperationException("task_owner_busy_update_not_started");
            PcrProfileRunGate.UpdatePending = true;
        }
        PcrProfileRunGate.Notify();
        Instances.RootViewModel.SetUpdating(true);
        var launched = false;
        try
        {
            ViewModel.PcrUpdateVisible = true;
            ViewModel.PcrUpdateIndeterminate = true;
            ViewModel.PcrUpdateStatus = "正在准备外置维护器，完成后软件将关闭…";
            var args = recovery ? new[] { "--recover", "--parent-pid", Environment.ProcessId.ToString() }
                : new[] { "--archive", archive!, "--parent-pid", Environment.ProcessId.ToString() };
            var result = await RunAsync("launch", CancellationToken.None, args);
            if ((string?)result["status"] != "installer_started"
                || result["worker_pid"]?.Type != JTokenType.Integer || (long)result["worker_pid"]! < 1)
                throw new InvalidOperationException("invalid_installer_response");
            launched = true;
            Instances.ShutdownApplication();
        }
        finally
        {
            if (!launched)
            {
                ViewModel.PcrUpdateIndeterminate = false;
                lock (PcrProfileRunGate.StartGate) PcrProfileRunGate.UpdatePending = false;
                Instances.RootViewModel.SetUpdating(false);
                PcrProfileRunGate.Notify();
            }
        }
    }

    private static void ReportFailure(Exception ex)
    {
        LoggerHelper.Warning($"PCR 更新操作失败：{ex.Message}");
        ViewModel.PcrUpdateVisible = true;
        ViewModel.PcrUpdateIndeterminate = false;
        ViewModel.PcrUpdateStatus = $"更新未启动或未完成，请查看更新记录。[{ex.Message}]";
        ToastHelper.Warn("Maa-PCR 更新", ViewModel.PcrUpdateStatus);
        RefreshRecoveryState();
    }

    private static async Task<JObject> RunAsync(string command, CancellationToken cancellation, params string[] arguments)
    {
        string repository, script, python;
        if (command == "launch" && Array.IndexOf(arguments, "--recover") >= 0
            && File.Exists(Path.Combine(Player, ".runtime", "player-maintenance", "external-updater.json")))
            (python, script, repository) = await Task.Run(() => RecoveryBootstrap(Player));
        else
        {
            repository = Repository;
            script = Path.Combine(repository, "scripts", "player-update.py");
            python = PcrRuntimePaths.ResolvePython(repository);
            if (!File.Exists(script) || !File.Exists(Path.Combine(Player, "package-manifest.json")))
                throw new InvalidOperationException("installed_update_component_missing");
        }
        var start = new ProcessStartInfo(python)
        {
            UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = repository,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8, StandardErrorEncoding = System.Text.Encoding.UTF8
        };
        foreach (var argument in new[] { "-X", "utf8", "-B", "-u", script, command, "--player", Player })
            start.ArgumentList.Add(argument);
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        if (!string.IsNullOrWhiteSpace(ViewModel.ProxyAddress))
        {
            var proxy = ViewModel.ProxyAddress.Trim();
            if (!proxy.Contains("://", StringComparison.Ordinal))
            {
                if (ViewModel.ProxyType == VersionUpdateSettingsUserControlModel.UpdateProxyType.Socks5)
                    throw new InvalidOperationException("unsupported_proxy_use_http_https");
                proxy = "http://" + proxy;
            }
            if (!Uri.TryCreate(proxy, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https"))
                throw new InvalidOperationException("unsupported_proxy_use_http_https");
            start.Environment["HTTPS_PROXY"] = proxy;
            start.Environment["HTTP_PROXY"] = proxy;
        }
        if (command != "launch") shutdown.Token.ThrowIfCancellationRequested();
        using var process = Process.Start(start) ?? throw new InvalidOperationException("updater_process_not_started");
        using var timeout = new CancellationTokenSource(command == "download" ? TimeSpan.FromMinutes(30) : TimeSpan.FromMinutes(3));
        using var stop = CancellationTokenSource.CreateLinkedTokenSource(cancellation, timeout.Token,
            command == "launch" ? CancellationToken.None : shutdown.Token);
        using var stopRegistration = stop.Token.Register(() =>
        {
            try { if (!process.HasExited) process.Kill(entireProcessTree: false); }
            catch (Exception error) when (error is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                LoggerHelper.Warning("PCR 更新检查或下载进程退出时已结束，或退出状态无法确认。");
            }
        });
        var stdout = process.StandardOutput.ReadToEndAsync(stop.Token);
        var progress = ReadProgressAsync(process.StandardError, stop.Token);
        try
        {
            await process.WaitForExitAsync(stop.Token);
            await progress;
            var output = await stdout;
            if (output.Length > 65536) throw new InvalidOperationException("invalid_update_response");
            var result = JObject.Parse(output, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (process.ExitCode != 0 || (string?)result["status"] == "failed")
                throw new InvalidOperationException((string?)result["error"] ?? "updater_failed");
            return result;
        }
        catch
        {
            if (!process.HasExited) process.Kill(entireProcessTree: false);
            try { await process.WaitForExitAsync(); } catch { }
            try { await Task.WhenAll(stdout, progress); } catch { }
            throw;
        }
    }

    private static async Task ReadProgressAsync(StreamReader stderr, CancellationToken cancellation)
    {
        while (await stderr.ReadLineAsync(cancellation) is { } line)
        {
            if (line.Length > 4096) continue;
            try
            {
                var progress = JObject.Parse(line);
                if (DownloadProgress(progress) is { } value)
                    DispatcherHelper.PostOnMainThread(() =>
                    {
                        if (!ViewModel.PcrUpdateDownloading) return;
                        ViewModel.PcrUpdateProgress = value;
                        ViewModel.PcrUpdateIndeterminate = value >= 100;
                        ViewModel.PcrUpdateStatus = value >= 100
                            ? "下载完成，正在校验更新文件…"
                            : $"正在下载更新：{value:F0}%（{(long)progress["bytes"]! / 1048576.0:F1} / {(long)progress["total"]! / 1048576.0:F1} MiB）";
                    });
            }
            catch (Newtonsoft.Json.JsonException) { }
        }
    }

    public static double? DownloadProgress(JObject progress)
    {
        if ((string?)progress["phase"] != "download" || progress["bytes"]?.Type != JTokenType.Integer
            || progress["total"]?.Type != JTokenType.Integer || (long)progress["total"]! <= 0
            || (long)progress["bytes"]! < 0 || (long)progress["bytes"]! > (long)progress["total"]!) return null;
        return 100.0 * (long)progress["bytes"]! / (long)progress["total"]!;
    }
}
