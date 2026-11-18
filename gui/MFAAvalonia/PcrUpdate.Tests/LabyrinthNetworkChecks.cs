using System;
using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;

/// <summary>
/// Offline checks for the labyrinth network self-check: the request is fixed,
/// the report parser accepts only fixed hosts/codes and the text never echoes
/// an address. No network, device or game input is used.
/// </summary>
internal static class LabyrinthNetworkChecks
{
    private const string GameRow =
        "{\"target\":\"game\",\"host\":\"l3-prod-all-gs-gzlj.bilibiligame.net\",\"ok\":true,"
        + "\"dns_ms\":5,\"tcp_ms\":20,\"tls_ms\":180,\"error\":null,\"attempts\":1}";
    private const string SdkRow =
        "{\"target\":\"sdk\",\"host\":\"line1-sdk-center-login-sh.biligame.net\",\"ok\":true,"
        + "\"dns_ms\":4,\"tcp_ms\":15,\"tls_ms\":170,\"error\":null,\"attempts\":1}";
    private const string DatabaseRow =
        "{\"target\":\"database\",\"host\":\"github.com\",\"ok\":true,"
        + "\"dns_ms\":8,\"tcp_ms\":30,\"tls_ms\":260,\"error\":null,\"attempts\":2}";
    private const string Healthy = "{\"kind\":\"network_report\",\"version\":1,\"summary\":\"ok\",\"results\":["
        + GameRow + "," + SdkRow + "," + DatabaseRow + "]}";

    internal static int Run()
    {
        var count = 0;
        void Require(bool value, string test)
        {
            if (!value) throw new InvalidOperationException(test);
            count++;
        }

        var assembly = typeof(MFAAvalonia.Pcr.PcrRuntimePaths).Assembly;
        var type = assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthNetworkCheck", true)!;
        var request = type.GetMethod("Request", BindingFlags.Static | BindingFlags.NonPublic)!;
        var read = type.GetMethod("ReadReport", BindingFlags.Static | BindingFlags.NonPublic)!;
        var describe = type.GetMethod("Describe", BindingFlags.Static | BindingFlags.NonPublic)!;

        Require((string)request.Invoke(null, new object[] { "cn-bilibili", true })!
                == "{\"version\":1,\"server\":\"cn-bilibili\",\"database\":true}",
            "labyrinth_network_request_is_fixed");
        var unknownRejected = false;
        try { request.Invoke(null, new object[] { "cn-not-a-channel", true }); }
        catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException)
        { unknownRejected = true; }
        Require(unknownRejected, "labyrinth_network_request_rejects_unknown_server");

        var healthyText = Describe(describe, read, Healthy);
        Require(healthyText.StartsWith("网络自检：连接正常。", StringComparison.Ordinal)
                && healthyText.Contains("游戏服握手 0.2 秒", StringComparison.Ordinal)
                && healthyText.Contains("GitHub握手 0.3 秒", StringComparison.Ordinal),
            "labyrinth_network_describes_handshake_seconds");
        Require(!healthyText.Contains("l3-prod", StringComparison.Ordinal)
                && !healthyText.Contains("github.com", StringComparison.Ordinal),
            "labyrinth_network_text_never_echoes_a_host");

        var isCurrent = type.GetMethod("IsCurrent", BindingFlags.Static | BindingFlags.NonPublic)!;
        var accountType = assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthAccount", true)!;
        var ctor = accountType.GetConstructor(BindingFlags.Instance | BindingFlags.NonPublic, null,
            new[] { typeof(string), typeof(string), typeof(string), typeof(JObject), typeof(string) }, null)!;
        object Account(string id) => ctor.Invoke(new object[] { id, id, "cn-bilibili", new JObject(), "" })!;
        var accountA = Account("a");
        var accountB = Account("b");
        bool Current(string server1, object account1, bool database1,
            string server2, object account2, bool database2) =>
            (bool)isCurrent.Invoke(null, new[] { server1, account1, database1, server2, account2, database2 })!;
        Require(Current("cn-bilibili", accountA, true, "cn-bilibili", accountA, true),
            "labyrinth_network_result_stays_current");
        Require(!Current("cn-bilibili", accountA, true, "cn-mumu", accountA, true),
            "labyrinth_network_result_discarded_on_channel_switch");
        Require(!Current("cn-bilibili", accountA, true, "cn-bilibili", accountB, true),
            "labyrinth_network_result_discarded_on_account_switch");
        Require(!Current("cn-bilibili", accountA, true, "cn-bilibili", accountA, false),
            "labyrinth_network_result_discarded_on_database_toggle");

        var slowText = Describe(describe, read, Healthy
            .Replace("\"summary\":\"ok\"", "\"summary\":\"slow\"")
            .Replace("\"tls_ms\":180", "\"tls_ms\":2400"));
        Require(slowText.StartsWith("网络自检：连接偏慢。", StringComparison.Ordinal)
                && slowText.EndsWith("请更换网络后重试。", StringComparison.Ordinal),
            "labyrinth_network_explains_slow_handshake");

        var failedText = Describe(describe, read, Healthy
            .Replace("\"summary\":\"ok\"", "\"summary\":\"failed\"")
            .Replace(GameRow, "{\"target\":\"game\",\"host\":\"l3-prod-all-gs-gzlj.bilibiligame.net\","
                + "\"ok\":false,\"dns_ms\":5,\"tcp_ms\":null,\"tls_ms\":null,"
                + "\"error\":\"tls_timeout\",\"attempts\":2}"));
        Require(failedText.StartsWith("网络自检：存在无法连接的目标。", StringComparison.Ordinal)
                && failedText.Contains("游戏服无法连接（握手超时）", StringComparison.Ordinal)
                && failedText.Contains("登录服握手", StringComparison.Ordinal),
            "labyrinth_network_names_the_failed_target");

        bool Rejected(string json)
        {
            try { read.Invoke(null, new object[] { json }); return false; }
            catch (TargetInvocationException ex) when (ex.InnerException is InvalidDataException) { return true; }
        }
        Require(Rejected(Healthy.Replace("line1-sdk-center-login-sh.biligame.net", "attacker.invalid")),
            "labyrinth_network_rejects_unknown_host");
        Require(Rejected(Healthy.Replace(SdkRow, SdkRow.Replace("line1-sdk-center-login-sh.biligame.net",
                "github.com"))),
            "labyrinth_network_rejects_host_target_mismatch");
        Require(Rejected(Healthy.Replace("\"target\":\"sdk\"", "\"target\":\"other\"")),
            "labyrinth_network_rejects_unknown_target");
        Require(Rejected(Healthy.Replace("\"version\":1", "\"version\":2")),
            "labyrinth_network_rejects_unknown_version");
        Require(Rejected(Healthy.Replace("\"summary\":\"ok\"", "\"summary\":\"complete\"")),
            "labyrinth_network_rejects_unknown_summary");
        Require(Rejected(Healthy.Replace("\"target\":\"game\",", "\"target\":\"game\",\"extra\":1,")),
            "labyrinth_network_rejects_extra_field");
        Require(Rejected(Healthy.Replace("\"error\":null", "\"error\":\"private detail\"")),
            "labyrinth_network_rejects_unknown_error_code");
        Require(Rejected(Healthy.Replace("\"tls_ms\":180,", "")),
            "labyrinth_network_rejects_missing_timing");
        Require(Rejected(Healthy.Replace("\"summary\":\"ok\"", "\"summary\":\"failed\"")),
            "labyrinth_network_rejects_summary_mismatch");
        Require(Rejected(Healthy.Replace(GameRow, SdkRow)),
            "labyrinth_network_rejects_duplicate_target");
        Require(Rejected(Healthy + new string('x', 5000)),
            "labyrinth_network_rejects_oversized_report");

        return count;
    }

    private static string Describe(MethodInfo describe, MethodInfo read, string json) =>
        (string)describe.Invoke(null, new[] { read.Invoke(null, new object[] { json })! })!;
}
