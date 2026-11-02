using System.Threading.Tasks;

namespace MFAAvalonia.Pcr;

/// <summary>
/// Bridge between the run owners and the single main-window captcha layer.
/// The overlay is created by <c>RootViewContent</c>; when it is not present the
/// caller keeps the existing default-browser path.
/// </summary>
internal static class PcrCaptchaHost
{
    private static PcrCaptchaOverlay? overlay;

    internal static void Attach(PcrCaptchaOverlay value) => overlay = value;

    internal static void Detach(PcrCaptchaOverlay value)
    {
        if (ReferenceEquals(overlay, value)) overlay = null;
    }

    /// <summary>Returns false when the page must be opened in the default browser.</summary>
    internal static Task<bool> OpenAsync(string url)
    {
        var target = overlay;
        return target is null ? Task.FromResult(false) : target.ShowAsync(url);
    }

    /// <summary>Hides the layer; called when the captcha wait has resolved or the run ends.</summary>
    internal static void Hide() => overlay?.Hide();
}
