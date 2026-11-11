using System;
using System.Collections.Generic;

namespace MFAAvalonia.Pcr;

/// <summary>
/// Fixed transport policy for the game-protocol children.
///
/// The CN game servers are reached directly. A desktop proxy must never be
/// inherited: requests and urllib otherwise pick up the Windows registry proxy,
/// so one proxy hiccup aborts a whole batch as network_failed/login_failed.
/// Online update checks and downloads keep their own proxy setting and are not
/// affected by this file.
/// </summary>
internal static class PcrNetworkProxy
{
    // Windows environment names are case-insensitive; the child block matches them that way.
    private static readonly string[] Inherited = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"];

    /// <summary>Child overrides: drop every inherited proxy variable and force a full bypass.</summary>
    internal static IReadOnlyDictionary<string, string?> DirectEnvironment()
    {
        var overrides = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var key in Inherited) overrides[key] = null;
        overrides["NO_PROXY"] = "*";
        return overrides;
    }
}
