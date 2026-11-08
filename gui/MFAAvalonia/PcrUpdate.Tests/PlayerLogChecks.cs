using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.Primitives;
using Avalonia.LogicalTree;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using Avalonia.Interactivity;
using Avalonia.Platform.Storage;
using System.Collections.Generic;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using Newtonsoft.Json.Linq;

internal static class PlayerLogChecks
{
    private const BindingFlags Hidden = BindingFlags.Static | BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly Type Store = typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrPlayerRunLog", true)!;
    private static int count;
    private static void Check(bool value, string name) { if (!value) throw new InvalidOperationException("player_log:" + name); count++; }
    private static object? Call(object? target, string name, params object?[] args) =>
        (target?.GetType() ?? Store).GetMethod(name, Hidden)!.Invoke(target, args);
    private static object? Property(object target, string name) => target.GetType().GetProperty(name, Hidden)!.GetValue(target);
    private static void Set(object target, string name, object? value) => target.GetType().GetField(name, Hidden)!.SetValue(target,value);
    private static void New(object target, string name) => Set(target,name,Activator.CreateInstance(target.GetType().GetField(name,Hidden)!.FieldType));
    private static void Complete(Task task)
    {
        var until = DateTime.UtcNow.AddSeconds(20);
        while (!task.IsCompleted && DateTime.UtcNow < until) { Dispatcher.UIThread.RunJobs(); System.Threading.Thread.Sleep(2); }
        Check(task.IsCompleted, "bounded_completion"); task.GetAwaiter().GetResult(); Dispatcher.UIThread.RunJobs();
    }
    internal static void Run(string directory, Func<string, TaskQueueViewModel> owner)
    {
        var root = Path.Combine(directory, "player-logs"); Directory.CreateDirectory(root);
        CheckConnectionNativeLog(root);
        var log = Call(null,"Begin",root,"daily")!;
        var path = (string)Property(log,"DirectoryPath")!;
        Call(log,"Append","测试 password=SECRET token=HIDDEN UID:123456789",false);
        File.WriteAllText(Path.Combine(path,"credentials.json"),"SECRET_DO_NOT_EXPORT");
        var bytes = (byte[])Call(log,"Export",(object)Array.Empty<string>())!;
        using (var zip = new ZipArchive(new MemoryStream(bytes)))
        {
            Check(zip.GetEntry("credentials.json") == null, "explicit_file_allowlist");
            using var reader = new StreamReader(zip.GetEntry("events.log")!.Open());
            var text = reader.ReadToEnd();
            Check(!text.Contains("SECRET") && !text.Contains("HIDDEN") && !text.Contains("123456789"), "redacted_text");
        }
        Check(bytes.Length <= 16*1024*1024, "actual_zip_limit");
        var before = File.ReadAllText(Path.Combine(path,"events.log"));
        Call(log,"Receive",new JObject { ["run_id"]=new string('b',32),["function"]="daily",["event"]="text",["text"]="wrong run" });
        Check(File.ReadAllText(Path.Combine(path,"events.log")) == before,"wrong_run_ignored");
        var github = (string)Call(log,"GitHubLink","测试失败")!;
        var mail = (string)Call(log,"MailLink","测试失败")!;
        Check(github.StartsWith("https://github.com/shiwandehongbai/Maa-PCR/issues/new?"),"correct_repository");
        Check(mail.StartsWith("mailto:a877236828@gmail.com?"),"latest_feedback_email");
        Call(log,"Finish","failed");
        for (var i=0;i<4;i++)
        {
            var other = Call(null,"Begin",root,"daily")!;
            var folder=(string)Property(other,"DirectoryPath")!;
            File.WriteAllBytes(Path.Combine(folder,"native.log"),new byte[6*1024*1024]);
            Call(other,"Finish","complete");
        }
        var latest = Call(null,"Begin",root,"daily")!;
        Call(latest,"Append","new run",false);
        var area=Path.GetDirectoryName((string)Property(latest,"DirectoryPath")!)!;
        Check(Directory.GetFiles(area,"*",SearchOption.AllDirectories).Where(p=>Path.GetFileName(p)!="credentials.json")
            .Sum(p=>new FileInfo(p).Length)<=16*1024*1024,"feature_cache_limit");
        Check(File.Exists(Path.Combine(path,"credentials.json")),"unrelated_file_not_removed_by_rotation");
        Check(File.Exists(Path.Combine(path,"events.log")),"recent_error_kept_before_successful_runs");
        var clan = Call(null,"Begin",root,"clan")!;
        Check((string)Property(clan,"DirectoryPath")! != (string)Property(latest,"DirectoryPath")!,"separate_feature_directories");
        Check(new StartSettingsUserControlModel().ShowBatchStartup == false,"ineffective_batch_area_hidden");
        foreach (var id in new[]{"pcr-alpha-v1",PcrClanGuide.ProfileId,"pcr-labyrinth-v1"})
        {
            var vm=owner(id);
            var begin=typeof(TaskQueueViewModel).GetMethod("BeginPcrRunFeedback",Hidden)!;
            var old=(long)begin.Invoke(vm,null)!;
            for(var i=0;i<510;i++) vm.AddPcrLiveLog("步骤 "+i);
            Dispatcher.UIThread.RunJobs();
            Check(vm.LabyrinthLogRows.Count==500,"bounded_rows:"+id);
            var store=Property(vm,"PlayerLog")!;
            var logId=(string)Property(store,"RunId")!;
            begin.Invoke(vm,null); Dispatcher.UIThread.RunJobs();
            Call(vm,"ReceivePlayerLog",old,new JObject { ["run_id"]=logId,["function"]=(string)Property(store,"Function")!,["event"]="text",["text"]="late stale output" });
            Dispatcher.UIThread.RunJobs();
            Check(!vm.LabyrinthLogRows.Any(r=>r.Content.Contains("stale")),"old_queued_output_rejected:"+id);
            vm.AddPcrLiveLog("运行结束：合成示例，未操作游戏。"); Dispatcher.UIThread.RunJobs();
            var view = new PcrLiveLogView { DataContext=vm,Background=Brushes.White };
            var window = new Window { Content=view,Width=900,Height=260,ShowActivated=false,ShowInTaskbar=false,Position=new PixelPoint(-12000,-12000) };
            try
            {
                window.Show(); Dispatcher.UIThread.RunJobs();
                view.GetLogicalDescendants().OfType<ToggleButton>().First().IsChecked=true; Dispatcher.UIThread.RunJobs();
                using var image = new RenderTargetBitmap(new PixelSize(900,260)); image.Render(view);
                image.Save(Path.Combine(root,id+"-live-log.png"));
                Check(view.GetLogicalDescendants().OfType<SelectableTextBlock>().Any(t=>t.Text!.Contains("合成示例")),"shared_selectable_view:"+id);
                CheckFeedback(view,vm,root,id,false);
                CheckFeedback(view,vm,root,id,true);
                vm.LabyrinthLogRows.Clear(); Check(Directory.GetFiles((string)Property(Property(vm,"PlayerLog")!,"DirectoryPath")!).Length>0,"clear_only_panel:"+id);
            }
            finally { window.Close(); Dispatcher.UIThread.RunJobs(); }
        }
        CheckLifecycle(owner);
        CheckRunHandoff(owner);
        Console.WriteLine($"PCR player log checks passed: {count}; cache/ZIP <=16MiB, isolation, redaction, stale output, three rendered profiles, awaitable cleanup; game inputs=0; messages sent=0");
    }
    private static void CheckConnectionNativeLog(string root)
    {
        var nativeRoot = Path.Combine(root, "connection-native");
        Directory.CreateDirectory(Path.Combine(nativeRoot, "debug"));
        var time = DateTime.Now.AddSeconds(-1);
        string Line(DateTime when, int pid, string text) => $"[{when:yyyy-MM-dd HH:mm:ss.fff}][ERR][Px{pid}][fixture] {text}\n";
        var pid = Environment.ProcessId;
        File.WriteAllText(Path.Combine(nativeRoot, "debug", "maafw.log"),
            new string('x', 600 * 1024) + "\n" + Line(time.AddSeconds(-10), pid, "OLD_CONNECTION")
            + Line(time, pid + 1, "OTHER_PROCESS") + Line(time, pid, "CURRENT_FAILURE password=SECRET token=HIDDEN UID:123456789")
            + Line(time.AddMinutes(1), pid, "NEXT_CONNECTION"));
        File.WriteAllText(Path.Combine(nativeRoot, "debug", "maa.bak.2026.11.07-01.02.03.001.log"),
            Line(time, pid, "ROTATED_CONNECTION_LINE"));
        File.SetLastWriteTimeUtc(Path.Combine(nativeRoot, "debug", "maa.bak.2026.11.07-01.02.03.001.log"), DateTime.UtcNow.AddMinutes(-1));
        File.WriteAllText(Path.Combine(nativeRoot, "debug", "private.log"), Line(time, pid, "UNLISTED_LOG"));
        var log = Call(null, "Begin", nativeRoot, "daily")!;
        Call(log, "CaptureConnectionNativeLog", nativeRoot, time.AddSeconds(-1), time.AddSeconds(1));
        Call(log, "Finish", "failed");
        var bytes = (byte[])Call(log, "Export", (object)Array.Empty<string>())!;
        using (var zip = new ZipArchive(new MemoryStream(bytes)))
        {
            using var reader = new StreamReader(zip.GetEntry("native.log")!.Open());
            var text = reader.ReadToEnd();
            Check(text.Contains("CURRENT_FAILURE") && text.Contains("ROTATED_CONNECTION_LINE"), "connection_native_tail_and_rotation");
            Check(text.IndexOf("ROTATED_CONNECTION_LINE", StringComparison.Ordinal) < text.IndexOf("CURRENT_FAILURE", StringComparison.Ordinal),
                "connection_native_keeps_newest_tail_last");
            Check(!text.Contains("OLD_CONNECTION") && !text.Contains("NEXT_CONNECTION") && !text.Contains("OTHER_PROCESS")
                && !text.Contains("UNLISTED_LOG"), "connection_native_window_process_and_allowlist");
            Check(!text.Contains("SECRET") && !text.Contains("HIDDEN") && !text.Contains("123456789"), "connection_native_redaction");
            Check(zip.GetEntry("native.log")!.Length <= 512 * 1024 && bytes.Length <= 16 * 1024 * 1024, "connection_native_bounded_export");
        }
        var empty = Call(null, "Begin", nativeRoot, "clan")!;
        Call(empty, "CaptureConnectionNativeLog", Path.Combine(nativeRoot, "missing"), time, DateTime.Now);
        Check(File.ReadAllText(Path.Combine((string)Property(empty, "DirectoryPath")!, "events.log")).Contains("未取得本次连接的底层日志"),
            "connection_without_native_log_explains_missing_evidence");
        Call(empty, "Finish", "failed");
    }
    private static void CheckFeedback(PcrLiveLogView view,TaskQueueViewModel vm,string root,string id,bool fromError)
    {
        var actions=typeof(PcrAlphaOwner).Assembly.GetType("MFAAvalonia.Pcr.PcrFeedbackActions",true)!;
        var opens=new List<string>();var copies=new List<string>();Window? dialog=null;
        var previous=actions.GetFields(Hidden).ToDictionary(f=>f,f=>f.GetValue(null));
        var label=id+(fromError?"-error":"-export");
        var target=Path.Combine(root,label+"-feedback.zip");
        var file=DispatchProxy.Create<IStorageFile,PlayerLogStorageProxy>();
        ((PlayerLogStorageProxy)(object)file).Filename=target;
        try
        {
            actions.GetField("Open",Hidden)!.SetValue(null,(Action<string>)(value=>opens.Add(value)));
            actions.GetField("CopyText",Hidden)!.SetValue(null,(Func<string,Task>)(value=>{copies.Add(value);return Task.CompletedTask;}));
            actions.GetField("PickFile",Hidden)!.SetValue(null,(Func<IStorageProvider,FilePickerSaveOptions,Task<IStorageFile?>>)((_,_)=>Task.FromResult<IStorageFile?>(file)));
            actions.GetField("WindowOpened",Hidden)!.SetValue(null,(Action<Window>)(window=>dialog=window));
            var store=Property(vm,"PlayerLog")!;
            var imagePath=Path.Combine((string)Property(store,"DirectoryPath")!,"image-"+new string('f',32)+".png");
            using(var picture=new RenderTargetBitmap(new PixelSize(1280,720))) picture.Save(imagePath);
            vm.PcrStartError=fromError?"合成报错：此测试没有实际游戏、账号或反馈提交。":"";
            var entry=view.GetLogicalDescendants().OfType<Button>().First(b=>(string?)b.Content==(fromError?"反馈问题":"导出本次日志"));
            Check(entry.IsEffectivelyVisible && entry.IsEnabled,"toolbar_entry:"+label);
            entry.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));Dispatcher.UIThread.RunJobs();
            Check(dialog!=null,"feedback_window_opened:"+id);
            Check(dialog!.GetLogicalDescendants().OfType<SelectableTextBlock>().Any(t=>t.IsEffectivelyVisible && t.Text?.Contains("a877236828@gmail.com")==true),"visible_feedback_address:"+label);
            Check(opens.Count==0,"opening_does_not_share:"+label);
            void Click(string name)=>dialog!.GetLogicalDescendants().OfType<Button>().First(b=>(string?)b.Content==name)
                .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            void Until(Func<bool> ready)
            {
                var end=DateTime.UtcNow.AddSeconds(15);
                while(!ready()&&DateTime.UtcNow<end){Dispatcher.UIThread.RunJobs();System.Threading.Thread.Sleep(2);}
                Check(ready(),"feedback_action_completes:"+id);
            }
            Click("预览截图");Check(opens.Contains(imagePath),"preview_is_explicit_and_intercepted:"+id);
            Click("复制反馈邮箱");Check(copies.Contains("a877236828@gmail.com"),"copy_latest_email:"+id);
            Click("打开 GitHub 反馈页");Until(()=>opens.Any(s=>s.StartsWith("https://github.com/shiwandehongbai/Maa-PCR/issues/new?")));
            using(var zip=ZipFile.OpenRead(target))Check(zip.Entries.Any(e=>e.FullName.EndsWith(".png")),"selected_picture_exported:"+id);
            Check(new FileInfo(target).Length<=16*1024*1024,"feedback_zip_limit:"+id);
            foreach(var box in dialog!.GetLogicalDescendants().OfType<CheckBox>())box.IsChecked=false;
            Click("写反馈邮件");Until(()=>opens.Any(s=>s.StartsWith("mailto:a877236828@gmail.com")));
            using(var zip=ZipFile.OpenRead(target))Check(!zip.Entries.Any(e=>e.FullName.EndsWith(".png")),"unselected_picture_removed:"+id);
            Click("复制反馈说明");Check(copies.Any(s=>s.Contains("运行编号")),"copy_feedback_without_mail_client:"+id);
            Click("打开日志包位置");Check(opens.Contains(Path.GetDirectoryName(target)!),"open_saved_package_directory:"+id);
            Check(dialog.GetLogicalDescendants().OfType<TextBlock>().Any(t=>t.Text?.Contains("尚未发送")==true),"prepare_never_claims_sent:"+id);
            using var frame=new RenderTargetBitmap(new PixelSize(680,490));frame.Render(dialog);
            frame.Save(Path.Combine(root,label+"-feedback-window.png"));
        }
        finally
        {
            dialog?.Close();Dispatcher.UIThread.RunJobs();
            foreach(var pair in previous)pair.Key.SetValue(null,pair.Value);
        }
    }
    private static void CheckRunHandoff(Func<string,TaskQueueViewModel> owner)
    {
        var finalize=typeof(MaaProcessor).GetMethod("FinalizePcrRunAsync",Hidden)!;
        foreach(var id in new[]{"pcr-alpha-v1",PcrClanGuide.ProfileId})
        foreach(var status in new[]{MFATask.MFATaskStatus.SUCCEEDED,MFATask.MFATaskStatus.FAILED,MFATask.MFATaskStatus.STOPPED})
        foreach(var restartOnUnlock in new[]{true,false})
        {
            var vm=owner(id);var processor=vm.Processor;
            foreach(var field in new[]{"_agentContexts","_screenshotTaskerInitLock","_liveViewScreencapJobLock","_detachedScreenshotTaskersLock","_detachedScreenshotTaskers"}) New(processor,field);
            Call(vm,"BeginPcrRunFeedback");Dispatcher.UIThread.RunJobs();
            var oldLog=Property(vm,"PlayerLog")!;
            vm.IsRunning=true;vm.PcrCleanupPending=true;Set(processor,"_isTaskRunActive",1);
            object? newLog=null;bool? oldLogClosedAtUnlock=null;string? oldErrorAtUnlock=null;
            void Restart()
            {
                Call(vm,"BeginPcrRunFeedback");newLog=Property(vm,"PlayerLog");
                Set(processor,"_isTaskRunActive",1);vm.IsRunning=true;vm.PcrCleanupPending=false;
            }
            System.ComponentModel.PropertyChangedEventHandler unlocked=(_,e)=>
            {
                if(!restartOnUnlock||e.PropertyName!=nameof(vm.PcrCleanupPending)||vm.PcrCleanupPending||newLog!=null)return;
                oldLogClosedAtUnlock=(bool)Property(oldLog,"IsFinished")!;oldErrorAtUnlock=vm.PcrStartError;
                Restart();
            };
            vm.PropertyChanged+=unlocked;
            try
            {
                // Keep the UI queue paused until cleanup has posted its completion callbacks.
                Task.Run(async()=>await (Task)finalize.Invoke(processor,[status])!).WaitAsync(TimeSpan.FromSeconds(15)).GetAwaiter().GetResult();
                if(!restartOnUnlock)Restart();
                Dispatcher.UIThread.RunJobs();
                var label=id+status+restartOnUnlock;
                Check(newLog!=null,"restart_reached:"+label);
                Check(string.IsNullOrEmpty(vm.PcrStartError),"late_failure_does_not_replace_new_run_error:"+label);
                Check(vm.IsRunning&&processor.IsTaskRunActive,"late_idle_does_not_stop_new_run:"+label);
                Check(!(bool)Property(newLog!,"IsFinished")!,"late_finish_does_not_close_new_log:"+label);
                var newText=File.ReadAllText(Path.Combine((string)Property(newLog!,"DirectoryPath")!,"events.log"));
                Check(!newText.Contains("执行资源已释放")&&!newText.Contains("本次任务未完成"),"new_run_has_no_old_completion_text:"+label);
                var oldManifest=JObject.Parse(File.ReadAllText(Path.Combine((string)Property(oldLog,"DirectoryPath")!,"manifest.json")));
                Check((string?)oldManifest["outcome"]==(status==MFATask.MFATaskStatus.SUCCEEDED?"complete":status==MFATask.MFATaskStatus.FAILED?"failed":"stopped"),"old_log_keeps_its_terminal_outcome:"+label);
                if(restartOnUnlock)
                {
                    Check(oldLogClosedAtUnlock==true,"log_closed_before_unlock:"+label);
                    if(status==MFATask.MFATaskStatus.FAILED)Check(!string.IsNullOrEmpty(oldErrorAtUnlock),"failure_attached_before_unlock:"+label);
                }
            }
            finally
            {
                vm.PropertyChanged-=unlocked;Set(processor,"_isTaskRunActive",0);vm.IsRunning=false;vm.PcrCleanupPending=false;
                Call(vm,"FinishPlayerLog","stopped");Dispatcher.UIThread.RunJobs();
            }
        }
    }
    private static void CheckLifecycle(Func<string,TaskQueueViewModel> owner)
    {
        var type=typeof(PcrAlphaOwner);
        var current=type.GetField("current",Hidden)!;
        var sessionType=type.GetNestedType("Session",BindingFlags.NonPublic)!;
        var configType=type.GetNestedType("ValidatedConfig",BindingFlags.NonPublic)!;
        var gate=type.Assembly.GetType("MFAAvalonia.Pcr.PcrProfileRunGate",true)!;
        object Construct(Type t,params object?[] args)=>Activator.CreateInstance(t,BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance,null,args,null)!;
        foreach(var id in new[]{"pcr-alpha-v1",PcrClanGuide.ProfileId})
        foreach(var status in new[]{MFATask.MFATaskStatus.SUCCEEDED,MFATask.MFATaskStatus.FAILED,MFATask.MFATaskStatus.STOPPED})
        {
            var vm=owner(id); var processor=vm.Processor;
            foreach(var field in new[]{"_agentContexts","_screenshotTaskerInitLock","_liveViewScreencapJobLock","_detachedScreenshotTaskersLock","_detachedScreenshotTaskers"}) New(processor,field);
            Set(processor,"_isTaskRunActive",1);
            var session=Construct(sessionType,processor,JObject.Parse("{\"selected_modules\":[\"rewards\"]}"),Construct(configType,"{}"),new PcrStartConsent("","gui-current-state",[],false));
            current.SetValue(null,session);
            var completion=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            var finalize=typeof(MaaProcessor).GetMethod("FinalizePcrRunAsync",Hidden)!;
            var task=(Task)typeof(MaaProcessor).GetMethod("ExecuteStopCore",Hidden)!.Invoke(processor,new object[]{true,(Func<Task>)(async()=>{
                await completion.Task; await (Task)finalize.Invoke(processor,[status])!;
            })})!;
            Check(!task.IsCompleted && (bool)gate.GetProperty("Busy",Hidden)!.GetValue(null)!,"cleanup_wait_keeps_switch_locked:"+id+status);
            completion.SetResult(); Complete(task);
            Check(current.GetValue(null)==null && !processor.IsTaskRunActive && !vm.PcrCleanupPending,"terminal_run_releases_owner:"+id+status);
        }
        var stoppedVm=owner("pcr-alpha-v1"); var stoppedProcessor=stoppedVm.Processor;
        foreach(var field in new[]{"_agentContexts","_screenshotTaskerInitLock","_liveViewScreencapJobLock","_detachedScreenshotTaskersLock","_detachedScreenshotTaskers","_stopLock","_tempTasks"}) New(stoppedProcessor,field);
        Set(stoppedProcessor,"_isTaskRunActive",1); Set(stoppedProcessor,"_stopCompletionMessageHandled",1);
        var callbackCount=0;
        var stop=typeof(MaaProcessor).GetMethod("StopInternal",Hidden)!;
        Task RequestStop()=>(Task)stop.Invoke(stoppedProcessor,[MFATask.MFATaskStatus.STOPPED,true,true,false,(Action)(()=>callbackCount++)])!;
        Complete(RequestStop());Complete(RequestStop());
        Check(callbackCount==1 && !stoppedVm.PcrCleanupPending && !stoppedProcessor.IsTaskRunActive,"duplicate_stop_runs_cleanup_and_callback_once");
        var idleVm=owner("pcr-alpha-v1");
        New(idleVm.Processor,"_stopLock");
        Check(!(bool)typeof(MaaProcessor).GetMethod("ShouldProcessStop",Hidden)!.Invoke(idleVm.Processor,[false])!,"idle_stop_has_no_resources_to_release");
        Check(!idleVm.PcrCleanupPending && !idleVm.Processor.IsTaskRunActive,"idle_stop_does_not_enter_cleanup_state");
        var faultVm=owner("pcr-alpha-v1");var faultProcessor=faultVm.Processor;
        foreach(var field in new[]{"_agentContexts","_screenshotTaskerInitLock","_liveViewScreencapJobLock","_detachedScreenshotTaskersLock","_detachedScreenshotTaskers","_stopLock","_tempTasks","_commandQueue","_commandThreadCts"}) New(faultProcessor,field);
        Set(faultProcessor,"_isTaskRunActive",1);Set(faultProcessor,"_stopCompletionMessageHandled",1);
        var faultOwner=Construct(sessionType,faultProcessor,JObject.Parse("{\"selected_modules\":[\"rewards\"]}"),Construct(configType,"{}"),new PcrStartConsent("","gui-current-state",[],false));
        current.SetValue(null,faultOwner);
        var queue=(System.Collections.Concurrent.BlockingCollection<Func<Task>>)typeof(MaaProcessor).GetField("_commandQueue",Hidden)!.GetValue(faultProcessor)!;
        var cancel=(System.Threading.CancellationTokenSource)typeof(MaaProcessor).GetField("_commandThreadCts",Hidden)!.GetValue(faultProcessor)!;
        queue.Add(()=>throw new InvalidOperationException("synthetic initialization failure"));
        var loop=Task.Run(()=>{Set(faultProcessor,"_commandThread",System.Threading.Thread.CurrentThread);typeof(MaaProcessor).GetMethod("CommandLoop",Hidden)!.Invoke(faultProcessor,null);});
        try
        {
            var end=DateTime.UtcNow.AddSeconds(15);
            while(faultProcessor.IsTaskRunActive&&DateTime.UtcNow<end){Dispatcher.UIThread.RunJobs();System.Threading.Thread.Sleep(2);}
            Dispatcher.UIThread.RunJobs();
            Check(!faultProcessor.IsTaskRunActive&&current.GetValue(null)==null&&!faultVm.PcrCleanupPending,"initialization_exception_uses_awaited_cleanup_queue");
        }
        finally {cancel.Cancel();queue.CompleteAdding();Complete(loop);cancel.Dispose();queue.Dispose();}
        var failedVm=owner("pcr-alpha-v1"); var failedProcessor=failedVm.Processor;
        foreach(var field in new[]{"_agentContexts","_screenshotTaskerInitLock","_liveViewScreencapJobLock","_detachedScreenshotTaskersLock","_detachedScreenshotTaskers"}) New(failedProcessor,field);
        Set(failedProcessor,"_isTaskRunActive",1);
        var retained=Construct(sessionType,failedProcessor,JObject.Parse("{\"selected_modules\":[\"rewards\"]}"),Construct(configType,"{}"),new PcrStartConsent("","gui-current-state",[],false));
        sessionType.GetField("CleanupUnconfirmed")!.SetValue(retained,true); current.SetValue(null,retained);
        try
        {
            Complete((Task)typeof(MaaProcessor).GetMethod("FinalizePcrRunAsync",Hidden)!.Invoke(failedProcessor,[MFATask.MFATaskStatus.FAILED])!);
            throw new Exception("unconfirmed_cleanup_was_accepted");
        }
        catch(InvalidOperationException ex) when(ex.Message.Contains("shutdown")) { count++; }
        Check(ReferenceEquals(current.GetValue(null),retained) && failedProcessor.IsTaskRunActive
            && (bool)gate.GetProperty("Busy",Hidden)!.GetValue(null)!,"unconfirmed_cleanup_keeps_owner_and_switch_lock");
        sessionType.GetField("CleanupUnconfirmed")!.SetValue(retained,false);
        PcrAlphaOwner.Release(failedProcessor);
        typeof(MaaProcessor).GetMethod("EndTaskRun",Hidden)!.Invoke(failedProcessor,null); Dispatcher.UIThread.RunJobs();
    }
}

public class PlayerLogStorageProxy : DispatchProxy
{
    public string Filename { get; set; }="";
    protected override object? Invoke(MethodInfo? method,object?[]? args)=>method!.Name switch
    {
        "get_Path"=>new Uri(Filename),
        "get_Name"=>Path.GetFileName(Filename),
        "OpenWriteAsync"=>Task.FromResult<Stream>(new FileStream(Filename,FileMode.Create,FileAccess.Write,FileShare.None)),
        "Dispose"=>null,
        _=>throw new NotSupportedException(method.Name)
    };
}
