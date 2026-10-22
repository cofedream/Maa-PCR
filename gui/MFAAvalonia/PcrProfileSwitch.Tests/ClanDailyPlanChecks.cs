using System;
using System.IO;
using System.Linq;
using System.Text;
using Avalonia;
using Avalonia.Controls;
using Avalonia.LogicalTree;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Pcr;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal static class ClanDailyPlanChecks
{
    public static void Run(string sourceRoot, string fixture)
    {
        var root = Path.GetFullPath(Path.Combine(fixture, "clan-daily-plan")); Directory.CreateDirectory(root);
        var config = Path.Combine(root, "config"); Directory.CreateDirectory(config); var now = DateTimeOffset.Now; var count = 0;
        void Require(bool value, string name) { if (!value) throw new Exception("clan_daily_" + name); count++; }
        void Throws(Action action, string name) { try { action(); } catch { count++; return; } throw new Exception("clan_daily_expected_rejection_" + name); }
        var catalog = new PcrGuideCatalog { Server = "cn-bilibili", ContentVersion = "202610141431", Source = "synthetic offline fixture", Sha256 = new string('a', 64),
            Units = Enumerable.Range(1, 20).Select(i => new PcrGuideUnit(100001 + i * 100, "合成角色" + i)).ToList() };
        var legacy = Path.Combine(config, "guide-review.json"); var store = new PcrGuideStore(legacy); store.Load(); store.Save(new() { Input = new() { Catalog = catalog, Title = "保留原模块" } });
        var legacyBytes = File.ReadAllBytes(legacy);
        var snapshot = new JObject { ["schema_version"] = 1, ["server"] = "cn-bilibili", ["account_id"] = "123456", ["content_version"] = catalog.ContentVersion,
            ["observed_at"] = now.AddMinutes(-5).ToString("O"), ["ownership_complete"] = true, ["unknown_fields"] = new JArray(),
            ["units"] = new JArray(catalog.Units.Select(u => new JObject { ["unit_id"] = u.UnitId, ["name"] = u.Name, ["battle_rarity"] = 5 })) };
        var cache = new PcrGuideAccountStore(config); cache.Save(Encoding.UTF8.GetBytes(snapshot.ToString()), catalog, "123456", now, null);
        var snapshotPath = PcrClanGuide.SnapshotPaths(config).Single(); var snapshotBytes = File.ReadAllBytes(snapshotPath);
        var imagePath = Path.Combine(root, "source.png");
        using (var bitmap = new RenderTargetBitmap(new PixelSize(8, 8), new Vector(96, 96))) bitmap.Save(imagePath);
        var imageBytes = File.ReadAllBytes(imagePath);
        JObject Job(string id, int offset, int boss) { var ids = catalog.Units.Skip(offset).Take(5).Select(u => u.UnitId).ToArray(); return new()
        {
            ["period"] = "2026-09", ["phase"] = "D", ["round_min"] = 23, ["round_max"] = JValue.CreateNull(), ["boss"] = boss, ["boss_name"] = "合成目标",
            ["package"] = "离线套餐", ["team_label"] = "离线队伍 " + id, ["unit_ids"] = new JArray(ids), ["stars"] = new JArray(ids.Select(_ => 5)), ["auto"] = true,
            ["set_by_unit"] = new JObject(ids.Select(uid => new JProperty(uid.ToString(), true))), ["switches"] = new JArray(), ["support_mode"] = "none", ["support_unit_id"] = JValue.CreateNull(), ["reference_damage"] = 3200000000L,
            ["source"] = new JObject { ["url"] = "https://www.bilibili.com/video/BV1Rbh86XEsq/?p=2", ["bvid"] = "BV1Rbh86XEsq", ["page"] = 2, ["cid"] = 42155640154L,
                ["at_seconds"] = 35, ["image_path"] = imagePath, ["image_sha256"] = PcrClanGuide.Hash(imageBytes), ["job_id"] = id }
        }; }
        var bundlePath = Path.Combine(root, "bundle.json");
        var bundle = new JObject { ["version"] = 1, ["source"] = new JObject { ["url"] = "https://www.bilibili.com/video/BV1Rbh86XEsq/", ["period"] = "2026-09" },
            ["pages"] = new JArray(new JObject { ["page"] = 2, ["cid"] = 42155640154L, ["label"] = "D" }), ["jobs"] = new JArray(Job("one", 0, 1), Job("two", 5, 2), Job("three", 10, 3), Job("other-account", 15, 4)) };
        File.WriteAllText(bundlePath, bundle.ToString(), new UTF8Encoding(false)); var bundleBytes = File.ReadAllBytes(bundlePath);
        var guidePath = Path.Combine(config, "agent-guide-review.json"); var statePath = PcrClanDailyPlan.PathFor(config);
        var current = new PcrClanGuideDocument { SnapshotPath = snapshotPath, SnapshotSha256 = PcrClanGuide.Hash(snapshotBytes) };
        string? guideStamp = null;
        foreach (var id in new[] { "one", "two", "three", "other-account" })
        { PcrClanGuide.Select(current, bundlePath, id); guideStamp = PcrClanGuide.Save(current, guidePath, guideStamp, config, root, now, true); }
        var guideBytes = File.ReadAllBytes(guidePath);
        var daily = PcrClanDailyPlan.Read(statePath, out var missingStamp);
        Require(missingStamp == null && daily.Slots.Count == 0, "absent_is_empty_draft");
        Throws(() => PcrClanDailyPlan.Review(daily, guidePath, config, root, now, true), "complete_requires_three");
        PcrClanGuide.Select(current, bundlePath, "one");
        var unconfirmed = JsonConvert.DeserializeObject<PcrClanGuideDocument>(JsonConvert.SerializeObject(current))!; unconfirmed.ConfirmedFor = null;
        Throws(() => PcrClanDailyPlan.Assign(daily, 1, unconfirmed, guidePath, config, root, now), "unconfirmed_not_a_slot");
        Throws(() => PcrClanDailyPlan.Assign(daily, 0, current, guidePath, config, root, now), "slot_zero");
        Throws(() => PcrClanDailyPlan.Assign(daily, 4, current, guidePath, config, root, now), "slot_four");
        PcrClanDailyPlan.Assign(daily, 1, current, guidePath, config, root, now);
        var savedHash = PcrClanDailyPlan.Save(daily, statePath, null);
        Require(File.Exists(statePath) && PcrClanDailyPlan.Read(statePath, out var loadedStamp).Slots.Count == 1 && loadedStamp == savedHash, "partial_persists_with_read_stamp");
        var raw = File.ReadAllText(statePath, Encoding.UTF8);
        Require(!raw.Contains("account_id") && !raw.Contains("unit_ids") && !raw.Contains("Snapshot") && !raw.Contains("damage") && !raw.Contains("attempt"), "references_only_no_account_or_execution_state");
        Require(daily.Slots.Single().MaterialSha256.Length == 64 && daily.Slots.Single().ConfirmedFor == current.ConfirmedFor, "material_and_confirmation_bound");
        Throws(() => PcrClanDailyPlan.Review(daily, guidePath, config, root, now, true), "one_not_complete");
        foreach (var (id, slot) in new[] { ("two", 2), ("three", 3) })
        { PcrClanGuide.Select(current, bundlePath, id); PcrClanDailyPlan.Assign(daily, slot, current, guidePath, config, root, now); }
        savedHash = PcrClanDailyPlan.Save(daily, statePath, savedHash);
        var review = PcrClanDailyPlan.Review(daily, guidePath, config, root, now, true);
        Require(review.Complete && review.Warnings.Count == 0 && review.Slots.All(s => s.Contains("2026-09") && s.Contains("BOSS")), "three_same_account_period_complete");
        ClanPlanInputChecks.Run(root);
        var request = PcrClanDailyExecution.BuildPlan(statePath, guidePath, config, root, now);
        foreach (var slot in daily.Slots)
        {
            var selected = PcrClanDailyPlan.Load(daily, slot.Slot, guidePath, config, root, now, out _);
            Require(JToken.DeepEquals(request["slot_plans"]![slot.Slot.ToString()], PcrClanGuide.BuildPlan(selected, config, root, now)), "shared_plan_matches_individual_" + slot.Slot);
            Require(JToken.DeepEquals(request["slot_materials"]![slot.Slot.ToString()], selected.Job), "shared_plan_preserves_material_" + slot.Slot);
        }
        Require(File.ReadAllBytes(guidePath).SequenceEqual(guideBytes), "assignment_never_updates_guide_state");
        var first = PcrClanDailyPlan.Read(statePath, out var staleHash); var second = PcrClanDailyPlan.Read(statePath, out _);
        PcrClanDailyPlan.Clear(second, 2); savedHash = PcrClanDailyPlan.Save(second, statePath, staleHash); var kept = File.ReadAllBytes(statePath);
        Throws(() => PcrClanDailyPlan.Save(first, statePath, staleHash), "concurrent_save_rejected");
        Require(File.ReadAllBytes(statePath).SequenceEqual(kept), "concurrent_save_keeps_other_writer");
        savedHash = PcrClanDailyPlan.Save(daily, statePath, savedHash);
        using (var held = new FileStream(statePath + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
            Throws(() => PcrClanDailyPlan.Save(daily, statePath, savedHash), "active_save_lock_rejected");
        var pristine = File.ReadAllBytes(statePath);
        void Bad(string json, string name) { File.WriteAllText(statePath, json, new UTF8Encoding(false)); Throws(() => PcrClanDailyPlan.Read(statePath, out _), name); File.WriteAllBytes(statePath, pristine); }
        Bad("{\"version\":1,\"version\":1,\"policy\":\"" + PcrClanDailyPlan.Policy + "\",\"slots\":[]}", "duplicate_json_property");
        Bad("{\"version\":true,\"policy\":\"" + PcrClanDailyPlan.Policy + "\",\"slots\":[]}", "boolean_version");
        Bad("{\"version\":1,\"policy\":\"other\",\"slots\":[]}", "unknown_compensation_policy");
        Bad("{\"version\":1,\"policy\":\"" + PcrClanDailyPlan.Policy + "\",\"slots\":[],\"completed\":3}", "execution_state_forbidden");
        Bad("{\"version\":1,\"policy\":\"" + PcrClanDailyPlan.Policy + "\",\"slots\":[]}{}", "trailing_json");
        var serialized = JObject.Parse(Encoding.UTF8.GetString(pristine)); var bad = (JObject)serialized.DeepClone(); ((JArray)bad["slots"]!).Add(bad["slots"]![0]!.DeepClone()); Bad(bad.ToString(), "too_many_slots");
        bad = (JObject)serialized.DeepClone(); bad["slots"]![1]!["slot"] = 1; Bad(bad.ToString(), "duplicate_slot");
        bad = (JObject)serialized.DeepClone(); bad["slots"]![0]!["slot"] = "1"; Bad(bad.ToString(), "string_slot");
        bad = (JObject)serialized.DeepClone(); bad["slots"]![0]!["bundle_path"] = @"\\server\share\bundle.json"; Bad(bad.ToString(), "network_reference");
        bad = (JObject)serialized.DeepClone(); bad["slots"]![0]!["bundle_path"] = bundlePath + ":stream"; Bad(bad.ToString(), "ads_reference");
        bad = (JObject)serialized.DeepClone(); bad["slots"]![0]!["material_sha256"] = new string('x', 64); Bad(bad.ToString(), "invalid_material_hash");
        bad = (JObject)serialized.DeepClone(); bad["slots"]![0]!["unknown"] = 1; Bad(bad.ToString(), "unknown_slot_field");
        var modifiedGuide = JObject.Parse(Encoding.UTF8.GetString(guideBytes)); modifiedGuide["SavedJobs"]![daily.Slots[0].BundleSha256 + ":one"]!["Job"]!["team_label"] = "另一个保存版本";
        File.WriteAllText(guidePath, modifiedGuide.ToString());
        Require(!PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Complete, "changed_saved_material_invalidates_slot");
        Throws(() => PcrClanDailyPlan.Review(daily, guidePath, config, root, now, true), "changed_saved_material_blocks_complete"); File.WriteAllBytes(guidePath, guideBytes);
        File.WriteAllBytes(imagePath, imageBytes.Concat(new byte[] { 0 }).ToArray());
        Require(!PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Complete, "changed_original_image_invalidates_slot"); File.WriteAllBytes(imagePath, imageBytes);
        File.WriteAllText(bundlePath, bundle.ToString() + "\n");
        Require(!PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Complete, "changed_bundle_invalidates_slot"); File.WriteAllBytes(bundlePath, bundleBytes);
        Require(!PcrClanDailyPlan.Review(daily, guidePath, config, root, now.AddHours(25)).Complete, "expired_snapshot_invalidates_summary");
        PcrClanGuide.Select(current, bundlePath, "two"); var shared = catalog.Units[0].UnitId;
        current.Job["unit_ids"]![0] = shared; current.Job["set_by_unit"] = new JObject(((JArray)current.Job["unit_ids"]!).Values<int>().Select(id => new JProperty(id.ToString(), true)));
        guideStamp = PcrClanGuide.Save(current, guidePath, guideStamp, config, root, now, true);
        PcrClanDailyPlan.Assign(daily, 2, current, guidePath, config, root, now);
        Require(PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Warnings.Any(s => s.Contains(shared.ToString()) && s.Contains("第 1、2 刀")), "cross_main_own_role_conflict_reported");
        Require(!PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Complete, "own_role_conflict_blocks_complete_summary");
        Throws(() => PcrClanDailyPlan.Review(daily, guidePath, config, root, now, true), "own_role_conflict_rejected_as_complete");
        current.Job["support_mode"] = "specified"; current.Job["support_unit_id"] = shared;
        guideStamp = PcrClanGuide.Save(current, guidePath, guideStamp, config, root, now, true); PcrClanDailyPlan.Assign(daily, 2, current, guidePath, config, root, now);
        Require(PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Warnings.Count == 0, "explicit_support_excluded_from_own_conflicts");
        current.Job["support_mode"] = "qsjs"; current.Job["support_unit_id"] = JValue.CreateNull();
        guideStamp = PcrClanGuide.Save(current, guidePath, guideStamp, config, root, now, true); PcrClanDailyPlan.Assign(daily, 2, current, guidePath, config, root, now);
        Require(PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Warnings.Any(s => s.Contains(shared.ToString())), "qsjs_owned_member_still_own_conflict");
        var partial = (JObject)snapshot.DeepClone(); partial["ownership_complete"] = false; partial["units"] = new JArray(((JArray)snapshot["units"]!).Where(u => (int)u["unit_id"]! != shared));
        File.WriteAllText(snapshotPath, partial.ToString()); current.SnapshotSha256 = PcrClanGuide.Stamp(snapshotPath)!; current.ConfirmedFor = null;
        Throws(() => PcrClanGuide.BuildPlan(current, config, root, now), "qsjs_partial_missing_ownership_unknown"); File.WriteAllBytes(snapshotPath, snapshotBytes);
        current.SnapshotSha256 = PcrClanGuide.Hash(snapshotBytes); PcrClanGuide.Select(current, bundlePath, "three"); current.Job["period"] = "2026-10";
        guideStamp = PcrClanGuide.Save(current, guidePath, guideStamp, config, root, now, true); PcrClanDailyPlan.Assign(daily, 3, current, guidePath, config, root, now);
        Require(!PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Complete && PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Warnings.Any(s => s.Contains("期次必须一致")), "mixed_periods_not_complete");
        File.WriteAllBytes(guidePath, guideBytes); guideStamp = PcrClanGuide.Hash(guideBytes); current = PcrClanGuide.Read(guidePath);
        PcrClanGuide.Select(current, bundlePath, "three"); PcrClanDailyPlan.Assign(daily, 3, current, guidePath, config, root, now);
        var other = (JObject)snapshot.DeepClone(); other["account_id"] = "654321"; cache.Save(Encoding.UTF8.GetBytes(other.ToString()), catalog, "654321", now, null);
        var otherPath = PcrClanGuide.SnapshotPaths(config).Single(p => p != snapshotPath);
        PcrClanGuide.Select(current, bundlePath, "other-account"); current.SnapshotPath = otherPath; current.SnapshotSha256 = PcrClanGuide.Stamp(otherPath)!;
        guideStamp = PcrClanGuide.Save(current, guidePath, guideStamp, config, root, now, true); PcrClanDailyPlan.Assign(daily, 3, current, guidePath, config, root, now);
        Require(!PcrClanDailyPlan.Review(daily, guidePath, config, root, now).Complete, "mixed_accounts_not_complete");
        File.WriteAllBytes(guidePath, guideBytes); current = PcrClanGuide.Read(guidePath); PcrClanGuide.Select(current, bundlePath, "three");
        PcrClanDailyPlan.Assign(daily, 3, current, guidePath, config, root, now); PcrClanGuide.Select(current, bundlePath, "two"); PcrClanDailyPlan.Assign(daily, 2, current, guidePath, config, root, now);
        savedHash = PcrClanDailyPlan.Save(daily, statePath, PcrClanGuide.Stamp(statePath));
        var confirmed = true; var view = new PcrClanDailyPlanView(root, statePath, config, guidePath, () => current, () => confirmed);
        view.Reload();
        view.ReviewThree(); Require(view.StatusText.Contains("实际出刀数与补偿仍未知"), "ui_complete_never_claims_actual_attempts");
        Require(view.SlotTexts.All(s => s.Contains("BOSS")), "ui_reopens_three_targets");
        confirmed = false; Throws(() => view.AssignCurrent(1), "ui_current_unconfirmed_rejected"); confirmed = true;
        view.ClearSlot(2); Require(PcrClanDailyPlan.Read(statePath, out _).Slots.Count == 2 && view.SlotTexts[1].Contains("尚未设置"), "ui_clear_only_reference");
        view.AssignCurrent(2); Require(PcrClanDailyPlan.Read(statePath, out _).Slots.Count == 3, "ui_assign_saved_current");
        var external = PcrClanDailyPlan.Read(statePath, out var expected); PcrClanDailyPlan.Clear(external, 1); PcrClanDailyPlan.Save(external, statePath, expected); var externalBytes = File.ReadAllBytes(statePath);
        Throws(() => view.ClearSlot(3), "ui_stale_writer_rejected"); Require(File.ReadAllBytes(statePath).SequenceEqual(externalBytes), "ui_stale_writer_keeps_newer_state"); view.Reload(); view.AssignCurrent(1);
        Render(view, Path.Combine(root, "daily-plan-view.png"), 1120, 760);
        Require(File.ReadAllBytes(legacy).SequenceEqual(legacyBytes) && File.ReadAllBytes(snapshotPath).SequenceEqual(snapshotBytes) && File.ReadAllBytes(guidePath).SequenceEqual(guideBytes), "shared_originals_unchanged");
        var realPath = Path.Combine(sourceRoot, ".runtime", "clan-agent-20261020", "source", "september-auto2", "bundle.json"); var realRendered = false;
        if (File.Exists(realPath))
        {
            var realConfig = Path.Combine(root, "real-source-config"); var realGuide = new PcrClanGuideView(sourceRoot, Path.Combine(realConfig, "agent-guide-review.json"), realConfig);
            realGuide.LoadBundle(realPath); Require(!realGuide.HasCurrentConfirmation, "real_source_load_not_confirmation");
            var expander = realGuide.GetLogicalDescendants().OfType<Expander>().Single(e => (string?)e.Header == "一天三刀目标（仅离线预设）"); expander.IsExpanded = true;
            var realDaily = (PcrClanDailyPlanView)expander.Content!; Throws(() => realDaily.AssignCurrent(1), "real_source_unreviewed_not_preset");
            Render(realGuide, Path.Combine(root, "real-source-guide.png"), 1120, 900); Render(realDaily, Path.Combine(root, "real-source-daily-plan.png"), 1120, 760);
            Require(realDaily.StatusText.Contains("0/3") && !File.Exists(PcrClanDailyPlan.PathFor(realConfig)), "real_render_no_private_state_write"); realRendered = true;
        }
        File.WriteAllText(Path.Combine(root, "checks.json"), new JObject { ["checks"] = count, ["synthetic"] = true, ["real_source_rendered"] = realRendered, ["game_input"] = false, ["original_cache_unchanged"] = true }.ToString(), new UTF8Encoding(false));
        Console.WriteLine($"PCR clan daily plan checks passed: {count}; references only, no game input; real source rendered: {realRendered}");
    }
    private static void Render(Control control, string path, int width, int height)
    {
        var oldParent = control.Parent as ContentControl;
        if (oldParent != null) oldParent.Content = null;
        var surface = new Border { Background = Avalonia.Media.Brushes.White, Child = control };
        var host = new Window { Content = surface, Background = Avalonia.Media.Brushes.White, Width = width, Height = height, ShowInTaskbar = false, ShowActivated = false, Opacity = 0 };
        host.Show();
        try
        {
            surface.Measure(new Size(width, height)); surface.Arrange(new Rect(0, 0, width, height)); surface.UpdateLayout(); Dispatcher.UIThread.RunJobs();
            using var bitmap = new RenderTargetBitmap(new PixelSize(width, height), new Vector(96, 96)); bitmap.Render(surface); bitmap.Save(path);
        }
        finally { surface.Child = null; host.Content = null; host.Close(); if (oldParent != null) oldParent.Content = control; }
    }
}
