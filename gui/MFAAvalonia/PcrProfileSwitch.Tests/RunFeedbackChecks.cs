using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Reflection;
using System.Runtime.CompilerServices;
using Avalonia.Threading;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json.Linq;

internal static class RunFeedbackChecks
{
    internal static void Run()
    {
        const BindingFlags hidden = BindingFlags.Instance | BindingFlags.NonPublic;
        var type = typeof(TaskQueueViewModel);
        var begin = type.GetMethod("BeginPcrRunFeedback", hidden)!;
        var receive = type.GetMethod("ReceivePcrRunOutput", hidden)!;
        var showError = type.GetMethod("ShowPcrStartError", hidden)!;
        var count = 0;
        void Check(bool value, string name)
        {
            if (!value) throw new InvalidOperationException(name);
            count++;
        }
        TaskQueueViewModel ViewModel(string profile)
        {
            var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
            typeof(MaaProcessor).GetField("<InstanceId>k__BackingField", hidden)!.SetValue(processor, profile);
            var vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(type);
            type.GetField("_processorField", hidden)!.SetValue(vm, processor);
            return vm;
        }
        long Begin(TaskQueueViewModel vm) => (long)begin.Invoke(vm, null)!;
        void Output(TaskQueueViewModel vm, long run, string line) => receive.Invoke(vm, [run, line]);
        string Result(string summary) => "info: PCR Alpha " + new JObject {
            ["outcome"] = "unknown", ["action_success"] = false, ["summary"] = summary }.ToString();

        var vm = ViewModel("pcr-alpha-v1");
        var changed = new HashSet<string>();
        vm.PropertyChanged += (_, e) => changed.Add(e.PropertyName ?? "");
        var run = Begin(vm);
        Check(!vm.PcrHasRunSummary && !vm.PcrHasStartError, "feedback_starts_empty");
        const string partial = "已完成：礼物领取。\n本次执行已停止；未确认的结果不能视为成功。\n请保留现场并导出日志。";
        Output(vm, run, Result(partial));
        Dispatcher.UIThread.RunJobs();
        Check(vm.PcrRunSummary == partial && vm.PcrHasRunSummary,
            "feedback_displays_existing_multiline_summary_without_reinterpreting_outcome");
        Check(changed.Contains(nameof(vm.PcrHasRunSummary)), "feedback_visibility_notifies");
        foreach (var line in new[] { "info: 普通日志", "info: PCR Alpha {", "info: PCR Alpha {\"summary\":42}",
                     "info: PCR Alpha {\"summary\":\"\"}" }) Output(vm, run, line);
        Dispatcher.UIThread.RunJobs();
        Check(vm.PcrRunSummary == partial, "ordinary_or_invalid_output_preserves_result");

        Output(vm, run, Result("已排队的旧结果"));
        showError.Invoke(vm, [run, "原启动错误"]);
        var next = Begin(vm);
        Dispatcher.UIThread.RunJobs();
        Check(!vm.PcrHasRunSummary && !vm.PcrHasStartError, "new_start_clears_feedback_and_rejects_queued_old_result");
        Output(vm, run, Result("迟到的旧结果"));
        showError.Invoke(vm, [run, "迟到的旧错误"]);
        Dispatcher.UIThread.RunJobs();
        Check(!vm.PcrHasRunSummary && !vm.PcrHasStartError, "late_previous_run_cannot_replace_new_feedback");
        Output(vm, next, Result("本次任务已停止。"));
        Dispatcher.UIThread.RunJobs();
        Check(vm.PcrRunSummary == "本次任务已停止。", "current_run_result_is_accepted");

        var other = ViewModel("pcr-alpha-other");
        Begin(other);
        Check(!other.PcrHasRunSummary, "result_belongs_to_its_profile_view_model");
        Output(other, 1, Result("窗口关闭后的结果"));
        type.GetField("_isDisposed", hidden)!.SetValue(other, 1);
        Dispatcher.UIThread.RunJobs();
        Check(!other.PcrHasRunSummary, "disposed_view_drops_queued_feedback");

        var labyrinth = ViewModel("pcr-labyrinth-v1");
        var labyrinthRun = Begin(labyrinth);
        const string error = "本次未能开始：请填写游戏玩家 ID（个人简介，非B站 UID），必须为正整数。";
        showError.Invoke(labyrinth, [labyrinthRun, error]);
        Check(labyrinth.PcrHasStartError && labyrinth.PcrRunStatus == error,
            "labyrinth_start_error_is_visible_in_fixed_run_status");
        var settings = (PcrLabyrinthSettings)RuntimeHelpers.GetUninitializedObject(typeof(PcrLabyrinthSettings));
        var goals = new ObservableCollection<PcrLabyrinthGoal> {
            new(1, "美食殿堂", settings) { Enabled = true, Clears = "2", TargetMode = "通关次数" },
            new(2, "破晓之星", settings) { Enabled = true, Clears = "3", TargetMode = "尝试次数" },
            new(3, "咲恋救济院", settings) { Enabled = false, Clears = "9" } };
        typeof(PcrLabyrinthSettings).GetField("<Goals>k__BackingField", hidden)!.SetValue(settings, goals);
        type.GetField("_labyrinthSettings", hidden)!.SetValue(labyrinth, settings);
        settings.Progress = "本次任务已停止。";
        var statusFor = type.Assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthOwner")!
            .GetMethod("StatusFor", BindingFlags.Static | BindingFlags.NonPublic)!;
        foreach (var outcome in new[] { "login_failed", "runtime_components_missing", "controller_connection_failed" })
        {
            var reason = (string)statusFor.Invoke(null, [outcome])!;
            showError.Invoke(labyrinth, [labyrinthRun, reason]);
            Check(labyrinth.PcrRunStatus == reason && labyrinth.PcrRunStatus != settings.Progress,
                "labyrinth_terminal_reason_remains_visible_" + outcome);
        }
        const string timeout = "正在加载黎明界执行组件等待超时，本次已停止。";
        showError.Invoke(labyrinth, [labyrinthRun, timeout]);
        settings.Progress = "本次任务已停止。";
        Check(labyrinth.PcrRunStatus == timeout,
            "labyrinth_startup_timeout_survives_cleanup_progress");
        Check(labyrinth.PcrSelectionSummary == "本次目标：美食殿堂：通关 2 次，破晓之星：尝试 3 次",
            "labyrinth_target_summary_distinguishes_clears_and_attempts");
        Console.WriteLine($"PCR run feedback checks passed: {count}");
    }
}
