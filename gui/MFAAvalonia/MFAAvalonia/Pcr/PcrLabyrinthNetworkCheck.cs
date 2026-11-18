using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// Fixed-host, read-only connectivity report for the labyrinth settings card.
// The child can dial only the selected channel's game root, the fixed SDK login
// host and the fixed database host; this side accepts only fixed codes, fixed
// host names and bounded millisecond values.
internal static class PcrLabyrinthNetworkCheck
{
    internal const string Script = "scripts/check-labyrinth-network.py";
    private static readonly TimeSpan Deadline = TimeSpan.FromSeconds(75);
    private static readonly HashSet<string> Targets = new(StringComparer.Ordinal) { "game", "sdk", "database" };
    private static readonly HashSet<string> Hosts = new(StringComparer.Ordinal) {
        "l1-prod-uo-gs-gzlj.bilibiligame.net", "l3-prod-all-gs-gzlj.bilibiligame.net",
        "line1-sdk-center-login-sh.biligame.net", "github.com" };
    private static readonly HashSet<string> Codes = new(StringComparer.Ordinal) {
        "dns_failed", "tcp_refused", "tcp_timeout", "tls_timeout", "tls_failed", "unreachable" };
    internal sealed record Probe(string Target, string Host, bool Ok, int? DnsMs, int? TcpMs,
        int? TlsMs, string? Error, int Attempts);
    internal sealed record Report(string Summary, IReadOnlyList<Probe> Probes);

    internal static string Request(string server, bool database)
    {
        if (!PcrLabyrinthServer.All.Any(item => item.Id == server))
            throw new InvalidOperationException("所选游戏渠道无效，无法自检。");
        return new JObject { ["version"] = 1, ["server"] = server, ["database"] = database }
            .ToString(Formatting.None);
    }

    internal static Report ReadReport(string text)
    {
        if (text.Length > 4096) throw new InvalidDataException("网络自检结果过大。");
        JObject root;
        try {
            root = JObject.Parse(text, new JsonLoadSettings {
                DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
        } catch (JsonException) { throw new InvalidDataException("网络自检结果格式无效。"); }
        if (root.Properties().Count() != 4 || (string?)root["kind"] != "network_report"
            || root["version"]?.Type != JTokenType.Integer
            || root["version"]!.ToString() != "1"
            || root["summary"]?.Type != JTokenType.String)
            throw new InvalidDataException("网络自检结果格式无效。");
        var summary = (string)root["summary"]!;
        if (summary is not ("ok" or "slow" or "failed") || root["results"] is not JArray rows
            || rows.Count is < 2 or > 3) throw new InvalidDataException("网络自检结果格式无效。");
        var probes = new List<Probe>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var token in rows) {
            if (token is not JObject row || row.Properties().Count() != 8)
                throw new InvalidDataException("网络自检结果格式无效。");
            var target = (string?)row["target"];
            var host = (string?)row["host"];
            if (target is null || !Targets.Contains(target) || !seen.Add(target)
                || host is null || !Hosts.Contains(host) || !HostMatches(target, host)
                || row["ok"]?.Type != JTokenType.Boolean)
                throw new InvalidDataException("网络自检结果格式无效。");
            int? Millis(string name) {
                var value = row[name];
                if (value is null || value.Type == JTokenType.Null) return null;
                if (value.Type != JTokenType.Integer
                    || !long.TryParse(value.ToString(), NumberStyles.None,
                        CultureInfo.InvariantCulture, out var number) || number is < 0 or > 600000)
                    throw new InvalidDataException("网络自检结果格式无效。");
                return (int)number;
            }
            var dns = Millis("dns_ms"); var tcp = Millis("tcp_ms"); var tls = Millis("tls_ms");
            string? error = null;
            if (row["error"]?.Type == JTokenType.String) {
                error = (string?)row["error"];
                if (error is null || !Codes.Contains(error)) throw new InvalidDataException("网络自检结果格式无效。");
            } else if (row["error"]?.Type != JTokenType.Null)
                throw new InvalidDataException("网络自检结果格式无效。");
            var ok = (bool)row["ok"]!;
            var attemptsToken = row["attempts"];
            if (attemptsToken?.Type != JTokenType.Integer
                || !long.TryParse(attemptsToken.ToString(), NumberStyles.None,
                    CultureInfo.InvariantCulture, out var attempts) || attempts is < 0 or > 2
                || ok != (error is null) || (ok && (tcp is null || tls is null)))
                throw new InvalidDataException("网络自检结果格式无效。");
            probes.Add(new Probe(target, host, ok, dns, tcp, tls, error, (int)attempts));
        }
        if (probes.All(probe => probe.Ok) == (summary == "failed"))
            throw new InvalidDataException("网络自检结果格式无效。");
        return new Report(summary, probes);
    }

    internal static string Describe(Report report)
    {
        var parts = report.Probes.Select(probe => {
            var label = probe.Target switch { "game" => "游戏服", "sdk" => "登录服", _ => "GitHub" };
            if (!probe.Ok) return label + "无法连接（" + Reason(probe.Error) + "）";
            var milliseconds = (probe.TcpMs ?? 0) + (probe.TlsMs ?? 0);
            return label + "握手 " + (milliseconds / 1000.0).ToString("0.0", CultureInfo.InvariantCulture) + " 秒";
        });
        var details = string.Join("，", parts) + "。";
        return report.Summary switch {
            "ok" => "网络自检：连接正常。" + details,
            "slow" => "网络自检：连接偏慢。" + details + "若登录或撤退仍超时，请更换网络后重试。",
            _ => "网络自检：存在无法连接的目标。" + details + "请检查代理、防火墙或网络环境后重试。" };
    }

    // A report belongs only to the selection it was started from.
    internal static bool IsCurrent(string startedServer, PcrLabyrinthAccount? startedAccount, bool startedDatabase,
        string currentServer, PcrLabyrinthAccount? currentAccount, bool currentDatabase) =>
        startedServer==currentServer && startedDatabase==currentDatabase
        && ReferenceEquals(startedAccount,currentAccount);

    private static string Reason(string? code) => code switch {
        "dns_failed" => "域名解析失败", "tcp_refused" => "端口拒绝",
        "tcp_timeout" => "连接超时", "tls_timeout" => "握手超时",
        "tls_failed" => "证书或 TLS 校验失败", _ => "网络不可达" };

    private static bool HostMatches(string target, string host) => target switch {
        "game" => host is "l1-prod-uo-gs-gzlj.bilibiligame.net" or "l3-prod-all-gs-gzlj.bilibiligame.net",
        "sdk" => host == "line1-sdk-center-login-sh.biligame.net",
        _ => host == "github.com" };

    internal static async Task<Report> RunAsync(string server, bool database, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        var root = PcrAlphaOwner.Repository;
        PcrRuntimePaths.RequireUpdateComplete(AppContext.BaseDirectory, root);
        var script = Path.Combine(root, Script);
        if (!File.Exists(script)) throw new InvalidOperationException("网络自检组件缺失，请重新解压完整程序包。");
        var child = new PcrWindowsChild();
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(Deadline);
        try {
            child.StartWithEnvironment(PcrRuntimePaths.ResolvePython(root), root,
                PcrNetworkProxy.DirectEnvironment(), "-X", "utf8", "-B", "-u", script);
            await child.Send(Request(server, database)).WaitAsync(deadline.Token);
            await foreach (var line in child.Lines.Reader.ReadAllAsync(deadline.Token)) {
                if (line.Error) continue;
                var report = ReadReport(line.Text);
                await Task.WhenAll(child.StdoutCompletion, child.StderrCompletion).WaitAsync(deadline.Token);
                while (!child.Exited) await Task.Delay(20, deadline.Token);
                if (child.ExitCode != 0) throw new InvalidOperationException("网络自检进程异常退出，请重试。");
                return report;
            }
            throw new InvalidOperationException("网络自检组件未返回结果，请重试。");
        } finally {
            try { await Task.Run(child.Dispose); }
            catch { throw new InvalidOperationException("网络自检进程退出尚未确认，请退出软件后重试。"); }
        }
    }
}
