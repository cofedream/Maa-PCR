using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MFAAvalonia.Pcr;

// A narrow, ephemeral stdout message. Never include its URL in UI logs or errors.
internal sealed class PcrLabyrinthCaptchaMessage
{
    private static readonly Regex PageUrl = new(
        @"\Ahttp://127\.0\.0\.1:([1-9][0-9]{0,4})/[A-Za-z0-9_-]{43}/\z",
        RegexOptions.CultureInvariant);
    internal string Stage { get; }
    internal string Url { get; }
    private PcrLabyrinthCaptchaMessage(string stage, string url) { Stage=stage; Url=url; }
    public override string ToString() => "Labyrinth human captcha message";

    internal static bool TryParse(string? line, out PcrLabyrinthCaptchaMessage? message)
    {
        message=null;
        if (line is null || line.Length>512) return false;
        try {
            using var document=JsonDocument.Parse(line,new JsonDocumentOptions { MaxDepth=2 });
            if (document.RootElement.ValueKind!=JsonValueKind.Object) return false;
            var fields=new Dictionary<string,string>(StringComparer.Ordinal);
            foreach (var property in document.RootElement.EnumerateObject()) {
                if (property.Name is not ("kind" or "stage" or "url")
                    || property.Value.ValueKind!=JsonValueKind.String
                    || !fields.TryAdd(property.Name,property.Value.GetString()!)) return false;
            }
            if (fields.Count!=3 || fields["kind"]!="captcha"
                || fields["stage"] is not ("sdk" or "game")) return false;
            var url=fields["url"];
            var match=PageUrl.Match(url);
            if (!match.Success || !int.TryParse(match.Groups[1].Value,NumberStyles.None,
                    CultureInfo.InvariantCulture,out var port) || port is <1 or >65535) return false;
            message=new(fields["stage"],url);
            return true;
        } catch (JsonException) { return false; }
    }

    internal void OpenPage(Action<string> openPage)
    {
        try { openPage(Url); }
        catch {
            // Shell/browser exceptions may contain the nonce. Do not retain them.
            throw new InvalidOperationException("无法打开人工验证码页面，本次任务已停止。");
        }
    }
}
