using System;
using System.Threading;
using Avalonia.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.ViewModels.Pages;

public partial class TaskQueueViewModel
{
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(Idle), nameof(PcrRunStatus), nameof(CanEditPcrTarget), nameof(CanExportPlayerLog))]
    private bool pcrCleanupPending;
    private PcrPlayerRunLog? playerLog;
    internal long CurrentPcrFeedbackRun => Interlocked.Read(ref _pcrFeedbackRun);
    internal string PlayerLogFunction => IsLabyrinthProfile ? "labyrinth" : IsAgentProfile ? "clan" : "daily";
    internal PcrPlayerRunLog? PlayerLog
    {
        get
        {
            if (playerLog != null) return playerLog;
            if (CurrentPcrFeedbackRun != 0) return null;
            try { return string.IsNullOrWhiteSpace(PcrAlphaOwner.Repository) ? null : PcrPlayerRunLog.Latest(PcrAlphaOwner.Repository, PlayerLogFunction); }
            catch { return null; }
        }
    }
    public bool CanExportPlayerLog => !PcrTargetBusy && !PcrCleanupPending && (!IsRunning || PcrHasStartError) && PlayerLog != null;
    internal void BeginPlayerLog(string message = "已请求开始，正在校验当前功能和运行资源…")
    {
        try
        {
            if (playerLog is { IsFinished: false }) playerLog.Finish("stopped");
            playerLog = PcrPlayerRunLog.Begin(PcrAlphaOwner.Repository, PlayerLogFunction);
        }
        catch { playerLog = null; }
        var run = CurrentPcrFeedbackRun;
        Dispatcher.UIThread.Post(() => { if (run == CurrentPcrFeedbackRun) LabyrinthLogRows.Clear(); });
        AddPcrLiveLog(message);
        OnPropertyChanged(nameof(CanExportPlayerLog));
    }
    public void AddPcrLiveLog(string content)
    {
        if (string.IsNullOrWhiteSpace(content)) return;
        content = PcrPlayerRunLog.Sanitize(content);
        try { playerLog?.Append(content); } catch { /* Diagnostics never decide business success. */ }
        AddPlayerLogRow(content, CurrentPcrFeedbackRun);
    }
    private void AddPlayerLogRow(string content, long run)
    {
        Dispatcher.UIThread.Post(() =>
        {
            if (run != CurrentPcrFeedbackRun || _isDisposed != 0) return;
            LabyrinthLogRows.Add(new LabyrinthLogRow(DateTime.Now.ToString("HH:mm:ss"), content));
            while (LabyrinthLogRows.Count > LabyrinthMaxLogCount) LabyrinthLogRows.RemoveAt(0);
        });
    }
    internal void ReceivePlayerLog(long run, JObject message)
    {
        if (run != CurrentPcrFeedbackRun || _isDisposed != 0 || playerLog == null) return;
        try
        {
            var text = playerLog.Receive(message);
            if (!string.IsNullOrWhiteSpace(text)) AddPlayerLogRow(text, run);
        }
        catch { /* Keep the execution protocol independent of logging. */ }
    }
    internal void FinishPlayerLog(string outcome)
    {
        AddPcrLiveLog(outcome switch { "complete" => "本次任务已结束，执行资源已释放。",
            "cleanup_unconfirmed" => "执行资源退出尚未确认，原记录已保留。",
            "not_started" => "本次没有进入任务执行，请查看校验或启动原因。",
            _ => "本次任务已停止，执行资源已释放。" });
        try { playerLog?.Finish(outcome); } catch { }
    }
    partial void OnPcrStartErrorChanged(string value)
    {
        if (!string.IsNullOrWhiteSpace(value)) AddPcrLiveLog(value);
    }
}
