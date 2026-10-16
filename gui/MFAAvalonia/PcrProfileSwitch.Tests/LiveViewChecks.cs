using System;
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
using MFAAvalonia.ViewModels.Pages;

internal static class LiveViewChecks
{
    private const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.NonPublic;

    internal static void Run()
    {
        var count = 0;
        void Check(bool condition, string name) { if (!condition) throw new InvalidOperationException(name); count++; }
        void Set(object target, string name, object? value) => target.GetType().GetField(name, Hidden)!.SetValue(target, value);
        object? Get(object target, string name) => target.GetType().GetField(name, Hidden)!.GetValue(target);
        var previousFactory = PlatformControllerFactory.Create;
        using var fake = new PreviewController();
        using var controller = new MaaCustomController(fake, LinkOption.None, CheckStatusOption.None);
        using var resource = new MaaResource();
        using var tasker = new MaaTasker(controller, resource, DisposeOptions.None, false);
        var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        Set(processor, "<InstanceId>k__BackingField", "pcr-alpha-v1");
        Set(processor, "_liveViewScreencapJobLock", new System.Threading.Lock());
        Set(processor, "_screencapLogLock", new System.Threading.Lock());
        processor.MaaTasker = tasker;
        PlatformControllerFactory.Create = _ => controller;
        void Active(bool active) => Set(processor, "_isTaskRunActive", active ? 1 : 0);
        MaaProcessor.LiveViewCaptureState Poll() => processor.PostScreencapPipelined(out _);
        void WaitCapture() => Check(controller.LastJob.Wait() == MaaJobStatus.Succeeded, "fixture_capture_succeeded");
        try
        {
            Check(Poll() == MaaProcessor.LiveViewCaptureState.ControllerNotReady, "unconnected_controller_remains_not_ready");
            Check(controller.LinkStart().Wait() == MaaJobStatus.Succeeded, "fixture_connection_succeeded");
            Active(true);
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Cached, "preflight_uses_cache_without_native_task");
            using (var empty = processor.GetLiveViewBuffer(0L))
                Check(empty == null && fake.Captures == 0, "missing_cache_does_not_capture");

            Active(false);
            Task<MaaProcessor.LiveViewCaptureState> queuedPoll;
            using (var held = ((System.Threading.Lock)Get(processor, "_liveViewScreencapJobLock")!).EnterScope())
            using (var entered = new ManualResetEventSlim())
            {
                queuedPoll = Task.Run(() => { entered.Set(); return Poll(); });
                Check(entered.Wait(TimeSpan.FromSeconds(5)) && !queuedPoll.Wait(30), "preview_waits_for_job_lock");
                Active(true);
            }
            Check(queuedPoll.GetAwaiter().GetResult() == MaaProcessor.LiveViewCaptureState.Cached && fake.Captures == 0,
                "task_start_while_preview_waits_does_not_post_capture");

            Check(controller.Screencap().Wait() == MaaJobStatus.Succeeded, "business_seeds_cache");
            var captures = fake.Captures;
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Cached, "active_preview_uses_business_cache");
            using (var frame = processor.GetLiveViewBuffer(0L))
                Check(frame != null && !frame.IsEmpty && fake.Captures == captures, "cached_buffer_has_business_frame_without_capture");

            var vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
            Set(vm, "_processorField", processor);
            Set(vm, "_enableLiveView", true);
            Set(vm, "_isConnected", true);
            Set(vm, "_isLiveViewExpanded", true);
            Set(vm, "_lastLiveViewGeneration", 0L);
            Set(vm, "_lastLiveViewCaptureState", MaaProcessor.LiveViewCaptureState.Cached);
            Set(vm, "_liveViewImageCache", new WriteableBitmap?[5]);
            Set(vm, "_liveViewFpsWindowStart", DateTime.UtcNow);
            Set(processor, "_screencapFailedCount", 7);
            var tick = typeof(TaskQueueViewModel).GetMethod("OnLiveViewTimerElapsed", Hidden)!;
            void Tick() { tick.Invoke(vm, [null, null]); Dispatcher.UIThread.RunJobs(); }
            Tick();
            Check(vm.LiveViewImage != null && fake.Captures == captures, "timer_renders_cache_without_capture");
            Check((int)Get(processor, "_screencapFailedCount")! == 7, "cached_render_does_not_clear_real_failure_count");
            var rendered = (int)Get(vm, "_liveViewImageCount")!;
            Tick();
            Check((int)Get(vm, "_liveViewImageCount")! == rendered, "cached_render_is_limited_to_five_fps");
            Set(vm, "_lastCachedLiveViewFrameAt", Environment.TickCount64 - 201);
            Tick();
            Check((int)Get(vm, "_liveViewImageCount")! == rendered + 1, "cached_render_resumes_after_interval");
            Set(vm, "_isLiveViewExpanded", false);
            Set(vm, "_lastCachedLiveViewFrameAt", Environment.TickCount64 - 201);
            Tick();
            Check((int)Get(vm, "_liveViewImageCount")! == rendered + 1, "collapsed_preview_does_not_copy_cache");
            Set(vm, "_isLiveViewExpanded", true);
            Set(vm, "_enableLiveView", false);
            Tick();
            Check(fake.Captures == captures, "disabled_preview_does_not_capture");
            Set(vm, "_enableLiveView", true);
            typeof(TaskQueueViewModel).GetMethod("DisposeLiveViewBitmaps", Hidden)!.Invoke(vm, null);

            Check(processor.PostScreencap() == MaaJobStatus.Succeeded && fake.Captures == ++captures,
                "explicit_screenshot_remains_fresh_during_active_run");
            Active(false);
            fake.Hold.Reset();
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Pending, "idle_preview_posts_capture");
            Check(fake.Entered.Wait(TimeSpan.FromSeconds(5)), "preview_capture_entered");
            Active(true);
            Check(Poll() is MaaProcessor.LiveViewCaptureState.Pending or MaaProcessor.LiveViewCaptureState.Running,
                "active_transition_keeps_pending_job");
            fake.Hold.Set();
            WaitCapture();
            captures++;
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Succeeded && fake.Captures == captures,
                "active_transition_reports_real_success_without_reposting");
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Cached && fake.Captures == captures,
                "success_is_reported_once_then_cache");

            Active(false);
            fake.Fail = true;
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Pending, "failure_fixture_posts_real_capture");
            Check(controller.LastJob.Wait() == MaaJobStatus.Failed, "failure_fixture_reports_native_failure");
            Active(true);
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Failed, "active_transition_preserves_failed_job");
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Cached, "failed_job_is_not_reposted_while_active");
            fake.Fail = false;

            Set(processor, "_screenshotTaskerGeneration", 1L);
            using (var stale = processor.GetLiveViewBuffer(0L))
                Check(stale == null, "old_generation_cannot_publish_cache");
            processor.PostScreencapPipelined(out var generation);
            Check(generation == 1, "new_generation_is_reported");
            fake.Online = false;
            Check(Poll() == MaaProcessor.LiveViewCaptureState.ControllerNotReady, "disconnect_is_visible_during_cache_mode");
            using (var disconnected = processor.GetLiveViewBuffer(generation))
                Check(disconnected == null, "disconnected_cache_is_not_published");
            fake.Online = true;

            Set(processor, "<InstanceId>k__BackingField", "pcr-labyrinth-v1");
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Pending, "labyrinth_keeps_independent_preview_captures");
            WaitCapture();
            processor.ResetLiveViewScreencapJob();
            Set(processor, "<InstanceId>k__BackingField", "pcr-alpha-v1");
            Active(false);
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Pending, "end_or_failure_resumes_idle_capture");
            WaitCapture();
            processor.ResetLiveViewScreencapJob();

            Check(resource.OverridePipeline("""{"PreviewBusiness":{"recognition":"DirectHit","action":"DoNothing","pre_delay":0,"post_delay":0}}"""),
                "business_fixture_pipeline_loaded");
            fake.Entered.Reset();
            fake.Hold.Reset();
            var business = tasker.AppendTask("PreviewBusiness", "{}");
            Check(fake.Entered.Wait(TimeSpan.FromSeconds(5)) && tasker.IsRunning, "native_business_capture_is_active");
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Cached, "native_running_guard_covers_ui_flag_gap");
            var stop = tasker.Stop();
            Check(tasker.IsStopping, "native_stop_is_in_progress");
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Cached, "native_stopping_guard_covers_teardown");
            fake.Hold.Set();
            stop.Wait();
            business.Wait();
            Check(!tasker.IsRunning && !tasker.IsStopping, "native_stop_completes");
            Check(Poll() == MaaProcessor.LiveViewCaptureState.Pending, "stop_completion_resumes_idle_preview");
            WaitCapture();
            processor.ResetLiveViewScreencapJob();
            Check(fake.Inputs == 0, "preview_contract_executes_no_input");
            Check(fake.InactiveCalls == 1, "native_stop_invokes_one_separate_inactive_callback");
            Console.WriteLine($"PCR live view checks passed: {count}; real device inputs=0; synthetic stop inactive callbacks={fake.InactiveCalls}");
        }
        finally
        {
            fake.Hold.Set();
            tasker.Stop().Wait();
            PlatformControllerFactory.Create = previousFactory;
            processor.MaaTasker = null;
        }
    }

    private sealed class PreviewController : IMaaCustomController
    {
        private readonly IntPtr pixels = Marshal.AllocHGlobal(16 * 16 * 3);
        private readonly MaaImageBuffer image = new();
        internal readonly ManualResetEventSlim Hold = new(true), Entered = new(false);
        internal int Captures, Inputs, InactiveCalls;
        private int disposed;
        internal bool Online, Fail;
        public string Name { get; set; } = "offline-preview-contract";
        public bool Connect() => Online = true;
        public bool Connected() => Online;
        public bool RequestUuid(IMaaStringBuffer buffer) => buffer.TrySetValue("offline-preview-contract");
        public ControllerFeatures GetFeatures() => ControllerFeatures.None;
        public bool Screencap(IMaaImageBuffer buffer)
        {
            Interlocked.Increment(ref Captures);
            Entered.Set();
            if (!Hold.Wait(TimeSpan.FromSeconds(10)) || Fail) return false;
            for (var index = 0; index < 16 * 16 * 3; index++) Marshal.WriteByte(pixels, index, (byte)Captures);
            return image.TrySetRawData(pixels, 16, 16, 16) && image.TryCopyTo(buffer);
        }
        private bool Input() { Interlocked.Increment(ref Inputs); return false; }
        public bool StartApp(string intent) => Input();
        public bool StopApp(string intent) => Input();
        public bool Click(int x, int y) => Input();
        public bool Swipe(int x1, int y1, int x2, int y2, int duration) => Input();
        public bool TouchDown(int contact, int x, int y, int pressure) => Input();
        public bool TouchMove(int contact, int x, int y, int pressure) => Input();
        public bool TouchUp(int contact) => Input();
        public bool ClickKey(int keycode) => Input();
        public bool InputText(string text) => Input();
        public bool KeyDown(int keycode) => Input();
        public bool KeyUp(int keycode) => Input();
        public bool Scroll(int dx, int dy) => Input();
        public bool RelativeMove(int dx, int dy) => Input();
        public bool Shell(string command, long timeout, IMaaStringBuffer output) => Input();
        public bool Inactive() { Interlocked.Increment(ref InactiveCalls); return false; }
        public bool GetInfo(IMaaStringBuffer buffer) => buffer.TrySetValue("{}");
        public void Dispose()
        {
            if (Interlocked.Exchange(ref disposed, 1) != 0) return;
            Hold.Dispose(); Entered.Dispose(); image.Dispose(); Marshal.FreeHGlobal(pixels);
        }
    }
}
