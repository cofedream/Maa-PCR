using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.LogicalTree;
using Avalonia.Threading;
using MFAAvalonia.Pcr;

internal static class ClanRecommendationViewChecks
{
    public static void Run(string repository, string guidePath, string config, string planPath, PcrClanGuideDocument current)
    {
        var candidatePath = Path.Combine(config, "recommendation-draft-test.json");
        var plan = PcrClanDailyPlan.Read(planPath, out _);
        plan.Slots.RemoveAll(slot => slot.Slot != 2);
        var locked = plan.Slots.Single().JobId;
        var initialHash = PcrClanDailyPlan.Save(plan, candidatePath, null);
        var guideHash = PcrClanGuide.Stamp(guidePath);
        var modePath = PcrClanDailyExecution.PathFor(config);
        var modeHash = PcrClanGuide.Stamp(modePath);
        var view = new PcrClanDailyPlanView(repository, candidatePath, config, guidePath, () => current, () => true);
        view.Reload();
        var controls = view.GetLogicalDescendants().OfType<Button>().ToArray();
        var recommend = controls.Single(button => Equals(button.Content, "帮我配三刀"));
        var save = controls.Single(button => Equals(button.Content, "保存推荐草案"));
        var lockSlot = view.GetLogicalDescendants().OfType<ComboBox>().Single();
        void Click(Button button)
        {
            if (!button.IsEnabled) throw new InvalidOperationException("按钮不可用：" + button.Content);
            button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            var clock = Stopwatch.StartNew();
            while (!view.PendingLoad.IsCompleted && clock.Elapsed < TimeSpan.FromSeconds(20))
            {
                Dispatcher.UIThread.RunJobs();
                Thread.Sleep(1);
            }
            if (!view.PendingLoad.IsCompleted) throw new TimeoutException("配队界面未完成：" + button.Content);
            view.PendingLoad.GetAwaiter().GetResult();
        }
        void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message + " " + view.StatusText);
        }
        lockSlot.SelectedIndex = 2;
        Click(recommend);
        Require(save.IsEnabled && PcrClanGuide.Stamp(candidatePath) == initialHash, "预览不应改写预设");
        lockSlot.SelectedIndex = 0;
        Require(!save.IsEnabled, "修改锁定位置后须重选候选");
        lockSlot.SelectedIndex = 2;
        Click(recommend);
        Click(save);
        var result = PcrClanDailyPlan.Read(candidatePath, out var stamp);
        Require(result.Slots.Count == 3 && result.Slots.Single(s => s.Slot == 2).JobId == locked, "保存的三刀应保留锁定队伍");
        Require(view.SlotTexts.All(s => s.Contains("BOSS")) && !save.IsEnabled, "已保存的队伍应显示在三个刀位中");
        Click(recommend);
        PcrClanDailyPlan.Clear(result, 3);
        var external = PcrClanDailyPlan.Save(result, candidatePath, stamp);
        Click(save);
        Require(PcrClanGuide.Stamp(candidatePath) == external && view.StatusText.Contains("其他窗口"), "过期预览不能覆盖其他窗口的新预设");
        Require(PcrClanGuide.Stamp(guidePath) == guideHash && PcrClanGuide.Stamp(modePath) == modeHash, "配队不得修改原作业或正式目标确认");
    }
}
