using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MFAAvalonia.Configuration;
using MFAAvalonia.Helper;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

internal static class PcrStartupActions
{
    private static TaskQueueViewModel? launching;
    private static CancellationTokenSource? cancellation;
    private static Action? afterCancellation;

    internal static Task RunAsync(TaskQueueViewModel vm) =>
        RunAsync(vm, token => vm.Processor.StartSoftware(token));

    internal static async Task RunAsync(TaskQueueViewModel vm, Func<CancellationToken, Task> launchSoftware)
    {
        if (PcrFormalPreflight.SettingsOnly) return;
        using var cancel = new CancellationTokenSource();
        try
        {
            var suppress = GlobalConfiguration.GetValue(ConfigurationKeys.NoAutoStart, bool.FalseString) == bool.TrueString;
            if (suppress) GlobalConfiguration.SetValue(ConfigurationKeys.NoAutoStart, bool.FalseString);
            if (GlobalConfiguration.HasFileAccessError)
                throw new IOException("无法读取或保存启动设置，已暂停本次自动启动。");
            // Consume both signals on this launch, including upgrades run by an older updater.
            if (ConsumeUpdateRestart(AppPaths.DataRoot) || suppress) return;
            var action = vm.Processor.InstanceConfiguration.GetValue(ConfigurationKeys.BeforeTask, "None");
            if (action == "None") return;
            if (action is not ("StartupSoftware" or "StartupSoftwareAndScript" or "StartupScriptOnly"))
                throw new InvalidOperationException("启动后操作无效，请重新选择并保存。");
            lock (PcrProfileRunGate.StartGate)
            {
                if (PcrProfileRunGate.Busy || !vm.CanEditPcrTarget) return;
                PcrRuntimePaths.RequireUpdateComplete(AppContext.BaseDirectory, PcrAlphaOwner.Repository);
                launching = vm;
                cancellation = cancel;
                vm.IsRunning = true;
                PcrProfileRunGate.Notify();
            }
            if (action != "StartupScriptOnly") await launchSoftware(cancel.Token);
            Task start;
            lock (PcrProfileRunGate.StartGate)
            {
                cancel.Token.ThrowIfCancellationRequested();
                launching = null;
                cancellation = null;
                vm.IsRunning = false;
                if (action == "StartupSoftware") return;
                // Automatic and manual starts use the same fresh Owner checks.
                start = vm.IsLabyrinthProfile ? PcrLabyrinthOwner.RequestStartAsync(vm) : PcrAlphaOwner.RequestStartAsync(vm);
            }
            await start;
        }
        catch (OperationCanceledException) when (cancel.IsCancellationRequested)
        {
            vm.Processor.AddLog("启动后操作已取消。", useMarkdown: false);
        }
        catch (Exception ex)
        {
            var message = "启动后操作未完成：" + ex.Message;
            vm.ShowPcrStartError(vm.BeginPcrRunFeedback(), message);
            vm.Processor.AddLog(message, useMarkdown: false);
            vm.FinishPlayerLog("not_started");
        }
        finally
        {
            Action? stopped = null;
            lock (PcrProfileRunGate.StartGate)
            {
                if (ReferenceEquals(launching, vm) && ReferenceEquals(cancellation, cancel))
                {
                    launching = null;
                    cancellation = null;
                    vm.IsRunning = false;
                    stopped = afterCancellation;
                    afterCancellation = null;
                }
                PcrProfileRunGate.Notify();
            }
            stopped?.Invoke();
        }
    }

    private static bool ConsumeUpdateRestart(string root)
    {
        var maintenance = Path.Combine(root, ".runtime", "player-maintenance");
        if (RestartAttributes(maintenance) is not { } attributes) return false;
        if ((attributes & FileAttributes.Directory) == 0 || !PcrLogFiles.IsRegularPath(root, maintenance))
            throw new IOException("更新回执目录不可用，已暂停本次自动启动。");
        var receipts = new List<(string Id, string Digest)>();
        foreach (var work in Directory.EnumerateDirectories(maintenance, "update-*"))
        {
            var id = Path.GetFileName(work);
            if (id.Length != 39 || !Guid.TryParseExact(id[7..], "N", out _)) continue;
            var receipt = ReadRestartJson(root, Path.Combine(work, "verification.json"));
            if (receipt?["status"]?.Type == JTokenType.String && (string?)receipt["status"] == "updated"
                && receipt["manifest_sha256"]?.Type == JTokenType.String)
                receipts.Add((id, (string)receipt["manifest_sha256"]!));
        }
        if (receipts.Count == 0) return false;
        var manifest = ReadRestartFile(root, Path.Combine(root, "package-manifest.json"))
            ?? throw new IOException("无法核对更新后的程序清单，已暂停本次自动启动。");
        var digest = Convert.ToHexString(SHA256.HashData(manifest)).ToLowerInvariant();
        var matching = receipts.Where(receipt => receipt.Digest == digest).Select(receipt => receipt.Id).ToHashSet();
        if (matching.Count == 0) return false;
        var marker = Path.Combine(maintenance, "startup-consumed.json");
        var consumed = ReadRestartJson(root, marker);
        if ((string?)consumed?["manifest_sha256"] == digest)
        {
            if (consumed["receipts"] is not JArray ids || ids.Any(id => id.Type != JTokenType.String))
                throw new IOException("更新启动记录无效，已暂停本次自动启动。");
            if (matching.IsSubsetOf(ids.Values<string>()!)) return false;
        }
        // Track update IDs as well as the digest: reinstalling the same payload is a new restart.
        JsonHelper.SaveJsonTextAtomicallyOrThrow(marker, new JObject
        {
            ["manifest_sha256"] = digest,
            ["receipts"] = new JArray(matching.OrderBy(id => id, StringComparer.Ordinal))
        }.ToString());
        return true;
    }

    private static FileAttributes? RestartAttributes(string path)
    {
        try { return File.GetAttributes(path); }
        catch (FileNotFoundException) { return null; }
        catch (DirectoryNotFoundException) { return null; }
    }

    private static byte[]? ReadRestartFile(string root, string path)
    {
        if (RestartAttributes(path) is not { } attributes) return null;
        if ((attributes & FileAttributes.Directory) != 0 || !PcrLogFiles.IsRegularPath(root, path)
            || new FileInfo(path).Length > 16 * 1024 * 1024)
            throw new IOException("更新启动记录不可用，已暂停本次自动启动。");
        var bytes = File.ReadAllBytes(path);
        if (bytes.Length > 16 * 1024 * 1024) throw new IOException("更新启动记录过大。");
        return bytes;
    }

    private static JObject? ReadRestartJson(string root, string path)
    {
        var bytes = ReadRestartFile(root, path);
        return bytes == null ? null : JObject.Parse(new UTF8Encoding(false, true).GetString(bytes),
            new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
    }

    internal static bool Cancel(TaskQueueViewModel vm, Action? action)
    {
        lock (PcrProfileRunGate.StartGate)
        {
            if (!ReferenceEquals(launching, vm)) return false;
            afterCancellation += action;
            cancellation!.Cancel();
            return true;
        }
    }
}
