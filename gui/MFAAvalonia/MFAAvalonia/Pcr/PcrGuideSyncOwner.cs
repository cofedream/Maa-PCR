using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Utilities;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// Shares the application's busy boundary; this child has no device or game-action input.
internal static class PcrGuideSyncOwner
{
    private sealed class Run(TaskQueueViewModel vm, CancellationToken token)
    {
        internal readonly TaskQueueViewModel ViewModel = vm;
        internal readonly CancellationTokenSource Cancel = CancellationTokenSource.CreateLinkedTokenSource(token);
        internal PcrWindowsChild? Child;
    }
    private static Run? current;
    internal static bool IsActive => Volatile.Read(ref current) != null;
    internal static void Stop() { lock (PcrProfileRunGate.StartGate) current?.Cancel.Cancel(); }
    internal static string? ConfiguredAccountId() => MaaProcessorManager.Instance.TryGetInstance("pcr-labyrinth-v1", out var processor)
        ? processor.ViewModel?.LabyrinthSettings.ExpectedPlayerId : null;

    internal static JObject Request(PcrGuideInput input, string playerId, string username, string password)
    {
        PcrGuideReview.Validate(input);
        var catalog = input.Catalog ?? throw new InvalidOperationException("请先导入当前静态角色表。");
        if (catalog.ContentVersion.Length != 12 || !catalog.ContentVersion.All(char.IsAsciiDigit))
            throw new InvalidOperationException("请导入带有官方内容版本的当前角色表。");
        if (!long.TryParse(playerId, NumberStyles.None, CultureInfo.InvariantCulture, out var id) || id <= 0
            || string.IsNullOrWhiteSpace(username) || string.IsNullOrEmpty(password))
            throw new InvalidOperationException("请先在黎明界配置中填写账号、密码和游戏玩家 ID；读取功能复用该配置。");
        if (input.Snapshot != null && input.Snapshot.AccountId != id.ToString(CultureInfo.InvariantCulture))
            throw new InvalidOperationException("已保存资料属于另一账号，请先清空本面板账号资料后再读取。");
        return new JObject { ["version"] = 2, ["account_id"] = id, ["content_version"] = catalog.ContentVersion,
            ["credentials"] = new JObject { ["username"] = username, ["password"] = password } };
    }
    internal static byte[] ReadResult(string text)
    {
        if (Encoding.UTF8.GetByteCount(text) > PcrGuideData.MaxBytes) throw new InvalidDataException("账号读取结果过大。");
        var result = JObject.Parse(text, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        if ((string?)result["kind"] != "result" || result.Properties().Any(p => p.Name is not ("kind" or "outcome" or "snapshot")))
            throw new InvalidDataException("账号读取结果格式无效。");
        if ((string?)result["outcome"] != "complete") throw new InvalidOperationException((string?)result["outcome"] switch
        {
            "stopped" => "已停止读取，原资料保持。",
            "account_mismatch" => "协议账号与游戏玩家 ID 不一致，原资料保持。",
            "content_update_required" => "游戏内容版本已变化，请先更新静态角色表。",
            "manual_verification_required" => "登录需要人工验证码；本次停止，没有自动重试。",
            "captcha_cancelled" or "captcha_timeout" => "验证码已取消或超时，原资料保持。",
            "captcha_failed" or "captcha_rejected" or "captcha_network_failed" => "验证码未通过或加载失败，本次停止，原资料保持。",
            "sdk_login_failed" or "login_failed" => "账号登录失败，请检查黎明界配置中的账号资料。",
            "sdk_login_timeout" or "protocol_login_timeout" => "账号登录超时，本次停止，没有自动重试。",
            _ => "账号读取未完成，原资料保持。"
        });
        if (result["snapshot"] is not JObject snapshot) throw new InvalidDataException("缺少账号快照。");
        return Encoding.UTF8.GetBytes(snapshot.ToString(Formatting.None));
    }
    internal static async Task<byte[]> ReadAsync(TaskQueueViewModel vm, PcrGuideInput input, CancellationToken token)
    {
        Run run;
        JObject request;
        lock (PcrProfileRunGate.StartGate)
        {
            if (PcrProfileRunGate.Busy || !vm.CanEditPcrTarget) throw new InvalidOperationException("请先停止当前任务或连接，再读取账号。");
            if (!MaaProcessorManager.Instance.TryGetInstance("pcr-labyrinth-v1", out var processor) || processor.ViewModel == null)
                throw new InvalidOperationException("请先打开黎明界配置并填写账号资料。");
            var settings = processor.ViewModel.LabyrinthSettings;
            settings.HideCredentials();
            request = Request(input, settings.ExpectedPlayerId, settings.Username, settings.Password);
            current = run = new Run(vm, token);
            vm.IsRunning = true;
            PcrProfileRunGate.Notify();
        }
        var released = false;
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(210));
        using var cancel = CancellationTokenSource.CreateLinkedTokenSource(run.Cancel.Token, deadline.Token);
        try
        {
            cancel.Token.ThrowIfCancellationRequested();
            var root = PcrAlphaOwner.Repository;
            PcrRuntimePaths.RequireUpdateComplete(AppContext.BaseDirectory, root);
            var script = Path.Combine(root, "scripts", "read-guide-snapshot.py");
            if (!File.Exists(script)) throw new InvalidOperationException("账号读取组件缺失，请使用完整候选包。");
            run.Child = new PcrWindowsChild();
            run.Child.Start(PcrRuntimePaths.ResolvePython(root), root, "-X", "utf8", "-B", "-u", script);
            await run.Child.Send(request.ToString(Formatting.None)).WaitAsync(cancel.Token);
            request.Remove("credentials");
            await foreach (var line in run.Child.Lines.Reader.ReadAllAsync(cancel.Token))
            {
                if (line.Error) continue;
                if (PcrLabyrinthCaptchaMessage.TryParse(line.Text, out var captcha))
                {
                    cancel.Token.ThrowIfCancellationRequested();
                    captcha!.OpenPage(UrlUtilities.OpenUrl);
                    deadline.CancelAfter(TimeSpan.FromSeconds(810));
                    continue;
                }
                var bytes = ReadResult(line.Text);
                // Validate before publication; failure leaves the previous panel untouched.
                _ = PcrGuideData.AccountSnapshot(bytes, input.Catalog!, request["account_id"]!.ToString(), DateTimeOffset.Now);
                await Task.WhenAll(run.Child.StdoutCompletion, run.Child.StderrCompletion).WaitAsync(cancel.Token);
                while (!run.Child.Exited) await Task.Delay(20, cancel.Token);
                if (run.Child.ExitCode != 0) throw new InvalidOperationException("账号读取进程异常退出，原资料保持。");
                cancel.Token.ThrowIfCancellationRequested();
                return bytes;
            }
            throw new InvalidOperationException("账号组件未返回结果，原资料保持。");
        }
        finally
        {
            request.Remove("credentials");
            try
            {
                if (run.Child != null) await Task.Run(run.Child.Dispose);
                released = true;
            }
            finally
            {
                if (released) lock (PcrProfileRunGate.StartGate)
                {
                    if (ReferenceEquals(current, run)) current = null;
                    run.Cancel.Dispose(); vm.IsRunning = false; PcrProfileRunGate.Notify();
                }
            }
        }
    }
}
