using System;
using System.Reflection;
using System.Threading.Tasks;
using Avalonia.Automation;
using Avalonia.Controls;
using MFAAvalonia.Pcr;

internal static class CaptchaOverlayChecks
{
    internal static void Run()
    {
        var overlay = new PcrCaptchaOverlay();
        Check(!overlay.IsVisible, "captcha_overlay_hidden_by_default");
        Check(overlay.FindControl<Border>("WebHost") != null, "captcha_overlay_has_native_host_slot");
        var cancel = overlay.FindControl<Button>("CancelButton");
        var browser = overlay.FindControl<Button>("BrowserButton");
        Check(cancel != null && AutomationProperties.GetName(cancel) == "取消本次人工验证",
            "captcha_cancel_button_is_named");
        Check(browser != null && AutomationProperties.GetName(browser) == "在浏览器中打开验证页面",
            "captcha_browser_button_is_named");
        Check(overlay.FindControl<TextBlock>("StatusText") != null, "captcha_status_text_exists");

        // With no main-window layer attached (headless contract tests, early startup,
        // or a future non-Windows build) the owners must get the browser fallback.
        var host = typeof(PcrCaptchaOverlay).Assembly.GetType("MFAAvalonia.Pcr.PcrCaptchaHost", true)!;
        var open = host.GetMethod("OpenAsync", BindingFlags.Static | BindingFlags.NonPublic)!;
        var url = "http://127.0.0.1:1/" + new string('a', 43) + "/";
        var task = (Task<bool>)open.Invoke(null, new object[] { url })!;
        Check(task.Wait(TimeSpan.FromSeconds(5)) && !task.Result, "captcha_without_overlay_requests_browser_fallback");
        host.GetMethod("Hide", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, null);
        Console.WriteLine("captcha overlay checks passed");
    }

    private static void Check(bool condition, string name)
    {
        if (!condition) throw new InvalidOperationException("captcha overlay check failed: " + name);
    }
}
