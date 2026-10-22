using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace MFAAvalonia.Pcr;

// Arithmetic targets for one confirmed team; no inventory, costs or game actions are inferred.
public static class PcrGuideMinimum
{
    private static readonly HashSet<string> ScopeItems = new()
    {
        "来源核实", "来源时效", "区服", "关卡", "版本", "账号区服", "账号资料版本", "账号资料日期",
        "导入账号确认", "快照时效", "快照观察日期", "快照内容版本", "快照区服", "快照角色映射",
        "静态表内容版本", "静态表区服", "原图角色核对"
    };
    private static bool Number(string text, out int value) => int.TryParse(text, NumberStyles.None,
        CultureInfo.InvariantCulture, out value) && value >= 0 && value <= 100_000;
    private static bool Trainable(string field) => field is "等级" or "Rank" or "专武1等级" or "专武2等级"
        or "UB等级" or "技能1等级" or "技能2等级" or "EX技能等级";

    public static PcrGuideReport Check(PcrGuideInput input, PcrGuideReport review, DateTimeOffset now, string? expectedAccountId = null)
    {
        PcrGuideReview.Validate(input);
        var material = input.Material ?? "";
        var findings = new List<PcrGuideFinding>();
        void Add(string status, string item, string detail, string? source = null) =>
            findings.Add(new(status, item, detail, source ?? input.SourceUrl));
        PcrGuideReport Result(string summary) => new(PcrGuideReview.Fingerprint(input), now, summary, findings);
        var fingerprint = PcrGuideReview.Fingerprint(input);
        if (review.InputHash != fingerprint) return Result("材料已改变，请重新检查后再生成单队最小补齐建议。");
        if (review.CheckedAt.LocalDateTime.Date != now.LocalDateTime.Date || review.CheckedAt > now)
            review = PcrGuideReview.Check(input, now);
        var blocked = review.Findings.Where(f => ScopeItems.Contains(f.Item) &&
            (f.Status == "冲突" || f.Status == "未知" && f.Item != "版本")).ToList();
        findings.AddRange(blocked);
        if (input.Snapshot == null || !input.SnapshotConfirmed)
            Add("待核实", "账号资料", "请先确认本机账号快照及采集时间。");
        else
        {
            if (!string.IsNullOrEmpty(expectedAccountId) && input.Snapshot.AccountId != expectedAccountId)
                Add("待核实", "账号身份", "快照与当前配置账号不一致，停止计算补齐建议。");
            var age = now - PcrGuideData.Observation(input.Snapshot.ObservedAt);
            if (age < TimeSpan.Zero || age > TimeSpan.FromHours(24))
                Add("待核实", "账号时效", "账号资料已过期或时间异常，请在空闲时按需刷新。");
        }
        var team = input.Conditions.Where(c => c.Character != "账号").ToList();
        if (team.Any(c => !c.UnitId.HasValue || input.Catalog?.Units.Any(u => u.UnitId == c.UnitId && u.Name == c.Character) != true)
            || team.Select(c => c.UnitId).Distinct().Count() != 5)
            Add("待核实", "五人身份", "本轮只计算一队；须对应五个不同的角色编号和形态。");
        foreach (var role in team.GroupBy(c => c.UnitId))
        {
            var owned = role.Where(c => c.Field == "持有" && c.Comparison == "=" && c.Expected == "是").ToList();
            var facts = input.Facts.Where(f => f.UnitId == role.Key && f.Character == role.First().Character && f.Field == "持有").ToList();
            if (owned.Count != 1 || string.IsNullOrWhiteSpace(owned[0].Evidence) || !material.Contains(owned[0].Evidence, StringComparison.Ordinal)
                || facts.Count != 1 || !facts[0].Confirmed || facts[0].Value != "是" || string.IsNullOrWhiteSpace(facts[0].Source))
                Add("待核实", role.First().Character + " · 持有", "持有未确认或明确未持有。先核对队伍，不推导角色获取或培养方案。");
        }
        if (findings.Count != 0) return Result("先核对来源、目标与账号；本次不生成培养建议。");

        var unknown = new Dictionary<string, HashSet<string>>();
        void Unknown(PcrGuideCondition c, string reason)
        {
            var actor = c.Character + (c.UnitId.HasValue ? $"（{c.UnitId}）" : "");
            if (!unknown.TryGetValue(actor, out var fields)) unknown[actor] = fields = new();
            fields.Add(c.Field + "（" + reason + "）");
        }
        PcrGuideFact? Fact(PcrGuideCondition c)
        {
            var matches = input.Facts.Where(f => f.UnitId == c.UnitId && f.Character == c.Character && f.Field == c.Field
                && (c.UnitId.HasValue || f.Form == c.Form)).ToList();
            return matches.Count == 1 && matches[0].Confirmed && !string.IsNullOrWhiteSpace(matches[0].Source)
                && !string.IsNullOrWhiteSpace(matches[0].Value) ? matches[0] : null;
        }
        bool Evidence(PcrGuideCondition c) => !string.IsNullOrWhiteSpace(c.Evidence) && material.Contains(c.Evidence, StringComparison.Ordinal);
        var conditions = input.Conditions.Where(c => c.Field is not ("持有" or "SET" or "SET标记")).ToList();
        foreach (var c in conditions.Where(c => c.Comparison == "参考"))
        {
            var fact = Fact(c);
            if (string.IsNullOrWhiteSpace(c.Field) || !Evidence(c) || string.IsNullOrWhiteSpace(c.Expected)) { Unknown(c, "作者要求未知"); continue; }
            if (fact == null) { Unknown(c, "账号值待核实"); continue; }
            if (Trainable(c.Field) && (!Number(c.Expected.Trim(), out _) || !Number(fact.Value.Trim(), out _)))
            { Unknown(c, "数值无效"); continue; }
            if (c.Expected.Trim() != fact.Value.Trim()) Add("参考差异", c.Character + " · " + c.Field,
                $"作者展示 {c.Expected}；账号 {fact.Value}。参考差异不计入必须补齐。", input.SourceUrl + " · " + c.Evidence);
        }
        foreach (var group in conditions.Where(c => c.Comparison != "参考").GroupBy(c => (c.UnitId, c.Character, Form: c.UnitId.HasValue ? "" : c.Form, c.Field)))
        {
            var c = group.First(); var rows = group.ToList(); var fact = Fact(c);
            if (rows.Any(x => string.IsNullOrWhiteSpace(x.Field) || !Evidence(x) || string.IsNullOrWhiteSpace(x.Expected) || x.Comparison is not ("=" or ">=" or "<=")))
            { Unknown(c, "要求或文字依据待核实"); continue; }
            if (fact == null) { Unknown(c, "账号值待核实"); continue; }
            var item = c.Character + " · " + c.Field;
            var evidence = input.SourceUrl + " · " + string.Join("；", rows.Select(x => x.Evidence).Distinct());
            if (rows.All(x => Number(x.Expected.Trim(), out _)) && Number(fact.Value.Trim(), out var actual))
            {
                var lower = rows.Where(x => x.Comparison is "=" or ">=").Select(x => int.Parse(x.Expected, CultureInfo.InvariantCulture)).DefaultIfEmpty(0).Max();
                var upper = rows.Where(x => x.Comparison is "=" or "<=").Select(x => int.Parse(x.Expected, CultureInfo.InvariantCulture)).DefaultIfEmpty(100_000).Min();
                if (lower > upper) Add("条件冲突", item, "作者条件互相冲突，不能计算补齐目标。", evidence);
                else if (c.Field == "星级" && (actual < lower || actual > upper))
                    Add("配置核对", item, $"当前出战 {actual} 星；作者要求 {string.Join("、", rows.Select(x => x.Comparison + " " + x.Expected))}。先核对已解放星级与可切换范围，星级切换不计为培养。", evidence);
                else if (actual > upper) Add("条件冲突", item, $"当前 {actual} 已超过要求上限 {upper}。继续升级无法解决，需要重新核对作业。", evidence);
                else if (actual < lower)
                {
                    if (Trainable(c.Field)) Add("明确数值缺口", item, $"{actual} → {lower}（差 {lower - actual}）。这是满足已知要求的最小字段目标；解锁条件、材料与费用尚未核算。", evidence);
                    else Add("配置核对", item, $"要求至少 {lower}；当前 {actual}。先核对出战设置或解锁条件，不直接列为升级任务。", evidence);
                }
            }
            else if (Trainable(c.Field)) Unknown(c, "数值无效");
            else if (rows.Any(x => x.Comparison != "=") || rows.Select(x => x.Expected.Trim()).Distinct().Count() != 1)
                Add("条件冲突", item, "非数值条件不明确或互相冲突，先核对原文。", evidence);
            else if (c.Expected.Trim() != fact.Value.Trim()) Add("配置核对", item,
                $"要求 {c.Expected}；当前 {fact.Value}。" + (c.Field.StartsWith("EX装备", StringComparison.Ordinal)
                    ? "账号只采集已装备 EX；未读取仓库，不能判断可替换装备或补齐成本。"
                    : "先核对设置与解锁条件，不能仅凭该差异推导最省资源的操作。"), evidence);
        }
        foreach (var (actor, fields) in unknown) Add("未知", actor, string.Join("、", fields) + "。未知项不计入必须培养。");
        Add("范围", "账号与资源", $"仅使用 {input.Snapshot!.ObservedAt} 的已确认资料。未读取完整仓库、库存或费用表；未证明可免费同步、已有替换 EX 或培养成本。不会自动培养、换装、采购或开战。");
        var gaps = findings.Count(f => f.Status == "明确数值缺口");
        if (findings.Any(f => f.Status == "条件冲突"))
            return Result($"先解决作者条件冲突；已知数值缺口 {gaps} 项仅供核对，目前不能形成可用的补齐方案。");
        return Result(gaps == 0 ? "本队目前没有可据此要求培养的明确数值缺口；参考差异和未知仍需保留。这不等于零成本或保证通关。"
            : $"本队有 {gaps} 项明确数值缺口。只补到已知要求的字段目标；资源成本与通关结果尚未确认。");
    }
}
