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

    private PcrCaptchaWebHost? host;
    private Window? window;
    private string? currentUrl;

    public PcrCaptchaOverlay()
    {
        AvaloniaXamlLoader.Load(this);
        overlayRoot = this.FindControl<Grid>("OverlayRoot");
        card = this.FindControl<Border>("Card");
        webHost = this.FindControl<Border>("WebHost");
        statusText = this.FindControl<TextBlock>("StatusText");
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
            SetStatus("请完成官方极验验证；完成后会自动继续本次任务。");
            return true;
        }
        catch (Exception ex)
        {
            var wasVisible = IsVisible;
            HideCore();
            if (!wasVisible) return true; // the task stopped while the layer was initializing
            // Never put the one-use page URL or exception text into logs.
            LoggerHelper.Warn("软件内人工验证窗口不可用，本次改用浏览器打开验证页面。");
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
        try { host?.Dispose(); } catch { /* never keep a page URL in logs */ }
        host = null;
    }

    private void UpdateCardSize()
    {
        if (overlayRoot == null || card == null) return;
        var bounds = overlayRoot.Bounds;
        if (bounds.Width <= 0 || bounds.Height <= 0) return;
        var width = Math.Clamp(bounds.Width - 72, 420, 640);
        var height = Math.Clamp(bounds.Height - 56, 430, 700);
        if (Math.Abs(card.Width - width) > 0.5) card.Width = width;
        if (Math.Abs(card.Height - height) > 0.5) card.Height = height;
    }

    private void SetStatus(string text)
    {
        if (statusText != null) statusText.Text = text;
    }

    private async void CancelVerification(object? sender, RoutedEventArgs e)
    {
        var url = currentUrl;
        if (url is null || host is null) return;
        SetStatus("正在取消本次验证…");
        try
        {
            var accepted = await host.CancelAsync(url);
            SetStatus(accepted
                ? "已请求取消，正在等待登录进程确认…"
                : "取消失败；请再试一次，或关闭本次任务。");
        }
        catch
        {
            SetStatus("取消失败；请再试一次，或关闭本次任务。");
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
