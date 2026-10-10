using MaaFramework.Binding;
using MaaFramework.Binding.Buffers;

namespace MFAAvalonia.Pcr;

/// <summary>The outer Maa job may succeed after its sole PCR action failed.</summary>
public static class PcrTaskResult
{
    public static MaaJobStatus Status(IMaaTasker tasker, long taskId, MaaJobStatus outer)
    {
        if (outer != MaaJobStatus.Succeeded) return outer;
        try
        {
            if (!tasker.GetTaskDetail(taskId, out var entry, out var nodes, out var status)
                || entry != "PCRAlphaEntry" || status != MaaJobStatus.Succeeded || nodes == null)
                return MaaJobStatus.Failed;
            var matches = 0;
            foreach (var id in nodes)
            {
                if (!tasker.GetNodeDetail(id, out var name, out _, out var actionId, out var completed))
                    return MaaJobStatus.Failed;
                if (name != "PCRAlphaEntry") continue;
                if (++matches != 1 || !completed || actionId <= 0) return MaaJobStatus.Failed;
                using var box = new MaaRectBuffer();
                if (!tasker.GetActionDetail(actionId, out var actionNode, out var action,
                        box, out var succeeded, out _)
                    || actionNode != "PCRAlphaEntry" || action != "Custom" || !succeeded)
                    return MaaJobStatus.Failed;
            }
            return matches == 1 ? MaaJobStatus.Succeeded : MaaJobStatus.Failed;
        }
        catch { return MaaJobStatus.Failed; }
    }
}
