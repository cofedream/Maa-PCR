using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Microsoft.Web.WebView2.Core;
using MFAAvalonia.Helper;

namespace MFAAvalonia.Pcr;

/// <summary>
/// Owns the WebView2 child window used by <see cref="PcrCaptchaOverlay"/>.
/// Only the loopback captcha page is allowed to navigate; the URL never enters
/// logs and the browser profile is an ephemeral per-process temp directory.
/// </summary>
internal sealed class PcrCaptchaWebHost : IDisposable
{
    private static CoreWebView2Environment? environment;
    private static readonly object environmentLock = new();

    private readonly PcrCaptchaOverlay owner;
    private CoreWebView2Controller? controller;
    private System.Drawing.Rectangle lastBounds = System.Drawing.Rectangle.Empty;
    private string? captchaUrl;
    private bool disposed;
    private bool navigationCompleted;
    private bool wantedVisible = true;

    internal PcrCaptchaWebHost(PcrCaptchaOverlay owner) => this.owner = owner;

    internal async Task EnsureAsync()
    {
        if (disposed) throw new InvalidOperationException("captcha host is closed");
        if (controller != null) return;
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("embedded captcha window requires Windows");
        var window = TopLevel.GetTopLevel(owner) as Window
            ?? throw new InvalidOperationException("captcha overlay has no top level window");
        var handle = window.TryGetPlatformHandle()?.Handle
            ?? throw new InvalidOperationException("captcha overlay has no native window handle");
        var environment = await GetEnvironmentAsync();
        var created = await environment.CreateCoreWebView2ControllerAsync(handle);
        // The layer may have been closed (user stopped the task) during creation.
        if (disposed)
        {
            try { created.Close(); } catch { }
            throw new InvalidOperationException("captcha host is closed");
        }
        controller = created;
        var core = controller.CoreWebView2;
        var settings = core.Settings;
        settings.AreDevToolsEnabled = false;
        settings.AreDefaultContextMenusEnabled = false;
        settings.IsStatusBarEnabled = false;
        settings.IsZoomControlEnabled = false;
        settings.AreBrowserAcceleratorKeysEnabled = false;
        core.NewWindowRequested += (_, args) => args.Handled = true;
        core.NavigationStarting += (_, args) =>
        {
            // Geetest assets load inside the page; only the one captcha document
            // itself may become a top-level navigation.
            if (args.Uri == "about:blank") return;
            if (captchaUrl != null && string.Equals(args.Uri, captchaUrl, StringComparison.Ordinal)) return;
            args.Cancel = true;
        };
        core.ProcessFailed += (_, _) =>
        {
            LoggerHelper.Warn("人工验证窗口的浏览器进程异常退出，正在请求取消本次验证。");
            _ = PostCancelAsync(captchaUrl);
        };
        core.NavigationCompleted += (_, _) =>
        {
            // Keep the native child hidden until the first document paints; an
            // unpainted WebView2 child can otherwise show the desktop through the
            // card for a frame while the page is loading.
            navigationCompleted = true;
            ApplyVisible();
            UpdateBounds();
        };
        controller.ShouldDetectMonitorScaleChanges = false;
        controller.BoundsMode = CoreWebView2BoundsMode.UseRawPixels;
        ApplyVisible();
        UpdateBounds();
    }

    internal void Navigate(string url)
    {
        captchaUrl = url;
        navigationCompleted = false;
        ApplyVisible();
        controller?.CoreWebView2.Navigate(url);
        UpdateBounds();
    }

    internal void SetVisible(bool visible)
    {
        wantedVisible = visible;
        ApplyVisible();
    }

    private void ApplyVisible()
    {
        if (controller != null) controller.IsVisible = navigationCompleted && wantedVisible;
    }

    /// <summary>Updates the native child rectangle from the Avalonia placeholder.</summary>
    internal void UpdateBounds()
    {
        if (controller == null) return;
        if (TopLevel.GetTopLevel(owner) is not Window window) return;
        var origin = owner.HostControl.TranslatePoint(new Point(0, 0), window);
        if (origin is null) return;
        var scale = window.RenderScaling;
        var width = (int)Math.Round(owner.HostControl.Bounds.Width * scale);
        var height = (int)Math.Round(owner.HostControl.Bounds.Height * scale);
        if (width <= 0 || height <= 0) return;
        var rect = new System.Drawing.Rectangle(
            (int)Math.Round(origin.Value.X * scale),
            (int)Math.Round(origin.Value.Y * scale),
            width, height);
        if (rect == lastBounds) return;
        lastBounds = rect;
        if (Math.Abs(controller.RasterizationScale - scale) > 0.001)
            controller.RasterizationScale = scale;
        controller.Bounds = rect;
    }

    /// <summary>
    /// Cancels the pending human wait so the login process stops cleanly instead
    /// of waiting for its own timeout. Uses the page's own loopback endpoint, so
    /// the proof never leaves the local process pair.
    /// </summary>
    internal async Task<bool> CancelAsync(string url)
    {
        if (await PostCancelAsync(url)) return true;
        if (controller?.CoreWebView2 is not { } core) return false;
        try
        {
            var result = await core.ExecuteScriptAsync(
                "(function(){var b=document.getElementById('cancel');if(!b)return 'missing';b.click();return 'clicked';})()");
            return result.Contains("clicked", StringComparison.Ordinal);
        }
        catch
        {
            return false;
        }
    }

    private static async Task<bool> PostCancelAsync(string? url)
    {
        if (string.IsNullOrEmpty(url) || !Uri.TryCreate(url, UriKind.Absolute, out var page)) return false;
        var origin = page.GetLeftPart(UriPartial.Authority);
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
            using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(page, "cancel"));
            request.Headers.TryAddWithoutValidation("Origin", origin);
            var body = new ByteArrayContent(Encoding.UTF8.GetBytes("{}"));
            body.Headers.ContentType = new MediaTypeHeaderValue("application/json");
            request.Content = body;
            using var response = await client.SendAsync(request);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private static async Task<CoreWebView2Environment> GetEnvironmentAsync()
    {
        lock (environmentLock)
        {
            if (environment != null) return environment;
        }
        string? installed;
        try
        {
            installed = CoreWebView2Environment.GetAvailableBrowserVersionString();
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException("WebView2 runtime is unavailable", ex);
        }
        if (string.IsNullOrEmpty(installed))
            throw new InvalidOperationException("WebView2 runtime is unavailable");
        var folder = Path.Combine(Path.GetTempPath(), "MaaPCR-WebView2",
            Environment.ProcessId.ToString(CultureInfo.InvariantCulture));
        CleanupStaleProfiles(Path.GetDirectoryName(folder)!);
        Directory.CreateDirectory(folder);
        var created = await CoreWebView2Environment.CreateAsync(null, folder);
        lock (environmentLock)
        {
            environment ??= created;
            return environment;
        }
    }

    /// <summary>
    /// Removes profiles left by earlier runs whose process is gone. The browser
    /// profile is ephemeral; a live MAA instance keeps its own directory.
    /// </summary>
    private static void CleanupStaleProfiles(string parent)
    {
        try
        {
            foreach (var directory in Directory.EnumerateDirectories(parent))
            {
                if (!int.TryParse(Path.GetFileName(directory), NumberStyles.None,
                        CultureInfo.InvariantCulture, out var pid) || pid == Environment.ProcessId)
                    continue;
                try { _ = Process.GetProcessById(pid); continue; }
                catch (ArgumentException) { }
                catch { continue; }
                try { Directory.Delete(directory, true); } catch { }
            }
        }
        catch
        {
            // Best effort only; a locked profile is reclaimed by the next run.
        }
    }

    public void Dispose()
    {
        disposed = true;
        try { controller?.Close(); } catch { /* page URL is never logged */ }
        controller = null;
        lastBounds = System.Drawing.Rectangle.Empty;
        captchaUrl = null;
    }
}
