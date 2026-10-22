using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.LogicalTree;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class ClanWorkflowChecks
{
    private static readonly string[] JobIds = { "BV1Rbh86XEsq-p2-t35-r1", "BV1Rbh86XEsq-p2-t35-r6", "BV1Rbh86XEsq-p2-t35-r12" };
    private sealed record Fixture(string Root, string Config, string Guide, string Bundle, string OriginalBundle, string Snapshot, IReadOnlyDictionary<string, string> OriginalHashes);
    private static string SyntheticBundle(string root)
    {
        var original = Path.Combine(root, "synthetic-original"); Directory.CreateDirectory(original);
        var imagePath = Path.Combine(original, "source.png");
        using (var bitmap = new RenderTargetBitmap(new PixelSize(8, 8), new Vector(96, 96))) bitmap.Save(imagePath);
        var image = File.ReadAllBytes(imagePath);
        var jobs = new JArray(Enumerable.Range(0, 21).Select(index =>
        {
            var team = index % 3;
            var ids = Enumerable.Range(team * 5 + 1, 5).Select(i => 100001 + i * 100).ToArray();
            return new JObject
            {
                ["period"] = "2026-09", ["phase"] = "D", ["round_min"] = 23, ["round_max"] = JValue.CreateNull(),
                ["boss"] = index % 5 + 1, ["boss_name"] = "合成 BOSS", ["package"] = "合成离线套餐", ["team_label"] = "合成队伍 " + (index + 1),
                ["unit_ids"] = new JArray(ids.Select(_ => JValue.CreateNull())), ["stars"] = new JArray(ids.Select(_ => JValue.CreateNull())), ["auto"] = true,
                ["set_by_unit"] = new JObject(), ["switches"] = new JArray(), ["support_mode"] = new[] { "none", "qsjs", "specified" }[team],
                ["support_unit_id"] = JValue.CreateNull(), ["reference_damage"] = 3200000000L, ["image_base64"] = Convert.ToBase64String(image),
                ["members"] = new JArray(ids.Select((id, position) => new JObject
                {
                    ["unit_id"] = JValue.CreateNull(), ["proposal_unit_id"] = id, ["proposal_name"] = "合成角色" + id,
                    ["star"] = JValue.CreateNull(), ["set"] = position % 2 == 0 ? new JValue(true) : JValue.CreateNull()
                })),
                ["source"] = new JObject { ["url"] = "https://www.bilibili.com/video/BV1Rbh86XEsq/?p=2", ["bvid"] = "BV1Rbh86XEsq", ["page"] = 2,
                    ["cid"] = 42155640154L, ["at_seconds"] = 35, ["image_path"] = imagePath, ["image_sha256"] = PcrClanGuide.Hash(image),
                    ["job_id"] = index < JobIds.Length ? JobIds[index] : "synthetic-unused-" + index }
            };
        }));
        var bundle = new JObject { ["version"] = 1,
            ["source"] = new JObject { ["url"] = "https://www.bilibili.com/video/BV1Rbh86XEsq/", ["period"] = "2026-09", ["title"] = "合成离线样例，无实际视频内容", ["author"] = "synthetic fixture" },
            ["pages"] = new JArray(new JObject { ["page"] = 2, ["cid"] = 42155640154L, ["label"] = "合成 D" }), ["jobs"] = jobs };
        var path = Path.Combine(original, "bundle.json"); File.WriteAllText(path, bundle.ToString(), new UTF8Encoding(false));
        return path;
    }
    private static Fixture Prepare(string directory, string? configOverride = null)
    {
        var root = Path.GetFullPath(Path.Combine(directory, "clan-workflow"));
        if (Directory.Exists(root)) throw new IOException("离线流程夹具目录已存在，请使用新的隔离目录。");
        var config = configOverride == null ? Path.Combine(root, "config") : Path.GetFullPath(configOverride);
        if (configOverride != null && Directory.Exists(config) && Directory.EnumerateFileSystemEntries(config).Any()) throw new IOException("新候选已有配置或私态，禁止生成合成种子。");
        Directory.CreateDirectory(config); Directory.CreateDirectory(root);
        var original = SyntheticBundle(root);
        var bundle = PcrClanGuide.Bundle(original); var jobs = ((JArray)bundle["jobs"]!).OfType<JObject>().ToArray();
        if (jobs.Length != 21) throw new InvalidDataException("合成作业夹具应包含 21 队。");
        var originals = jobs.Select(j => (string)j["source"]!["image_path"]!).Append(original).Distinct().ToDictionary(p => p, p => PcrClanGuide.Stamp(p)!);
        var rows = JobIds.Select(id => jobs.Single(j => (string?)j["source"]?["job_id"] == id)).ToArray();
        var units = rows.SelectMany(j => (JArray)j["members"]!).Select(m => new PcrGuideUnit((int)m["proposal_unit_id"]!, (string)m["proposal_name"]!)).ToList();
        if (units.Select(u => u.UnitId).Distinct().Count() != 15) throw new InvalidDataException("离线三队存在重复 proposal。");
        var catalog = new PcrGuideCatalog { Server = "cn-bilibili", ContentVersion = "000000000001", Source = "synthetic offline fixture; not a game static table", Sha256 = new string('a', 64), Units = units };
        var store = new PcrGuideStore(Path.Combine(config, "guide-review.json")); store.Load(); store.Save(new() { Input = new() { Catalog = catalog, Title = "离线流程夹具：合成静态表" } });
        var now = DateTimeOffset.Now;
        var snapshot = new JObject { ["schema_version"] = 1, ["server"] = catalog.Server, ["account_id"] = "999000001", ["content_version"] = catalog.ContentVersion,
            ["observed_at"] = now.AddMinutes(-5).ToString("O"), ["ownership_complete"] = true, ["unknown_fields"] = new JArray(),
            ["units"] = new JArray(units.Select(u => new JObject { ["unit_id"] = u.UnitId, ["name"] = u.Name, ["battle_rarity"] = 5 })) };
        new PcrGuideAccountStore(config).Save(Encoding.UTF8.GetBytes(snapshot.ToString()), catalog, "999000001", now, null);
        var snapshotPath = PcrClanGuide.SnapshotPaths(config).Single();
        var copies = new Dictionary<string, string>();
        foreach (var job in jobs)
        {
            var image = (string)job["source"]!["image_path"]!;
            if (!copies.TryGetValue(image, out var copy)) { copy = Path.Combine(root, "source-" + copies.Count + ".png"); File.Copy(image, copy); copies[image] = copy; }
            job["source"]!["image_path"] = copy;
        }
        var bundlePath = Path.Combine(root, "bundle.json"); File.WriteAllText(bundlePath, bundle.ToString(), new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(root, "fixture.json"), new JObject { ["synthetic"] = true, ["author_confirmation"] = false, ["game_evidence"] = false,
            ["source_bundle"] = original, ["source_sha256"] = originals[original], ["source_kind"] = "synthetic generated image and jobs", ["original_jobs"] = jobs.Length, ["fixture_account"] = "999000001",
            ["fixture_content_version"] = catalog.ContentVersion, ["fixture_observed_at"] = snapshot["observed_at"]!.DeepClone(), ["jobs"] = new JArray(JobIds),
            ["assumptions"] = "Only these three jobs use proposal IDs as explicit offline fixture choices; unknown stars=5, unknown SET=off, specified support=first member. This is not author review or a real account snapshot." }.ToString(), new UTF8Encoding(false));
        return new(root, config, Path.Combine(config, "agent-guide-review.json"), bundlePath, original, snapshotPath, originals);
    }
    public static void SeedPackage(string sourceRoot, string playerDirectory)
    {
        var player = Path.GetFullPath(playerDirectory); var allowed = Path.Combine(Path.GetFullPath(sourceRoot), ".runtime") + Path.DirectorySeparatorChar;
        if (!Directory.Exists(player) || !player.StartsWith(allowed, StringComparison.OrdinalIgnoreCase)) throw new IOException("合成种子只允许生成在当前源码 .runtime 内明确的新候选目录。");
        var fixture = Prepare(Path.Combine(player, ".runtime", "offline-fixture"), Path.Combine(player, "pcr-alpha-v1", "config"));
        var view = new PcrClanGuideView(sourceRoot, fixture.Guide, fixture.Config); view.LoadBundle(fixture.Bundle);
        foreach (var (id, slot) in JobIds.Select((id, n) => (id, n + 1)))
        { view.SelectJob(id); ApplyFixture(view, fixture); view.ConfirmAndSave(); Daily(view).AssignCurrent(slot); }
        Daily(view).ReviewThree();
        if (view.ExecutionMode != PcrClanDailyExecution.Simulation || File.Exists(PcrClanDailyExecution.PathFor(fixture.Config))) throw new InvalidOperationException("合成候选必须保持默认单次模拟。");
        File.WriteAllText(Path.Combine(fixture.Root, "seed-receipt.json"), new JObject { ["synthetic"] = true, ["seeded_by_test_helper"] = true, ["gui_entered_three_teams"] = false,
            ["author_confirmation"] = false, ["real_account_snapshot"] = false, ["game_input"] = false, ["mode"] = "simulation", ["confirmed_fixture_slots"] = 3,
            ["fixture_account"] = "999000001", ["fixture_content_version"] = "000000000001", ["snapshot_path"] = fixture.Snapshot,
            ["observed_at"] = PcrClanGuide.ReadJson(fixture.Snapshot)["observed_at"]!.DeepClone(), ["guide_path"] = fixture.Guide, ["daily_path"] = PcrClanDailyPlan.PathFor(fixture.Config),
            ["bundle_path"] = fixture.Bundle, ["source_preserved"] = fixture.OriginalHashes.All(p => PcrClanGuide.Stamp(p.Key) == p.Value), ["fixture_job_ids"] = new JArray(JobIds),
            ["assumptions"] = "Proposal IDs are explicit fixture choices; unknown star=5, unknown SET=off, specified support=first member. Not author review or live evidence. Root must record actual GUI actions separately." }.ToString(), new UTF8Encoding(false));
        Console.WriteLine("PCR clan package synthetic seed saved: " + Path.Combine(fixture.Root, "seed-receipt.json"));
    }
    private static PcrClanDailyPlanView Daily(PcrClanGuideView view) => view.GetLogicalDescendants().OfType<PcrClanDailyPlanView>().Single();
    private static void ApplyFixture(PcrClanGuideView view, Fixture fixture)
    {
        var value = view.Capture();
        if (!JobIds.Contains(value.SelectedJobId)) throw new InvalidDataException("只为 D 主表三队提供离线夹具核对值。");
        var candidates = (JArray)value.Job["members"]!; var ids = candidates.Select(m => (int)m["proposal_unit_id"]!).ToArray();
        value.Job["unit_ids"] = new JArray(ids); value.Job["stars"] = new JArray(candidates.Select(m => m["star"]?.Type == JTokenType.Integer ? m["star"]!.DeepClone() : new JValue(5)));
        value.Job["set_by_unit"] = new JObject(candidates.Select((m, n) => new JProperty(ids[n].ToString(), m["set"]?.Type == JTokenType.Boolean && (bool)m["set"]!)));
        value.Job["team_label"] = "离线fixture · " + value.Job["team_label"];
        if ((string?)value.Job["support_mode"] == "specified") value.Job["support_unit_id"] = ids[0];
        value.SnapshotPath = fixture.Snapshot; value.SnapshotSha256 = PcrClanGuide.Stamp(fixture.Snapshot)!; value.ConfirmedFor = null;
        view.Display(value);
    }
    public static void Run(string sourceRoot, string directory)
    {
        var fixture = Prepare(directory); var count = 0;
        void Require(bool pass, string label) { if (!pass) throw new InvalidOperationException("clan_workflow_" + label); count++; }
        void Throws(Action action, string label) { try { action(); } catch { count++; return; } throw new InvalidOperationException("clan_workflow_expected_rejection_" + label); }
        var view = new PcrClanGuideView(sourceRoot, fixture.Guide, fixture.Config); view.LoadBundle(fixture.OriginalBundle);
        Require(!view.HasCurrentConfirmation && !File.Exists(fixture.Guide), "synthetic_21_jobs_import_is_not_confirmation");
        view.LoadBundle(fixture.Bundle); var daily = Daily(view);
        foreach (var (id, slot) in JobIds.Select((id, n) => (id, n + 1)))
        {
            view.SelectJob(id); Require(!view.HasCurrentConfirmation, "proposal_not_confirmation_" + slot);
            Throws(() => daily.AssignCurrent(slot), "unreviewed_not_assigned_" + slot);
            ApplyFixture(view, fixture); Require(!view.HasCurrentConfirmation, "fixture_choice_still_needs_confirm_" + slot);
            view.ConfirmAndSave(); daily.AssignCurrent(slot);
            Require(view.HasCurrentConfirmation, "fixture_explicit_confirm_" + slot);
        }
        daily.ReviewThree(); Require(daily.SlotTexts.All(s => s.Contains("BOSS") && s.Contains("离线fixture")), "three_valid_targets");
        var legacy = File.ReadAllBytes(Path.Combine(fixture.Config, "guide-review.json")); var cache = File.ReadAllBytes(fixture.Snapshot);
        view = new PcrClanGuideView(sourceRoot, fixture.Guide, fixture.Config); daily = Daily(view); daily.ReviewThree();
        Require(view.HasCurrentConfirmation && daily.StatusText.Contains("实际出刀数与补偿仍未知"), "new_view_reopens_confirmation_and_three_slots");
        foreach (var (id, slot) in JobIds.Select((id, n) => (id, n + 1)))
        {
            view.LoadDailySlot(slot); var captured = view.Capture(); var saved = PcrClanGuide.Read(fixture.Guide);
            var start = PcrClanGuide.CreatePlan(fixture.Guide, fixture.Config, sourceRoot, DateTimeOffset.Now);
            Require(captured.SelectedJobId == id && saved.SelectedJobId == id && (string?)start["source"]?["job_id"] == id, "ui_disk_start_selected_job_" + slot);
            Require(captured.ConfirmedFor == saved.ConfirmedFor && saved.ConfirmedFor == (string?)start["confirmed_for"] && view.HasCurrentConfirmation, "ui_disk_start_confirmation_" + slot);
            Require(JToken.DeepEquals(captured.Job["unit_ids"], start["unit_ids"]) && JToken.DeepEquals(captured.Job["stars"], start["stars"])
                && JToken.DeepEquals(captured.Job["set_by_unit"], start["set_by_unit"]) && JToken.DeepEquals(captured.Job["auto"], start["auto"])
                && JToken.DeepEquals(captured.Job["support_unit_id"], start["support_unit_id"]), "visible_controls_match_start_" + slot);
            Require(saved.SavedJobs.Count == 3, "slot_load_keeps_all_saved_jobs_" + slot);
        }
        var dailyPath = PcrClanDailyPlan.PathFor(fixture.Config); var before = File.ReadAllBytes(fixture.Guide); var visible = view.Capture().SelectedJobId;
        void RejectUnchanged(Action mutate, Action restore, string label)
        {
            mutate(); try { Throws(() => view.LoadDailySlot(1), label); Require(view.Capture().SelectedJobId == visible && File.ReadAllBytes(fixture.Guide).SequenceEqual(before), label + "_keeps_current"); }
            finally { restore(); }
        }
        var originalBundle = File.ReadAllBytes(fixture.Bundle);
        RejectUnchanged(() => File.AppendAllText(fixture.Bundle, "\n"), () => File.WriteAllBytes(fixture.Bundle, originalBundle), "changed_bundle");
        var imagePath = (string)PcrClanGuide.Read(fixture.Guide).Job["source"]!["image_path"]!; var imageBytes = File.ReadAllBytes(imagePath);
        RejectUnchanged(() => File.AppendAllText(imagePath, "x"), () => File.WriteAllBytes(imagePath, imageBytes), "changed_source_image");
        RejectUnchanged(() => File.WriteAllBytes(fixture.Snapshot, cache.Concat(new byte[] { 32 }).ToArray()), () => File.WriteAllBytes(fixture.Snapshot, cache), "changed_cache");
        var state = PcrClanDailyPlan.Read(dailyPath, out _);
        Throws(() => PcrClanDailyPlan.Load(state, 1, fixture.Guide, fixture.Config, sourceRoot, DateTimeOffset.Now.AddHours(25), out _), "expired_cache_model");
        var expired = PcrClanGuide.ReadJson(fixture.Snapshot); expired["observed_at"] = DateTimeOffset.Now.AddHours(-25).ToString("O");
        File.WriteAllText(fixture.Snapshot, expired.ToString());
        var expiredGuide = PcrClanGuide.Read(fixture.Guide);
        foreach (var saved in expiredGuide.SavedJobs.Values) saved.SnapshotSha256 = PcrClanGuide.Stamp(fixture.Snapshot)!;
        File.WriteAllText(fixture.Guide, Newtonsoft.Json.JsonConvert.SerializeObject(expiredGuide));
        Throws(() => PcrClanDailyPlan.Load(state, 1, fixture.Guide, fixture.Config, sourceRoot, DateTimeOffset.Now, out _), "expired_cache_matching_hash");
        File.WriteAllBytes(fixture.Snapshot, cache); File.WriteAllBytes(fixture.Guide, before);
        using (var gate = new FileStream(fixture.Guide + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
        { Throws(() => view.LoadDailySlot(1), "guide_save_lock"); Require(File.ReadAllBytes(fixture.Guide).SequenceEqual(before) && view.Capture().SelectedJobId == visible, "save_lock_keeps_current"); }
        var externalGuide = PcrClanGuide.Read(fixture.Guide); PcrClanGuide.Select(externalGuide, fixture.Bundle, JobIds[1]);
        PcrClanGuide.Save(externalGuide, fixture.Guide, PcrClanGuide.Stamp(fixture.Guide), fixture.Config, sourceRoot, DateTimeOffset.Now, true); var externalBytes = File.ReadAllBytes(fixture.Guide);
        Throws(() => view.LoadDailySlot(1), "stale_guide_window");
        Require(File.ReadAllBytes(fixture.Guide).SequenceEqual(externalBytes) && view.Capture().SelectedJobId == visible, "concurrent_guide_kept");
        view.Reload(); Require(view.Capture().SelectedJobId == JobIds[1] && view.HasCurrentConfirmation, "parent_reload_reads_current_guide");
        var allSlots = File.ReadAllBytes(dailyPath); var changed = PcrClanDailyPlan.Read(dailyPath, out var stamp); PcrClanDailyPlan.Clear(changed, 2); PcrClanDailyPlan.Save(changed, dailyPath, stamp);
        Throws(() => view.LoadDailySlot(1), "stale_daily_window");
        view.Reload(); Require(Daily(view).SlotTexts[1].Contains("尚未设置") && Daily(view).StatusText.Contains("2/3"), "parent_reload_refreshes_child_summary");
        File.WriteAllBytes(dailyPath, allSlots); view.Reload(); daily = Daily(view); daily.ReviewThree();
        CheckImageLifecycle(sourceRoot, fixture, view, Require);
        Require(File.ReadAllBytes(Path.Combine(fixture.Config, "guide-review.json")).SequenceEqual(legacy) && File.ReadAllBytes(fixture.Snapshot).SequenceEqual(cache), "fixture_shared_sources_unchanged");
        Require(fixture.OriginalHashes.All(p => PcrClanGuide.Stamp(p.Key) == p.Value), "all_original_21_job_sources_unchanged");
        File.WriteAllText(Path.Combine(fixture.Root, "checks.json"), new JObject { ["checks"] = count, ["synthetic"] = true, ["original_jobs"] = 21, ["valid_slots"] = 3,
            ["game_input"] = false, ["owner_created"] = false, ["real_account_updated"] = false, ["source_preserved"] = true }.ToString(), new UTF8Encoding(false));
        Console.WriteLine($"PCR clan workflow checks passed: {count}; synthetic21/fixture3, UI=disk=Start, Owner/device/input=0");
    }
    private static void CheckImageLifecycle(string sourceRoot, Fixture fixture, PcrClanGuideView view, Action<bool, string> check)
    {
        const BindingFlags hidden = BindingFlags.NonPublic | BindingFlags.Instance;
        var image = (Image)typeof(PcrClanGuideView).GetField("image", hidden)!.GetValue(view)!;
        var preview = (Image)typeof(PcrClanGuideView).GetField("preview", hidden)!.GetValue(view)!;
        var sourceDetails = (Expander)typeof(PcrClanGuideView).GetField("sourceDetails", hidden)!.GetValue(view)!;
        check(!sourceDetails.IsExpanded && image.Source == null && preview.Source == null, "collapsed_source_does_not_decode_images");
        sourceDetails.IsExpanded = true;
        var boxes = (Dictionary<string, TextBox>)typeof(PcrClanGuideView).GetField("fields", hidden)!.GetValue(view)!;
        boxes["boss_name"].Text = "离线未保存草稿：保留图片与编辑";
        var draft = JObject.FromObject(view.Capture()); var diskHash = PcrClanGuide.Stamp(fixture.Guide);
        var firstImage = image.Source; var firstPreview = preview.Source;
        check(firstImage != null && firstPreview != null && !view.HasCurrentConfirmation, "lifecycle_has_two_images_and_unsaved_draft");
        var host = new Window { Content = view, Width = 1100, Height = 800, ShowActivated = false, ShowInTaskbar = false, Opacity = 0 };
        host.Show(); Dispatcher.UIThread.RunJobs();
        try
        {
            check(ReferenceEquals(image.Source, firstImage) && ReferenceEquals(preview.Source, firstPreview), "initial_attach_does_not_allocate_duplicate_images");
            for (var cycle = 1; cycle <= 2; cycle++)
            {
                var beforeImage = image.Source; var beforePreview = preview.Source;
                host.Content = null; Dispatcher.UIThread.RunJobs();
                check(image.Source == null && preview.Source == null, "detach_releases_image_sources_" + cycle);
                host.Content = view; Dispatcher.UIThread.RunJobs();
                check(image.Source != null && preview.Source != null && !ReferenceEquals(image.Source, beforeImage) && !ReferenceEquals(preview.Source, beforePreview), "reattach_recreates_both_images_" + cycle);
                check(JToken.DeepEquals(draft, JObject.FromObject(view.Capture())) && PcrClanGuide.Stamp(fixture.Guide) == diskHash && !view.HasCurrentConfirmation,
                    "reattach_preserves_unsaved_fields_confirmation_and_disk_" + cycle);
            }
            host.Content = null; Dispatcher.UIThread.RunJobs();
            var fresh = new PcrClanGuideView(sourceRoot, fixture.Guide, fixture.Config);
            var freshImage = (Image)typeof(PcrClanGuideView).GetField("image", hidden)!.GetValue(fresh)!;
            var freshPreview = (Image)typeof(PcrClanGuideView).GetField("preview", hidden)!.GetValue(fresh)!;
            var freshDetails = (Expander)typeof(PcrClanGuideView).GetField("sourceDetails", hidden)!.GetValue(fresh)!;
            freshDetails.IsExpanded = true;
            check(fresh.HasCurrentConfirmation, "source_change_case_starts_confirmed");
            host.Content = fresh; Dispatcher.UIThread.RunJobs(); host.Content = null; Dispatcher.UIThread.RunJobs();
            var path = (string)fresh.Capture().Job["source"]!["image_path"]!; var bytes = File.ReadAllBytes(path);
            try
            {
                File.WriteAllBytes(path, bytes.Concat(new byte[] { 0 }).ToArray());
                host.Content = fresh; Dispatcher.UIThread.RunJobs();
                check(freshImage.Source == null && freshPreview.Source == null && !fresh.HasCurrentConfirmation && fresh.Capture().ConfirmedFor == null
                    && fresh.StatusText.Contains("图片"), "reattach_changed_source_stays_blank_and_revokes_confirmation");
            }
            finally { File.WriteAllBytes(path, bytes); host.Content = null; Dispatcher.UIThread.RunJobs(); }
            host.Content = fresh; Dispatcher.UIThread.RunJobs();
            check(freshImage.Source != null && freshPreview.Source != null && !fresh.HasCurrentConfirmation && PcrClanGuide.Stamp(fixture.Guide) == diskHash,
                "restored_source_reloads_images_without_restoring_confirmation_or_saving");
        }
        finally { host.Content = null; host.Close(); Dispatcher.UIThread.RunJobs(); }
    }
    public static void ShowWindow(string sourceRoot, string directory)
    {
        var fixture = Prepare(directory); var view = new PcrClanGuideView(sourceRoot, fixture.Guide, fixture.Config);
        var note = new TextBlock { Text = "离线流程验收／合成账号 999000001。21 队及原图均为生成的合成夹具，三队 proposal 和未知值只作夹具选择；无作者确认、无 Owner、无连接或开始任务。仅写隔离 config。", TextWrapping = Avalonia.Media.TextWrapping.Wrap };
        var import = new Button { Content = "导入合成离线样例" }; import.Click += (_, _) => view.LoadBundle(fixture.Bundle);
        var fill = new Button { Content = "填入当前队离线核对值（不确认）" }; fill.Click += (_, _) => { try { ApplyFixture(view, fixture); note.Text = "离线核对值已填入，仍须点击生产面板的核对保存。合成字段说明见 fixture.json。"; } catch (Exception ex) { note.Text = ex.Message; } };
        var actions = new WrapPanel(); actions.Children.Add(import); actions.Children.Add(fill);
        var panel = new DockPanel { Margin = new Avalonia.Thickness(12) }; var head = new StackPanel { Spacing = 6 }; head.Children.Add(note); head.Children.Add(actions); DockPanel.SetDock(head, Dock.Top); panel.Children.Add(head); panel.Children.Add(view);
        var window = new Window { Title = "PCR Agent 离线流程验收／合成账号", Width = 1160, Height = 940, Content = panel, Background = Brushes.White };
        using var closed = new CancellationTokenSource(); window.Closed += (_, _) => closed.Cancel(); window.Show();
        Dispatcher.UIThread.MainLoop(closed.Token);
        File.WriteAllText(Path.Combine(fixture.Root, "window-closed.json"), new JObject { ["closed"] = true, ["game_input"] = false, ["owner_created"] = false,
            ["sources_preserved"] = fixture.OriginalHashes.All(p => PcrClanGuide.Stamp(p.Key) == p.Value) }.ToString(), new UTF8Encoding(false));
    }
}
