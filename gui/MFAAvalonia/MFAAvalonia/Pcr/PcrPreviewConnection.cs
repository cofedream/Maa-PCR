using System;
using System.Threading;
using System.Threading.Tasks;
using MaaFramework.Binding;
using MaaFramework.Binding.Buffers;
using MFAAvalonia.Extensions.MaaFW;

namespace MFAAvalonia.Pcr;

// Native controller jobs have no abort API. Keep their owner until both the call
// and disposal return; cancelling the UI wait must never release that owner.
internal sealed class PcrPreviewConnection
{
    private readonly object gate = new();
    private readonly CancellationTokenSource cancellation = new();
    private readonly MaaProcessor processor;
    private readonly Task preparation;
    private Task? cleanup;
    private MaaController? controller;
    private MaaResource? resource;
    private MaaTasker? preview;
    private MaaImageBuffer? firstFrame;
    private MaaTasker? previousTasker, previousScreenshotTasker;
    private bool previousCleanupUnconfirmed;
    private bool cancelled, closed;

    internal PcrPreviewConnection(MaaProcessor processor, AdbDeviceInfo device,
        Func<AdbDeviceInfo, MaaController> factory)
    {
        this.processor = processor;
        preparation = Task.Run(() =>
        {
            if (processor.MaaTasker != null || processor.ScreenshotTasker != null)
            {
                previousTasker = processor.MaaTasker;
                previousScreenshotTasker = processor.ScreenshotTasker;
                try { processor.SetTasker(requireAllStopped: true, waitForNativeCleanup: true); }
                catch { previousCleanupUnconfirmed = true; throw; }
                previousTasker = null;
                previousScreenshotTasker = null;
            }
            cancellation.Token.ThrowIfCancellationRequested();
            controller = factory(device);
            cancellation.Token.ThrowIfCancellationRequested();
            resource = new MaaResource();
            preview = new MaaTasker(controller, resource, DisposeOptions.None, false);
            if (!controller.SetOption_ScreenshotUseRawSize(true))
                throw new InvalidOperationException("Maa 设备连接失败。");
            cancellation.Token.ThrowIfCancellationRequested();
            if (controller.LinkStart().Wait() != MaaJobStatus.Succeeded || !controller.IsConnected)
                throw new InvalidOperationException("Maa 设备连接失败。");
            cancellation.Token.ThrowIfCancellationRequested();
            firstFrame = processor.GetImage(controller);
            if (firstFrame == null || !controller.IsConnected
                || !firstFrame.TryGetRawData(out var pixels, out var width, out var height, out _)
                || pixels == IntPtr.Zero || width <= 0 || height <= 0 || firstFrame.Channels is not (3 or 4))
                throw new InvalidOperationException("Maa 未取得有效设备画面。");
            cancellation.Token.ThrowIfCancellationRequested();
        });
    }

    internal CancellationToken Token => cancellation.Token;
    internal Task WaitAsync(TimeSpan timeout) => preparation.WaitAsync(timeout, cancellation.Token);

    internal void Cancel()
    {
        lock (gate)
        {
            if (cancelled || closed) return;
            cancelled = true;
            cancellation.Cancel();
        }
    }

    internal MaaImageBuffer TakeFirstFrame(Action connected)
    {
        lock (gate)
        {
            cancellation.Token.ThrowIfCancellationRequested();
            if (!preparation.IsCompletedSuccessfully || cleanup != null || preview == null || firstFrame == null)
                throw new InvalidOperationException("PCR 预览连接尚未就绪。");
            connected();
            var image = firstFrame;
            firstFrame = null;
            return image;
        }
    }

    internal void Transfer()
    {
        lock (gate)
        {
            cancellation.Token.ThrowIfCancellationRequested();
            if (!preparation.IsCompletedSuccessfully || cleanup != null || preview == null)
                throw new InvalidOperationException("PCR 预览连接尚未就绪。");
            preview.DisposeOptions = DisposeOptions.All;
            try { processor.SetTasker(preview); }
            finally
            {
                // SetTasker can throw after publishing. Once published, only the
                // processor may dispose it; late cleanup never touches Processor.
                if (ReferenceEquals(processor.MaaTasker, preview))
                {
                    preview = null;
                    controller = null;
                    resource = null;
                }
                else if (preview != null) preview.DisposeOptions = DisposeOptions.None;
            }
        }
    }

    internal Task CleanupAsync()
    {
        lock (gate)
            return cleanup ??= Task.Run(async () =>
            {
                try { await preparation.ConfigureAwait(false); }
                catch { } // The foreground wait reports preparation errors.
                if (previousCleanupUnconfirmed)
                    throw new InvalidOperationException("先前连接的原生清理结果未确认，保留隔离直到重新启动程序。");
                firstFrame?.Dispose();
                preview?.Dispose();
                controller?.Dispose();
                resource?.Dispose();
                lock (gate)
                {
                    closed = true;
                    cancellation.Dispose();
                }
            });
    }
}
