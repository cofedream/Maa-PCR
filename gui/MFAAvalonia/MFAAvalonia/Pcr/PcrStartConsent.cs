using System;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public sealed record PcrStartConsent(string GameDay, string CountContext,
    string[] ScheduleAuthorizations, bool AttestCurrentPlan, bool ConfirmNewCycle = false)
{
    private static readonly string[] ScheduleItems = {"arena_coins", "princess_arena_coins", "dungeon_skip", "mana_skip", "exp_skip", "normal_gacha_free", "guild_like", "house_items", "memorial_rewards"};

    private static (bool Day, bool Count, bool Schedule) ConsentRequirements(JObject selection, JObject config)
    {
        var modules = selection["selected_modules"]!.Values<string>().ToArray();
        bool schedule = modules.Contains("schedule_onekey");
        bool sweep = modules.Contains("event") || modules.Contains("abyss") || config["presets"]!.Any(p =>
            (string?)p["kind"] != "normal" && modules.Contains((string?)p["kind"]) && (int?)p["target_per_stage"] > 0);
        return (sweep || schedule, sweep, schedule);
    }

    internal static PcrStartConsent Validate(JObject selection, JObject config, PcrStartConsent consent)
    {
        var needs = ConsentRequirements(selection, config);
        // GUI requests fresh counter/completion observations, never a human
        // assertion. Python derives the bookkeeping day with its game-day tool.
        bool currentState = consent.CountContext == "gui-current-state";
        if (currentState && ((int?)config["version"] is not (3 or 4) || consent.GameDay != ""))
            throw new InvalidOperationException("当前状态语义只适用于本机 GUI 配置，日期由内部工具生成");
        if (!currentState && (needs.Day && !DateOnly.TryParseExact(consent.GameDay, "yyyy-MM-dd", out _)
            || needs.Count && consent.CountContext != "user-reported-unreset"))
            throw new InvalidOperationException("本次游戏日及未重置次数声明无效");
        if (consent.ConfirmNewCycle && (!currentState || !needs.Schedule))
            throw new InvalidOperationException("日程恢复确认只适用于本次 GUI 日程启动。");
        var auth = consent.ScheduleAuthorizations;
        if (auth == null || (needs.Schedule
            ? (!currentState && !consent.AttestCurrentPlan) || auth.Length == 0 || auth.Distinct().Count() != auth.Length || auth.Any(n => !ScheduleItems.Contains(n))
            : consent.AttestCurrentPlan || auth.Length != 0))
            throw new InvalidOperationException(needs.Schedule ? "日程设置：请至少选择一个有效且不重复的本次项目；CLI 仍须当前计划声明。" : "未选择日程：不能附带日程许可或声明。");
        // An omitted reward-only date is derived and labelled by supported_daily.
        // Never turn an irrelevant saved/checked field into a user assertion.
        return consent with { GameDay = needs.Day ? consent.GameDay : "",
            CountContext = needs.Count || currentState && needs.Schedule ? consent.CountContext : "" };
    }

}
