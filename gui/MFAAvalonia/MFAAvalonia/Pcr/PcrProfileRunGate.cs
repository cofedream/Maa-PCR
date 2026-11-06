using System.Linq;
using MFAAvalonia.Extensions.MaaFW;
namespace MFAAvalonia.Pcr;
internal static class PcrProfileRunGate
{
    internal static readonly object StartGate = new();
    internal static volatile bool UpdatePending;
    internal static volatile bool ShutdownPending;
    internal static bool StartBlocked => UpdatePending || ShutdownPending;
    internal static bool ConnectionPending => MaaProcessorManager.IsInstanceCreated
        && MaaProcessorManager.Instance.Instances.Any(p => p.ViewModel?.PcrConnectionPending == true);
    internal static bool Busy => StartBlocked || HasRunningWork;
    internal static bool HasRunningWork => PcrAlphaOwner.HasActiveRun || PcrLabyrinthOwner.IsActive || PcrGuideSyncOwner.IsActive
        || MaaProcessorManager.IsInstanceCreated && MaaProcessorManager.Instance.Instances.Any(p => p.IsTaskRunActive || p.IsConnecting || p.ViewModel?.IsRunning == true || p.ViewModel?.PcrTargetBusy == true || p.ViewModel?.PcrConnectionPending == true);
    internal static bool OtherProfileBusy(string id) =>
        PcrGuideSyncOwner.IsActive || PcrAlphaOwner.HasActiveRun && PcrAlphaOwner.ActiveProfile != id
        || PcrLabyrinthOwner.IsActive && id != "pcr-labyrinth-v1"
        || MaaProcessorManager.IsInstanceCreated && MaaProcessorManager.Instance.Instances.Any(p => p.InstanceId != id && (p.IsTaskRunActive || p.IsConnecting || p.ViewModel?.IsRunning == true || p.ViewModel?.PcrConnectionPending == true));
    internal static void Notify() {
        if (MaaProcessorManager.IsInstanceCreated)
            foreach (var p in MaaProcessorManager.Instance.Instances) p.ViewModel?.NotifyPcrDiagnosticBoundary();
        if (MFAAvalonia.Helper.Instances.TryGetResolved<MFAAvalonia.ViewModels.UsersControls.Settings.VersionUpdateSettingsUserControlModel>(out var updates))
            updates.RefreshPcrUpdateAvailability();
    }
}
