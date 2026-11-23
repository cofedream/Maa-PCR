using System;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
using MFAAvalonia.Helper;
using MFAAvalonia.Utilities;

namespace MFAAvalonia.Pcr;

/// <summary>
/// Main-window modal layer for the human Geetest page. The page stays on loopback
/// (127.0.0.1) and only its display moves into MAA; the login process keeps the
/// credentials and consumes the one-use proof.
/// </summary>
public partial class PcrCaptchaOverlay : UserControl
{
    private Grid? overlayRoot;
    private Border? card;
    private Border? webHost;
    private TextBlock? statusText;
    private Button? copyButton;

    private PcrCaptchaWebHost? host;
    private Window? window;
    private string? currentUrl;
    // The embedded WebView2 child failed; the card stays visible so the player
    // can still open or copy the same page themselves.
    private bool browserFallback;

    public PcrCaptchaOverlay()
    {
        AvaloniaXamlLoader.Load(this);
        overlayRoot = this.FindControl<Grid>("OverlayRoot");
        card = this.FindControl<Border>("Card");
        webHost = this.FindControl<Border>("WebHost");
        statusText = this.FindControl<TextBlock>("StatusText");
        copyButton = this.FindControl<Button>("CopyButton");
        LayoutUpdated += (_, _) =>
        {
            if (!IsVisible) return;
            UpdateCardSize();
            host?.UpdateBounds();
        };
    }

    internal Control HostControl =>
        webHost ?? throw new InvalidOperationException("captcha overlay is not loaded");

    protected override void OnAttachedToVisualTree(VisualTreeAttachmentEventArgs e)
    {
        base.OnAttachedToVisualTree(e);
        PcrCaptchaHost.Attach(this);
        window = TopLevel.GetTopLevel(this) as Window;
        if (window != null) window.PropertyChanged += OnWindowPropertyChanged;
    }

    protected override void OnDetachedFromVisualTree(VisualTreeAttachmentEventArgs e)
    {
        if (window != null)
        {
            window.PropertyChanged -= OnWindowPropertyChanged;
            window = null;
        }
        PcrCaptchaHost.Detach(this);
        HideCore();
        base.OnDetachedFromVisualTree(e);
    }

    private void OnWindowPropertyChanged(object? sender, AvaloniaPropertyChangedEventArgs e)
    {
        if (e.Property == Window.WindowStateProperty && window != null)
        {
            // A hidden native child would otherwise block nothing; keep it in sync
            // with the minimized main window and recompute after restoring.
            host?.SetVisible(window.WindowState != WindowState.Minimized);
            if (window.WindowState != WindowState.Minimized) host?.UpdateBounds();
        }
    }

    /// <summary>
    /// Shows the verification page inside the main window. Returns false when the
    /// embedded window is unavailable so the caller can fall back to the browser.
    /// </summary>
    internal Task<bool> ShowAsync(string url)
    {
        if (Dispatcher.UIThread.CheckAccess()) return ShowCoreAsync(url);
        var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        Dispatcher.UIThread.Post(async () =>
        {
            try { completion.TrySetResult(await ShowCoreAsync(url)); }
            catch (Exception ex) { completion.TrySetException(ex); }
        });
        return completion.Task;
    }

    private async Task<bool> ShowCoreAsync(string url)
    {
        try
        {
            currentUrl = url;
            IsVisible = true;
            UpdateCardSize();
            SetStatus("正在打开验证窗口…");
            if (window != null)
            {
                if (window.WindowState == WindowState.Minimized)
                    window.WindowState = WindowState.Normal;
                // The human wait must be visible even if the user switched away
                // while the task was running.
                window.Activate();
            }
            host ??= new PcrCaptchaWebHost(this);
            await host.EnsureAsync();
            var layer = host;
            if (layer is null || currentUrl != url || !IsVisible) return true;
            layer.Navigate(url);
            if (copyButton != null) copyButton.IsVisible = false;
            SetStatus("请完成官方极验验证；完成后会自动继续本次任务。");
            return true;
        }
        catch (Exception ex)
        {
            // The task may have stopped while WebView2 was initializing; keep the
            // previous "already closed" behavior then.
            if (!IsVisible) { HideCore(); return true; }
            ReleaseHost();
            browserFallback = true;
            if (webHost != null) { webHost.IsVisible = false; webHost.MinHeight = 0; }
            if (copyButton != null) copyButton.IsVisible = true;
            SetStatus("软件内验证窗口不可用，已在浏览器中打开验证页面。如果浏览器没有出现，"
                + "可点「在浏览器中打开」或「复制验证链接」后粘贴完成。");
            // Never put the one-use page URL or exception text into logs.
            var stage = ex.Data["captcha_stage"] as string ?? "show";
            LoggerHelper.Warn($"软件内人工验证窗口不可用（阶段 {stage}，{ex.GetType().Name}），本次改用浏览器打开验证页面。");
            LoggerHelper.Debug("captcha window failure: " + ex.GetType().FullName);
            return false;
        }
    }

    /// <summary>Hides the layer and releases the native child; safe on any thread.</summary>
    internal void Hide()
    {
        if (!Dispatcher.UIThread.CheckAccess())
        {
            Dispatcher.UIThread.Post(Hide);
            return;
        }
        if (!IsVisible && host == null) return;
        HideCore();
    }

    private void HideCore()
    {
        IsVisible = false;
        currentUrl = null;
        browserFallback = false;
        ReleaseHost();
        if (webHost != null) { webHost.IsVisible = true; webHost.MinHeight = 260; }
        if (card != null) card.Height = double.NaN;
    }

    private void ReleaseHost()
    {
        try { host?.Dispose(); } catch { /* never keep a page URL in logs */ }
        host = null;
    }

    private void UpdateCardSize()
    {
        if (overlayRoot == null || card == null) return;
        var bounds = overlayRoot.Bounds;
        if (bounds.Width <= 0 || bounds.Height <= 0) return;
        var width = Math.Clamp(bounds.Width - 72, 420, 640);
        if (Math.Abs(card.Width - width) > 0.5) card.Width = width;
        if (browserFallback)
        {
            // The page area is collapsed; let header, status and buttons size it.
            if (!double.IsNaN(card.Height)) card.Height = double.NaN;
            return;
        }
        var height = Math.Clamp(bounds.Height - 56, 430, 700);
        if (Math.Abs(card.Height - height) > 0.5) card.Height = height;
    }

    private void SetStatus(string text)
    {
        if (statusText != null) statusText.Text = text;
    }

    private async void CancelVerification(object? sender, RoutedEventArgs e)
    {
        var url = currentUrl;
        if (url is null) return;
        SetStatus("正在取消本次验证…");
        try
        {
            // The browser fallback has no WebView2 to click the page's own cancel
            // button, but the loopback endpoint is the same.
            var accepted = host != null
                ? await host.CancelAsync(url)
                : await PcrCaptchaHost.CancelPageAsync(url);
            SetStatus(accepted
                ? "已请求取消，正在等待登录进程确认…"
                : "取消失败；请再试一次，或关闭本次任务。");
        }
        catch
        {
            SetStatus("取消失败；请再试一次，或关闭本次任务。");
        }
    }

    private async void CopyLink(object? sender, RoutedEventArgs e)
    {
        var url = currentUrl;
        if (url is null) return;
        try
        {
            var clipboard = TopLevel.GetTopLevel(this)?.Clipboard ?? Instances.TopLevel?.Clipboard;
            if (clipboard is null)
            {
                SetStatus("剪贴板不可用，请用「在浏览器中打开」。");
                return;
            }
            await clipboard.SetTextAsync(url);
            SetStatus("验证链接已复制；请粘贴到浏览器打开，完成后会自动继续。");
        }
        catch
        {
            SetStatus("复制失败，请用「在浏览器中打开」或取消本次验证。");
        }
    }

    private void OpenInBrowser(object? sender, RoutedEventArgs e)
    {
        var url = currentUrl;
        if (url is null) return;
        try
        {
            UrlUtilities.OpenUrl(url);
            SetStatus("已在浏览器中打开同一验证页面；在任意一处完成即可。");
        }
        catch
        {
            SetStatus("无法打开浏览器，请重试或取消本次验证。");
        }
    }
}
