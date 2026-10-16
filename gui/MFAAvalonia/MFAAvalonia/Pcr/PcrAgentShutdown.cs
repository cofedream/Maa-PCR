using System;
using System.Threading.Tasks;

namespace MFAAvalonia.Pcr;

// A deadline abandons no SDK operation: the owner retains a call until it returns.
internal sealed class PcrAgentShutdown
{
    private readonly object gate = new();
    private Task? pending;
    private bool uncertain;
    private TimeSpan grace = TimeSpan.FromSeconds(5), afterExit = TimeSpan.FromSeconds(5);
    private volatile bool forced;
    internal bool Forced => forced;
    internal bool Quiescent { get { lock (gate) return pending == null && !uncertain; } }
    internal bool TryRun(Action operation, Action terminate)
    {
        Task call;
        lock (gate)
        {
            if (uncertain || pending != null) return false;
            pending = call = Task.Run(operation);
        }
        var terminationAttempted = false;
        try
        {
            if (!call.Wait(grace))
            {
                forced = true;
                terminationAttempted = true;
                terminate();
                if (!call.Wait(afterExit)) { lock (gate) uncertain = true; return false; }
            }
            call.GetAwaiter().GetResult();
            return true;
        }
        catch
        {
            _ = call.Exception;
            if (!terminationAttempted)
            {
                forced = true;
                try { terminate(); } catch { }
            }
            lock (gate) uncertain = true;
            return false;
        }
        finally
        {
            lock (gate) if (call.IsCompleted) pending = null;
        }
    }
}
