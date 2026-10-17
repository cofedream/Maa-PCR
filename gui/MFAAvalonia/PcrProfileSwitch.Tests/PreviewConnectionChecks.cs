using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MaaFramework.Binding;
using MaaFramework.Binding.Buffers;
using MaaFramework.Binding.Custom;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Other;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json.Linq;

internal static class PreviewConnectionChecks
{
    private const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.Static | BindingFlags.NonPublic;
    private static readonly Type VmType = typeof(TaskQueueViewModel);
    private static readonly Type Gate = VmType.Assembly.GetType("MFAAvalonia.Pcr.PcrProfileRunGate", true)!;
    private static int checks;
    private static string adbPath = "";

    internal static void Run(MaaProcessorManager manager, string fixtureDirectory)
    {
        var directory = Path.Combine(fixtureDirectory, "preview-connection");
        Directory.CreateDirectory(directory);
        adbPath = Path.Combine(directory, "adb.exe");
        File.WriteAllText(adbPath, "OFFLINE FIXTURE - MUST NEVER EXECUTE");
        var previousContext = SynchronizationContext.Current;
        SynchronizationContext.SetSynchronizationContext(new AvaloniaSynchronizationContext(Dispatcher.UIThread, DispatcherPriority.Normal));
        try
        {
            CheckOwnerStartFixture(manager);
            foreach (var phase in new[] { "connect", "first_frame" })
            {
                CancelOrTimeout(manager, phase, timeout: false);
                CancelOrTimeout(manager, phase, timeout: true);
                ReturnAndStop(manager, phase);
            }
            CleanupBlocked(manager);
            CleanupFaulted(manager);
            ExistingPreviewCleanupBlocked(manager);
            RenderWaitBlocked(manager);
            FactoryLateReturn(manager);
            Console.WriteLine($"PCR preview connection checks passed: {checks}; synthetic native callbacks only; real ADB/device inputs=0");
        }
        finally { SynchronizationContext.SetSynchronizationContext(previousContext); }
    }

    private static void CheckOwnerStartFixture(MaaProcessorManager manager)
    {
        using var fixture = new Fixture(manager);
        var select = typeof(PcrAlphaOwner).GetProperty(nameof(PcrAlphaOwner.SelectConfiguration))!;
        var previous = select.GetValue(null);
        var selections = 0;
        try
        {
            PcrAlphaOwner.SelectConfiguration = () => { selections++; return Task.FromResult<string?>(null); };
            var request = PcrAlphaOwner.RequestStartAsync(fixture.Vm);
            Until(() => request.IsCompleted, "owner_fixture_reaches_inert_configuration_picker");
            request.GetAwaiter().GetResult();
            Require(selections == 1, "owner_fixture_would_reach_start_without_pending_connection_gate");
        }
        finally { select.SetValue(null, previous); }
    }

    private static void CancelOrTimeout(MaaProcessorManager manager, string phase, bool timeout)
    {
        var name = phase + (timeout ? "_timeout" : "_stop");
        using var fixture = new Fixture(manager);
        var blocked = new PreviewController(phase);
        fixture.Factory(blocked);
        fixture.Timeout(timeout ? TimeSpan.FromMilliseconds(100) : TimeSpan.FromSeconds(5));
        var request = fixture.Connect();
        Until(() => blocked.Entered.IsSet, name + "_enters_native");
        Require(fixture.Pending && fixture.Vm.PcrTargetBusy, name + "_owns_pending_connection");
        if (!timeout) StopTwice(fixture.Vm, name);
        Until(() => request.IsCompleted, name + "_request_completes_without_native_return", 2000);
        request.GetAwaiter().GetResult();
        if (timeout) StopTwice(fixture.Vm, name + "_stop_after_timeout");
        CheckQuarantine(fixture, name);
        Require(!blocked.Returned && blocked.DisposeCalls == 0 && fixture.Processor.MaaTasker == null,
            name + "_does_not_dispose_or_publish_in_use_controller");
        CheckOwnerRejected(fixture, name);
        blocked.Hold.Set();
        Until(() => !fixture.Pending, name + "_late_return_is_cleaned");
        Require(blocked.Disposed && blocked.DisposeCalls == 1 && !fixture.Vm.IsConnected
            && fixture.Processor.MaaTasker == null && fixture.Vm.CanEditPcrTarget,
            name + "_late_return_releases_once_without_publishing");

        var replacement = new PreviewController("none");
        fixture.Factory(replacement);
        var reconnect = fixture.Connect();
        Until(() => reconnect.IsCompleted, name + "_reconnect_completes");
        reconnect.GetAwaiter().GetResult();
        var tasker = fixture.Processor.MaaTasker;
        Require(fixture.Vm.IsConnected && !fixture.Pending && !fixture.Vm.PcrTargetBusy
            && tasker != null && ReferenceEquals(tasker.Controller, fixture.LastController),
            name + "_subsequent_connection_owns_new_controller");
        PumpFor(30);
        Require(fixture.Vm.IsConnected && ReferenceEquals(fixture.Processor.MaaTasker, tasker)
            && blocked.DisposeCalls == 1 && replacement.DisposeCalls == 0,
            name + "_old_completion_cannot_change_new_session");
        Require(blocked.Inputs == 0 && replacement.Inputs == 0, name + "_no_device_input");
        fixture.ReleaseTasker();
        Require(replacement.Disposed && replacement.DisposeCalls == 1, name + "_new_session_is_reclaimable");
        Console.WriteLine("PCR preview connection scenario passed: " + name + "; late native return cleaned once; reconnect succeeded");
    }

    private static void ReturnAndStop(MaaProcessorManager manager, string phase)
    {
        var name = phase + "_return_stop_race";
        using var fixture = new Fixture(manager);
        var native = new PreviewController(phase);
        fixture.Factory(native);
        var request = fixture.Connect();
        Until(() => native.Entered.IsSet, name + "_enters_native");
        // The UI continuation is queued on this dispatcher; Stop wins even if the native callback returns now.
        native.Hold.Set();
        StopTwice(fixture.Vm, name);
        Until(() => request.IsCompleted && !fixture.Pending, name + "_settles");
        request.GetAwaiter().GetResult();
        Require(!fixture.Vm.IsConnected && fixture.Processor.MaaTasker == null
            && native.Disposed && native.DisposeCalls == 1 && native.Inputs == 0,
            name + "_cannot_publish_cancelled_result");
        Console.WriteLine("PCR preview connection scenario passed: " + name);
    }

    private static void CleanupBlocked(MaaProcessorManager manager)
    {
        using var fixture = new Fixture(manager);
        var native = new PreviewController("connect", blockDispose: true);
        fixture.Factory(native);
        var request = fixture.Connect();
        Until(() => native.Entered.IsSet, "cleanup_block_enters_native");
        StopTwice(fixture.Vm, "cleanup_block");
        Until(() => request.IsCompleted, "cleanup_block_cancel_is_bounded", 2000);
        native.Hold.Set();
        Until(() => native.DisposeEntered.IsSet, "cleanup_block_enters_dispose");
        CheckQuarantine(fixture, "cleanup_block");
        Require(!native.Disposed && native.DisposeCalls == 1, "cleanup_block_retains_in_progress_dispose");
        StopTwice(fixture.Vm, "cleanup_block_repeat");
        CheckOwnerRejected(fixture, "cleanup_block");
        native.DisposeHold.Set();
        Until(() => !fixture.Pending, "cleanup_block_releases_after_dispose");
        Require(native.Disposed && native.DisposeCalls == 1 && fixture.Vm.CanEditPcrTarget,
            "cleanup_block_releases_isolation_only_after_dispose");
        Console.WriteLine("PCR preview connection scenario passed: cleanup_block; quarantine held until disposal returned");
    }

    private static void CleanupFaulted(MaaProcessorManager manager)
    {
        using var fixture = new Fixture(manager);
        var native = new PreviewController("connect", throwDispose: true);
        fixture.Factory(native);
        var request = fixture.Connect();
        Until(() => native.Entered.IsSet, "cleanup_fault_enters_native");
        StopTwice(fixture.Vm, "cleanup_fault");
        Until(() => request.IsCompleted, "cleanup_fault_cancel_is_bounded", 2000);
        native.Hold.Set();
        Until(() => native.Disposed, "cleanup_fault_reaches_dispose_failure");
        PumpFor(50);
        CheckQuarantine(fixture, "cleanup_fault");
        CheckOwnerRejected(fixture, "cleanup_fault");
        Require(native.DisposeCalls == 1 && native.Inputs == 0 && fixture.Processor.MaaTasker == null,
            "cleanup_fault_keeps_uncertain_cleanup_isolated");
        Console.WriteLine("PCR preview connection scenario passed: cleanup_fault; quarantine retained");
        // Only the isolated fixture is removed from the test manager; production quarantine is never reset.
    }

    private static void ExistingPreviewCleanupBlocked(MaaProcessorManager manager)
    {
        using var fixture = new Fixture(manager);
        var old = new PreviewController("none", blockInactive: true);
        fixture.Factory(old);
        var first = fixture.Connect();
        Until(() => first.IsCompleted && !fixture.Pending, "existing_cleanup_initial_connection_completes");
        first.GetAwaiter().GetResult();
        Require(fixture.Vm.IsConnected && fixture.Processor.MaaTasker != null, "existing_cleanup_has_owned_preview");
        var replacement = new PreviewController("none");
        fixture.Factory(replacement);
        var request = fixture.Connect();
        Until(() => old.InactiveEntered.IsSet, "existing_cleanup_blocks_in_old_native_stop");
        StopTwice(fixture.Vm, "existing_cleanup");
        Until(() => request.IsCompleted, "existing_cleanup_cancel_is_bounded", 2000);
        CheckQuarantine(fixture, "existing_cleanup");
        PumpFor(5200); // Exceed the old SetTasker five-second timeout while the native stop is still held.
        Require(fixture.Pending && GateFlag("Busy") && !old.Disposed && fixture.FactoryCalls == 1,
            "existing_cleanup_does_not_abandon_native_stop_after_old_timeout");
        CheckOwnerRejected(fixture, "existing_cleanup");
        old.InactiveHold.Set();
        Until(() => !fixture.Pending, "existing_cleanup_releases_after_old_stop_and_disposal");
        Require(old.Disposed && old.DisposeCalls == 1 && fixture.FactoryCalls == 1 && !fixture.Vm.IsConnected,
            "existing_cleanup_cancel_does_not_create_replacement");
        var reconnect = fixture.Connect();
        Until(() => reconnect.IsCompleted && !fixture.Pending, "existing_cleanup_subsequent_reconnect_completes");
        reconnect.GetAwaiter().GetResult();
        Require(fixture.Vm.IsConnected && fixture.FactoryCalls == 2 && old.DisposeCalls == 1
            && old.Inputs == 0 && replacement.Inputs == 0,
            "existing_cleanup_reconnect_uses_fresh_session");
        fixture.ReleaseTasker();
        Require(replacement.Disposed && replacement.DisposeCalls == 1, "existing_cleanup_replacement_is_reclaimable");
        Console.WriteLine("PCR preview connection scenario passed: existing_cleanup_block; old native stop held beyond five seconds; no replacement until safe disposal");
    }

    private static void RenderWaitBlocked(MaaProcessorManager manager)
    {
        var semaphore = (SemaphoreSlim)VmType.GetField("_liveViewSemaphore", Hidden)!.GetValue(null)!;
        var held = 0;
        while (semaphore.Wait(0)) held++;
        try
        {
            using var fixture = new Fixture(manager);
            var native = new PreviewController("none");
            fixture.Factory(native);
            var request = fixture.Connect();
            Until(() => fixture.Vm.IsConnected && fixture.Processor.MaaTasker == null, "renderer_wait_owns_first_frame_before_processor_transfer");
            StopTwice(fixture.Vm, "renderer_wait");
            Until(() => request.IsCompleted && !fixture.Pending, "renderer_wait_cancels_without_renderer_permit", 2000);
            request.GetAwaiter().GetResult();
            Require(!fixture.Vm.IsConnected && !fixture.Vm.PcrTargetBusy && fixture.Processor.MaaTasker == null
                && fixture.Vm.LiveViewImage == null && native.Disposed && native.DisposeCalls == 1 && native.Inputs == 0,
                "renderer_wait_disposes_unpublished_connection_once");
            semaphore.Release(held);
            held = 0;
            var replacement = new PreviewController("none");
            fixture.Factory(replacement);
            var reconnect = fixture.Connect();
            Until(() => reconnect.IsCompleted && !fixture.Pending, "renderer_wait_subsequent_reconnect_completes");
            reconnect.GetAwaiter().GetResult();
            Require(fixture.Vm.IsConnected && fixture.Processor.MaaTasker != null && native.DisposeCalls == 1,
                "renderer_wait_late_permit_cannot_resume_cancelled_session");
            fixture.ReleaseTasker();
            Require(replacement.Disposed && replacement.DisposeCalls == 1, "renderer_wait_replacement_is_reclaimable");
            Console.WriteLine("PCR preview connection scenario passed: renderer_wait; cancellation works without a renderer semaphore permit");
        }
        finally { if (held != 0) semaphore.Release(held); }
    }

    private static void FactoryLateReturn(MaaProcessorManager manager)
    {
        using var fixture = new Fixture(manager);
        using var hold = new ManualResetEventSlim(false);
        using var entered = new ManualResetEventSlim(false);
        var native = new PreviewController("none");
        fixture.Factory(native, hold, entered);
        var request = fixture.Connect();
        try
        {
            Until(() => entered.IsSet, "factory_late_enters_factory");
            StopTwice(fixture.Vm, "factory_late");
            Until(() => request.IsCompleted, "factory_late_cancel_is_bounded", 2000);
            CheckQuarantine(fixture, "factory_late");
            Require(native.ConnectCalls == 0 && native.DisposeCalls == 0, "factory_late_does_not_use_unreturned_controller");
        }
        finally { hold.Set(); }
        Until(() => !fixture.Pending, "factory_late_result_is_cleaned");
        Require(native.ConnectCalls == 0 && native.Disposed && native.DisposeCalls == 1
            && fixture.Processor.MaaTasker == null && !fixture.Vm.IsConnected,
            "factory_late_cancellation_is_checked_before_native_connect");
        Console.WriteLine("PCR preview connection scenario passed: factory_late; cancelled factory result never starts native connection");
    }

    private static void CheckQuarantine(Fixture fixture, string name)
    {
        Require(!fixture.Vm.PcrTargetBusy && !fixture.Vm.IsConnected && fixture.Pending
            && !fixture.Vm.CanEditPcrTarget && GateFlag("ConnectionPending") && GateFlag("Busy"),
            name + "_ui_finishes_but_global_native_isolation_remains");
        var creations = fixture.FactoryCalls;
        var duplicate = fixture.Connect();
        Require(duplicate.IsCompleted && fixture.FactoryCalls == creations,
            name + "_reconnect_does_not_allocate_while_native_pending");
    }

    private static void CheckOwnerRejected(Fixture fixture, string name)
    {
        var counters = (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants, PcrAlphaOwner.SessionCreations);
        var select = typeof(PcrAlphaOwner).GetProperty(nameof(PcrAlphaOwner.SelectConfiguration))!;
        var previous = select.GetValue(null);
        var selections = 0;
        try
        {
            PcrAlphaOwner.SelectConfiguration = () => { selections++; return Task.FromResult<string?>(null); };
            var alpha = PcrAlphaOwner.RequestStartAsync(fixture.Vm);
            var labyrinth = (Task)VmType.Assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthOwner", true)!
                .GetMethod("RequestStartAsync", Hidden)!.Invoke(null, [fixture.Vm])!;
            Require(alpha.IsCompleted && labyrinth.IsCompleted && selections == 0
                && !(bool)typeof(PcrAlphaOwner).GetProperty("HasActiveRun", Hidden)!.GetValue(null)! && !GateFlag("UpdatePending")
                && (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants, PcrAlphaOwner.SessionCreations) == counters,
                name + "_both_direct_owner_starts_rejected_without_authority");
            alpha.GetAwaiter().GetResult();
            labyrinth.GetAwaiter().GetResult();
        }
        finally { select.SetValue(null, previous); }
    }

    private static void StopTwice(TaskQueueViewModel vm, string name)
    {
        var watch = Stopwatch.StartNew();
        vm.StopTask();
        vm.StopTask();
        Require(watch.Elapsed < TimeSpan.FromMilliseconds(500), name + "_repeated_stop_returns_promptly");
    }

    private static bool GateFlag(string name) => (bool)(Gate.GetProperty(name, Hidden)?.GetValue(null)
        ?? Gate.GetField(name, Hidden)!.GetValue(null))!;
    private static void Set(object target, string name, object? value) => target.GetType().GetField(name, Hidden)!.SetValue(target, value);
    private static void New(object target, string name)
    {
        var field = target.GetType().GetField(name, Hidden)!;
        field.SetValue(target, Activator.CreateInstance(field.FieldType, nonPublic: true));
    }
    private static void Require(bool value, string name)
    {
        if (!value) throw new InvalidOperationException(name);
        checks++;
    }
    private static void Until(Func<bool> condition, string name, int milliseconds = 5000)
    {
        var watch = Stopwatch.StartNew();
        while (!condition() && watch.ElapsedMilliseconds < milliseconds)
        {
            Dispatcher.UIThread.RunJobs();
            Thread.Sleep(2);
        }
        Require(condition(), name);
        Dispatcher.UIThread.RunJobs();
    }
    private static void PumpFor(int milliseconds)
    {
        var watch = Stopwatch.StartNew();
        while (watch.ElapsedMilliseconds < milliseconds) { Dispatcher.UIThread.RunJobs(); Thread.Sleep(2); }
    }

    private sealed class Fixture : IDisposable
    {
        private readonly MaaProcessorManager manager;
        private readonly Dictionary<string, object?> previous = new();
        private readonly List<PreviewController> fakes = new();
        private readonly CancellationTokenSource noJobs = new();
        internal readonly MaaProcessor Processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        internal readonly TaskQueueViewModel Vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(VmType);
        internal MaaController? LastController;
        internal int FactoryCalls;
        internal bool Pending => (bool)VmType.GetProperty("PcrConnectionPending", Hidden)!.GetValue(Vm)!;

        internal Fixture(MaaProcessorManager manager)
        {
            this.manager = manager;
            foreach (var name in new[] { "_instances", "_viewModels", "_instanceOrder", "<Current>k__BackingField" })
                previous[name] = manager.GetType().GetField(name, Hidden)!.GetValue(manager);
            Set(Processor, "<InstanceId>k__BackingField", "pcr-alpha-v1");
            Set(Vm, "_processorField", Processor);
            Set(Vm, "_currentDevice", new AdbDeviceInfo("synthetic preview controller", adbPath, "127.0.0.1:16384", AdbScreencapMethods.Encode, AdbInputMethods.None, "{}"));
            foreach (var name in new[] { "_agentContexts", "_screenshotTaskerInitLock", "_liveViewScreencapJobLock", "_screencapLogLock", "_detachedScreenshotTaskersLock", "_detachedScreenshotTaskers" }) New(Processor, name);
            New(Vm, "_pcrDiscovery");
            New(Vm, "_currentResources");
            var nodes = new Dictionary<string, JToken> { ["PCRAlphaEntry"] = new JObject { ["custom_action_param"] = new JObject { ["selection_mode"] = "pi_nodes_v1" } } };
            foreach (var name in new[] { "Schedule", "Event", "Abyss", "Deep", "Rewards", "VeryHard", "Hard", "Normal", "RewardsFinal" })
                nodes["PCRAlphaSelect" + name] = new JObject { ["attach"] = new JObject { ["selected"] = name == "Rewards" } };
            var item = (DragItemViewModel)RuntimeHelpers.GetUninitializedObject(typeof(DragItemViewModel));
            Set(item, "_interfaceItem", new MaaInterface.MaaInterfaceTask { Name = "PCRAlpha", Entry = "PCRAlphaEntry", PipelineOverride = nodes,
                Option = [new MaaInterface.MaaInterfaceSelectOption { Name = "AlphaModules", SelectedCases = ["rewards"] }] });
            Set(Vm, "_taskItemViewModels", new ObservableCollection<DragItemViewModel> { item });
            Set(Vm, "_liveViewImageCache", new WriteableBitmap?[5]);
            noJobs.Cancel(); // There is no business job; Stop still executes its real cancel/revoke path.
            Set(Processor, "_commandThreadCts", noJobs);
            Set(manager, "_instances", new Dictionary<string, MaaProcessor> { [Processor.InstanceId] = Processor });
            Set(manager, "_viewModels", new Dictionary<string, TaskQueueViewModel> { [Processor.InstanceId] = Vm });
            Set(manager, "_instanceOrder", new List<string> { Processor.InstanceId });
            Set(manager, "<Current>k__BackingField", Processor);
            Timeout(TimeSpan.FromSeconds(5));
        }

        internal void Factory(PreviewController fake, ManualResetEventSlim? hold = null, ManualResetEventSlim? entered = null)
        {
            fakes.Add(fake);
            VmType.GetProperty("PcrPreviewControllerFactory", Hidden)!.SetValue(Vm,
                new Func<AdbDeviceInfo, MaaController>(_ => {
                    FactoryCalls++;
                    entered?.Set();
                    if (hold != null && !hold.Wait(TimeSpan.FromSeconds(15))) throw new TimeoutException("synthetic controller factory remained blocked");
                    return LastController = new MaaCustomController(fake, LinkOption.None, CheckStatusOption.None);
                }));
        }
        internal void Timeout(TimeSpan value) => VmType.GetProperty("PcrConnectTimeout", Hidden)!.SetValue(Vm, value);
        internal Task Connect() => (Task)VmType.GetMethod("ConnectPcrAdb", Hidden)!.Invoke(Vm, null)!;
        internal void ReleaseTasker()
        {
            var tasker = Processor.MaaTasker;
            Processor.MaaTasker = null;
            tasker?.Dispose();
            Vm.SetConnected(false);
        }
        public void Dispose()
        {
            foreach (var fake in fakes) { fake.Hold.Set(); fake.DisposeHold.Set(); fake.InactiveHold.Set(); }
            var watch = Stopwatch.StartNew();
            while (Pending && watch.ElapsedMilliseconds < 2000) { Dispatcher.UIThread.RunJobs(); Thread.Sleep(2); }
            ReleaseTasker();
            foreach (var value in previous) Set(manager, value.Key, value.Value);
            foreach (var fake in fakes)
                if (fake.Disposed) fake.CloseFixtureEvents();
            noJobs.Dispose();
        }
    }

    private sealed class PreviewController(string phase, bool blockDispose = false, bool throwDispose = false, bool blockInactive = false) : IMaaCustomController
    {
        private readonly IntPtr pixels = Marshal.AllocHGlobal(16 * 16 * 3);
        private readonly MaaImageBuffer image = new();
        internal readonly ManualResetEventSlim Hold = new(false), Entered = new(false), DisposeHold = new(!blockDispose), DisposeEntered = new(false);
        internal readonly ManualResetEventSlim InactiveHold = new(!blockInactive), InactiveEntered = new(false);
        internal volatile bool Returned, Disposed;
        internal int DisposeCalls, Inputs, ConnectCalls;
        private bool online;
        private int disposeStarted;
        public string Name { get; set; } = "offline-preview-cancellation";
        private void Block()
        {
            Entered.Set();
            if (!Hold.Wait(TimeSpan.FromSeconds(15))) throw new TimeoutException("synthetic native callback remained blocked");
            Returned = true;
        }
        public bool Connect() { Interlocked.Increment(ref ConnectCalls); if (phase == "connect") Block(); return online = true; }
        public bool Connected() => online;
        public bool RequestUuid(IMaaStringBuffer buffer) => buffer.TrySetValue("offline-preview-cancellation");
        public ControllerFeatures GetFeatures() => ControllerFeatures.None;
        public bool Screencap(IMaaImageBuffer buffer)
        {
            if (phase == "first_frame") Block();
            return image.TrySetRawData(pixels, 16, 16, 16) && image.TryCopyTo(buffer);
        }
        private bool Input() { Interlocked.Increment(ref Inputs); return false; }
        public bool StartApp(string intent) => Input(); public bool StopApp(string intent) => Input();
        public bool Click(int x, int y) => Input(); public bool Swipe(int x1, int y1, int x2, int y2, int duration) => Input();
        public bool TouchDown(int contact, int x, int y, int pressure) => Input(); public bool TouchMove(int contact, int x, int y, int pressure) => Input(); public bool TouchUp(int contact) => Input();
        public bool ClickKey(int keycode) => Input(); public bool InputText(string text) => Input(); public bool KeyDown(int keycode) => Input(); public bool KeyUp(int keycode) => Input();
        public bool Scroll(int dx, int dy) => Input(); public bool RelativeMove(int dx, int dy) => Input(); public bool Shell(string command, long timeout, IMaaStringBuffer output) => Input();
        public bool Inactive()
        {
            InactiveEntered.Set();
            if (!InactiveHold.Wait(TimeSpan.FromSeconds(15))) throw new TimeoutException("synthetic inactive callback remained blocked");
            return true;
        }
        public bool GetInfo(IMaaStringBuffer buffer) => buffer.TrySetValue("{}");
        public void Dispose()
        {
            if (Interlocked.Exchange(ref disposeStarted, 1) != 0) return;
            Interlocked.Increment(ref DisposeCalls);
            DisposeEntered.Set();
            if (!DisposeHold.Wait(TimeSpan.FromSeconds(15))) throw new TimeoutException("synthetic dispose remained blocked");
            image.Dispose();
            Marshal.FreeHGlobal(pixels);
            Disposed = true;
            if (throwDispose) throw new InvalidOperationException("synthetic controller disposal failed");
        }
        internal void CloseFixtureEvents() { Hold.Dispose(); Entered.Dispose(); DisposeHold.Dispose(); DisposeEntered.Dispose(); InactiveHold.Dispose(); InactiveEntered.Dispose(); }
    }
}
