using System;
using System.IO;
using System.Linq;
using System.Text;
using Avalonia.Controls;
using Avalonia.LogicalTree;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class ClanRecommendationRosterChecks
{
    public static void Run(string repository, PcrGuideCatalog catalog, string bundlePath, JObject snapshot, DateTimeOffset now)
    {
        var config = Path.Combine(repository, "recommendation-roster");
        Directory.CreateDirectory(config);
        var catalogStore = new PcrGuideStore(Path.Combine(config, "guide-review.json"));
        catalogStore.Load();
        catalogStore.Save(new() { Input = new() { Catalog = catalog } });
        var guidePath = Path.Combine(config, "agent-guide-review.json");
        var planPath = PcrClanDailyPlan.PathFor(config);
        var accounts = new PcrGuideAccountStore(config);
        var guide = new PcrClanGuideDocument();
        void UseSnapshot(JObject data)
        {
            var account = data["account_id"]!.ToString();
            accounts.Save(Encoding.UTF8.GetBytes(data.ToString()), catalog, account, now, accounts.Stamp(catalog, account));
            guide.SnapshotPath = PcrClanGuide.SnapshotPaths(config).Single(path =>
                string.Equals(Path.GetFileName(Path.GetDirectoryName(path)), account, StringComparison.Ordinal));
            guide.SnapshotSha256 = PcrClanGuide.Stamp(guide.SnapshotPath)!;
        }
        void Confirm(string id)
        {
            PcrClanGuide.Select(guide, bundlePath, id);
            PcrClanGuide.Save(guide, guidePath, PcrClanGuide.Stamp(guidePath), config, repository, now, true);
        }
        var jobs = new[] { "one", "two", "three", "other-account" };
        UseSnapshot(snapshot);
        foreach (var id in jobs) Confirm(id);
        var originalPath = guide.SnapshotPath;
        var originalHash = guide.SnapshotSha256;
        var view = new PcrClanDailyPlanView(repository, planPath, config, guidePath, () => guide, () => true);
        view.Reload();
        var buttons = view.GetLogicalDescendants().OfType<Button>().ToArray();
        var preview = buttons.Single(b => Equals(b.Content, "帮我配三刀"));
        var save = buttons.Single(b => Equals(b.Content, "保存推荐草案"));
        var lockSlot = view.GetLogicalDescendants().OfType<ComboBox>().Single();
        void Click(Button button) => ClanRecommendationViewChecks.Click(view, button);
        void Check(bool passed, string message)
        {
            if (!passed) throw new InvalidOperationException(message + " " + view.StatusText);
        }
        void CheckStarReason(string message)
        {
            var reason = view.StatusText.Replace(" ", "");
            Check(!save.IsEnabled && reason.Contains(catalog.Units[0].Name)
                && reason.Contains("5星") && reason.Contains("4星"), message);
        }

        Click(preview);
        var other = (JObject)snapshot.DeepClone();
        other["account_id"] = "987654";
        UseSnapshot(other);
        Click(save);
        Check(!File.Exists(planPath), "切换账号后保存旧预览不能写入预设。");
        guide.SnapshotPath = originalPath;
        guide.SnapshotSha256 = originalHash;
        Click(preview);

        var refreshed = (JObject)snapshot.DeepClone();
        refreshed["observed_at"] = now.AddMinutes(-3).ToString("O");
        refreshed["units"]![0]!["battle_rarity"] = 4;
        UseSnapshot(refreshed);
        var archive = Path.Combine(Path.GetDirectoryName(originalPath)!, ".snapshot-history", originalHash.ToLowerInvariant() + ".json");
        Check(PcrClanGuide.Stamp(archive) == originalHash, "刷新后原快照应仍可读。");
        Click(save);
        Check(!File.Exists(planPath), "刷新账号后保存旧推荐不能使用历史快照。");
        foreach (var id in jobs) Confirm(id);
        Click(preview);
        var previewText = view.GetLogicalDescendants().OfType<TextBlock>()
            .Single(block => block.IsVisible && block.Text?.StartsWith("推荐草案（待保存）", StringComparison.Ordinal) == true)
            .Text!.Replace(" ", "");
        Check(save.IsEnabled && previewText.Contains(catalog.Units[0].Name) && previewText.Contains("5星") && previewText.Contains("4星"),
            "找到替代队时，待保存的预览仍须显示排除角色和两边星级。");
        Click(save);
        var result = PcrClanDailyPlan.Read(planPath, out var stamp);
        Check(result.Slots.Select(s => s.JobId).ToHashSet().SetEquals(jobs.Skip(1)), "应选另外三队，排除要求五星而实有四星的第一队。");

        PcrClanGuide.Select(guide, bundlePath, "one");
        PcrClanDailyPlan.Assign(result, 1, guide, guidePath, config, repository, now);
        var manualStamp = PcrClanDailyPlan.Save(result, planPath, stamp);
        view.Reload();
        lockSlot.SelectedIndex = 1;
        Click(preview);
        CheckStarReason("锁定星级不符的队伍应标出角色、所需五星和当前四星。");
        Check(PcrClanGuide.Stamp(planPath) == manualStamp, "推荐失败时应保留玩家手工安排。");
        lockSlot.SelectedIndex = 0;

        var unknown = (JObject)snapshot.DeepClone();
        unknown["observed_at"] = now.AddMinutes(-2).ToString("O");
        unknown["unknown_fields"] = new JArray($"units.{catalog.Units[0].UnitId}.battle_rarity");
        Check((int)unknown["units"]![0]!["battle_rarity"]! == 5, "未知字段样本应同时包含表观五星。");
        UseSnapshot(unknown);
        foreach (var id in jobs) Confirm(id);
        Click(preview);
        Click(save);
        Check(PcrClanDailyPlan.Read(planPath, out _).Slots.Select(s => s.JobId).ToHashSet().SetEquals(jobs.Skip(1)),
            "账号明确标记出战星级未知时，不得依据表观五星推荐该角色所在的队伍。");

        var missingTwo = (JObject)refreshed.DeepClone();
        missingTwo["observed_at"] = now.AddMinutes(-1).ToString("O");
        var roster = (JArray)missingTwo["units"]!;
        roster.RemoveAt(19);
        roster.RemoveAt(18);
        UseSnapshot(missingTwo);
        foreach (var id in jobs.Take(3)) Confirm(id);
        PcrClanGuide.Select(guide, bundlePath, "other-account");
        guide.Job["support_mode"] = "qsjs";
        try
        {
            PcrClanGuide.Save(guide, guidePath, PcrClanGuide.Stamp(guidePath), config, repository, now, true);
            throw new InvalidOperationException("缺两名自有角色时不应能确认该队。");
        }
        catch (InvalidDataException ex) when (ex.Message.Contains("缺少")) { }
        Click(preview);
        CheckStarReason("两队可以用时，应提示星级不符，缺两名的旧队伍也不能补位。");

        PcrClanGuide.Select(guide, bundlePath, "one");
        guide.Job["stars"]![0] = JValue.CreateNull();
        PcrClanGuide.Save(guide, guidePath, PcrClanGuide.Stamp(guidePath), config, repository, now, true);
        var accountStars = PcrClanGuide.BuildPlan(guide, config, repository, now);
        Check((int)accountStars["stars"]![0]! == 4, "星级留空应采用当前四星。");
        Click(preview);
        Click(save);
        Check(PcrClanDailyPlan.Read(planPath, out _).Slots.Select(s => s.JobId).ToHashSet().SetEquals(jobs.Take(3)),
            "重新核对后应能保存仅包含本号满足条件的三队。");
    }
}
