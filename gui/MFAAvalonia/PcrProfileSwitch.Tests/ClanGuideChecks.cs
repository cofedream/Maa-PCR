using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Pcr;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal static class ClanGuideChecks
{
    public static void Run(string directory)
    {
        var root = Path.GetFullPath(Path.Combine(directory, "clan-guide")); Directory.CreateDirectory(root);
        var config = Path.Combine(root, "config"); Directory.CreateDirectory(config);
        var now = DateTimeOffset.Now; var count = 0;
        void Require(bool value, string name) { if (!value) throw new Exception("clan_guide_" + name); count++; }
        void Throws(Action action, string name) { try { action(); } catch { count++; return; } throw new Exception("clan_guide_expected_rejection_" + name); }
        PcrClanGuideDocument Copy(PcrClanGuideDocument value) => JsonConvert.DeserializeObject<PcrClanGuideDocument>(JsonConvert.SerializeObject(value))!;
        var catalog = new PcrGuideCatalog { Server = "cn-bilibili", ContentVersion = "202610141431", Source = "synthetic offline fixture", Sha256 = new string('a', 64),
            Units = Enumerable.Range(1, 6).Select(i => new PcrGuideUnit(100001 + i * 100, "合成角色" + i)).ToList() };
        var legacy = Path.Combine(config, "guide-review.json");
        var oldStore = new PcrGuideStore(legacy); oldStore.Load(); oldStore.Save(new() { Input = new() { Catalog = catalog, Title = "原深域草稿" } });
        var originalLegacy = File.ReadAllBytes(legacy);
        var snapshot = new JObject { ["schema_version"] = 1, ["server"] = "cn-bilibili", ["account_id"] = "123456", ["content_version"] = catalog.ContentVersion,
            ["observed_at"] = now.AddMinutes(-5).ToString("O"), ["ownership_complete"] = true, ["unknown_fields"] = new JArray(),
            ["units"] = new JArray(catalog.Units.Take(5).Select(u => new JObject { ["unit_id"] = u.UnitId, ["name"] = u.Name, ["battle_rarity"] = 5 })) };
        var cache = new PcrGuideAccountStore(config); cache.Save(Encoding.UTF8.GetBytes(snapshot.ToString()), catalog, "123456", now, null);
        var snapshotPath = PcrClanGuide.SnapshotPaths(config).Single(); var originalSnapshot = File.ReadAllBytes(snapshotPath);
        var imagePath = Path.Combine(root, "synthetic-source.png");
        using (var bitmap = new RenderTargetBitmap(new PixelSize(8, 8), new Vector(96, 96))) bitmap.Save(imagePath);
        var originalImage = File.ReadAllBytes(imagePath);
        var ids = catalog.Units.Take(5).Select(u => u.UnitId).ToArray();
        JObject Job(string id, int page, string package) => new()
        {
            ["period"] = "2026-09", ["phase"] = page == 1 ? "BC" : "D", ["round_min"] = page == 1 ? 1 : 23, ["round_max"] = page == 1 ? new JValue(22) : JValue.CreateNull(),
            ["boss"] = page == 1 ? 1 : 5, ["boss_name"] = "合成 BOSS", ["package"] = package, ["team_label"] = "合成队伍" + id,
            ["unit_ids"] = new JArray(ids), ["stars"] = new JArray(ids.Select(_ => JValue.CreateNull())), ["auto"] = true,
            ["set_by_unit"] = new JObject(ids.Select((uid, index) => new JProperty(uid.ToString(), index % 2 == 0))),
            ["switches"] = new JArray(), ["support_mode"] = "none", ["support_unit_id"] = JValue.CreateNull(), ["reference_damage"] = 3200000000L, ["image_base64"] = Convert.ToBase64String(originalImage),
            ["source"] = new JObject { ["url"] = "https://www.bilibili.com/video/BV1Rbh86XEsq/" + (page > 1 ? "?p=" + page : ""), ["bvid"] = "BV1Rbh86XEsq", ["page"] = page,
                ["cid"] = page == 1 ? 42138340822L : 42155640154L, ["at_seconds"] = 12, ["image_path"] = imagePath, ["image_sha256"] = PcrClanGuide.Hash(originalImage), ["job_id"] = id }
        };
        var bundle = new JObject { ["version"] = 1, ["source"] = new JObject { ["url"] = "https://www.bilibili.com/video/BV1Rbh86XEsq/", ["bvid"] = "BV1Rbh86XEsq", ["title"] = "合成离线样例", ["author"] = "fixture", ["period"] = "2026-09" },
            ["pages"] = new JArray(new JObject { ["page"] = 1, ["cid"] = 42138340822L, ["label"] = "BC" }, new JObject { ["page"] = 2, ["cid"] = 42155640154L, ["label"] = "D" }),
            ["jobs"] = new JArray(Job("bc-a", 1, "套餐甲"), Job("bc-b", 1, "套餐乙"), Job("d-a", 2, "套餐甲")) };
        var candidateJob = Job("candidate", 1, "待核对候选"); candidateJob["unit_ids"] = new JArray(ids.Select(_ => JValue.CreateNull())); candidateJob["set_by_unit"] = new JObject();
        candidateJob["members"] = new JArray(ids.Select((id, index) => new JObject { ["unit_id"] = JValue.CreateNull(), ["proposal_unit_id"] = id, ["proposal_name"] = catalog.Units[index].Name, ["set"] = index % 2 == 0 }));
        ((JArray)bundle["jobs"]!).Add(candidateJob);
        var unknownAxisJob = Job("unknown-axis", 1, "未知原轴"); unknownAxisJob["switches"] = JValue.CreateNull(); ((JArray)bundle["jobs"]!).Add(unknownAxisJob);
        var bundlePath = Path.Combine(root, "bundle.json"); File.WriteAllText(bundlePath, bundle.ToString(), new UTF8Encoding(false)); var originalBundle = File.ReadAllBytes(bundlePath);
        var statePath = Path.Combine(config, "agent-guide-review.json");
        var document = new PcrClanGuideDocument { SnapshotPath = snapshotPath, SnapshotSha256 = PcrClanGuide.Hash(originalSnapshot) };
        PcrClanGuide.Select(document, bundlePath, "bc-a");
        Require(document.ConfirmedFor == null, "import_never_confirms");
        var plan = PcrClanGuide.BuildPlan(document, config, root, now);
        Require((long)plan["source"]!["cid"]! == 42138340822L && (long)plan["reference_damage"]! == 3200000000L, "large_cid_and_damage");
        Require(plan["stars"]!.Values<int>().All(x => x == 5), "missing_guide_stars_use_snapshot_battle_rarity");
        Require(PcrClanGuide.Fingerprint(JObject.Parse("{\"b\":1,\"a\":[true,\"雪\"]}")) == PcrClanGuide.Hash(Encoding.UTF8.GetBytes("{\"a\":[true,\"雪\"],\"b\":1}")), "canonical_ordinal_utf8");
        PcrClanGuide.Save(document, statePath, null, config, root, now, false);
        Throws(() => PcrClanGuide.CreatePlan(statePath, config, root, now), "draft_cannot_start");
        PcrClanGuide.Save(document, statePath, PcrClanGuide.Stamp(statePath), config, root, now, true);
        var reopened = PcrClanGuide.Read(statePath);
        _ = PcrClanGuide.Read(statePath, out var readStamp); Require(readStamp == PcrClanGuide.Stamp(statePath), "read_hash_belongs_to_loaded_bytes");
        Require(JToken.DeepEquals(plan, PcrClanGuide.CreatePlan(statePath, config, root, now)), "confirmed_save_reopen");
        Require(reopened.Job["stars"]!.All(s => s.Type == JTokenType.Null), "snapshot_fallback_does_not_rewrite_guide_reference");
        Require(reopened.Job["image_base64"] == null, "selection_references_original_media_without_copy");
        var edited = Copy(reopened); edited.Job["boss"] = 2;
        File.WriteAllText(statePath, JsonConvert.SerializeObject(edited));
        Throws(() => PcrClanGuide.CreatePlan(statePath, config, root, now), "target_change_invalidates_confirmation");
        PcrClanGuide.Save(reopened, statePath, PcrClanGuide.Stamp(statePath), config, root, now, true);
        var firstStamp = PcrClanGuide.Stamp(statePath); var concurrent = Copy(reopened); concurrent.Job["team_label"] = "另一个窗口";
        PcrClanGuide.Save(concurrent, statePath, firstStamp, config, root, now, false); var kept = File.ReadAllBytes(statePath);
        Throws(() => PcrClanGuide.Save(reopened, statePath, firstStamp, config, root, now, true), "concurrent_save");
        Require(File.ReadAllBytes(statePath).SequenceEqual(kept), "concurrent_save_preserves_original");
        PcrClanGuide.Save(reopened, statePath, PcrClanGuide.Stamp(statePath), config, root, now, true);
        var cross = Copy(reopened); PcrClanGuide.Select(cross, bundlePath, "bc-b");
        Require(cross.ConfirmedFor == null && (string?)cross.Job["package"] == "套餐乙", "package_change_clears_confirmation");
        PcrClanGuide.Select(cross, bundlePath, "d-a");
        Require((int)PcrClanGuide.BuildPlan(cross, config, root, now)["source"]!["page"]! == 2 && (string?)cross.Job["phase"] == "D", "cross_page_separate_phase");
        var multiplePath = Path.Combine(config, "multiple-state.json"); var multiple = Copy(reopened); multiple.Job["boss"] = 2;
        var multipleHash = PcrClanGuide.Save(multiple, multiplePath, null, config, root, now, true);
        PcrClanGuide.Select(multiple, bundlePath, "d-a"); multipleHash = PcrClanGuide.Save(multiple, multiplePath, multipleHash, config, root, now, true);
        PcrClanGuide.Select(multiple, bundlePath, "bc-a");
        Require((int)multiple.Job["boss"]! == 2 && multiple.ConfirmedFor == (string?)PcrClanGuide.BuildPlan(multiple, config, root, now)["confirmed_for"], "bc_d_bc_restores_reviewed_changes_and_confirmation");
        multipleHash = PcrClanGuide.Save(multiple, multiplePath, multipleHash, config, root, now, true);
        var multipleReopened = PcrClanGuide.Read(multiplePath); PcrClanGuide.Select(multipleReopened, bundlePath, "d-a"); PcrClanGuide.Select(multipleReopened, bundlePath, "bc-a");
        Require((int)multipleReopened.Job["boss"]! == 2 && multipleReopened.ConfirmedFor != null, "saved_team_revisions_survive_reopen");
        multipleReopened.SnapshotSha256 = new string('0', 64); PcrClanGuide.Select(multipleReopened, bundlePath, "bc-a");
        Require(multipleReopened.ConfirmedFor == null, "recalled_team_new_snapshot_clears_confirmation");
        void Reject(Action<PcrClanGuideDocument> change, string name)
        { var bad = Copy(reopened); change(bad); Throws(() => PcrClanGuide.BuildPlan(bad, config, root, now), name); }
        Reject(d => d.Job["boss"] = JValue.CreateNull(), "unknown_boss");
        Reject(d => d.Job["unit_ids"]![0] = d.Job["unit_ids"]![1]!.DeepClone(), "duplicate_member");
        Reject(d => d.Job["set_by_unit"]![ids[0].ToString()] = JValue.CreateNull(), "unknown_set_never_default_true");
        Reject(d => d.Job["auto"] = JValue.CreateNull(), "unknown_auto");
        Reject(d => d.Job["source"]!["page"] = 2, "source_cannot_be_rebound_by_edit");
        Reject(d => d.Job["stars"]![0] = 7, "invalid_star");
        Reject(d => d.Job["support_mode"] = "specified", "specified_support_required");
        Reject(d => d.SnapshotSha256 = new string('0', 64), "snapshot_changed");
        var qsjs = Copy(reopened); qsjs.Job["support_mode"] = "qsjs";
        Require((string?)PcrClanGuide.BuildPlan(qsjs, config, root, now)["support_mode"] == "qsjs", "qsjs_zero_missing_keeps_own");
        qsjs.Job["unit_ids"]![4] = catalog.Units[5].UnitId; qsjs.Job["stars"]![4] = 5;
        ((JObject)qsjs.Job["set_by_unit"]!).Remove(ids[4].ToString()); qsjs.Job["set_by_unit"]![catalog.Units[5].UnitId.ToString()] = true;
        var qsjsPlan = PcrClanGuide.BuildPlan(qsjs, config, root, now);
        Require((int)qsjsPlan["unit_ids"]![4]! == catalog.Units[5].UnitId, "qsjs_one_missing_exact_identity");
        var specified = Copy(qsjs); specified.Job["support_mode"] = "specified"; specified.Job["support_unit_id"] = catalog.Units[5].UnitId;
        var specifiedPlan = PcrClanGuide.BuildPlan(specified, config, root, now);
        Require((int)specifiedPlan["support_unit_id"]! == catalog.Units[5].UnitId, "specified_support");
        var incomplete = (JObject)snapshot.DeepClone(); incomplete["ownership_complete"] = false; var incompleteBytes = Encoding.UTF8.GetBytes(incomplete.ToString());
        File.WriteAllBytes(snapshotPath, incompleteBytes); var partial = Copy(qsjs); partial.SnapshotSha256 = PcrClanGuide.Hash(incompleteBytes);
        Throws(() => PcrClanGuide.BuildPlan(partial, config, root, now), "partial_absence_remains_unknown");
        var partialOwned = Copy(reopened); partialOwned.SnapshotSha256 = PcrClanGuide.Hash(incompleteBytes);
        Require((string?)PcrClanGuide.BuildPlan(partialOwned, config, root, now)["account_id"] == "123456", "partial_all_five_explicitly_owned");
        var partialSupport = Copy(specified); partialSupport.SnapshotSha256 = PcrClanGuide.Hash(incompleteBytes);
        Require((int)PcrClanGuide.BuildPlan(partialSupport, config, root, now)["support_unit_id"]! == catalog.Units[5].UnitId, "partial_four_owned_plus_specified_support");
        incomplete["unknown_fields"] = new JArray("units." + catalog.Units[5].UnitId); var unknownSupportBytes = Encoding.UTF8.GetBytes(incomplete.ToString());
        File.WriteAllBytes(snapshotPath, unknownSupportBytes); partialSupport.SnapshotSha256 = PcrClanGuide.Hash(unknownSupportBytes);
        Require((int)PcrClanGuide.BuildPlan(partialSupport, config, root, now)["support_unit_id"]! == catalog.Units[5].UnitId, "specified_borrowed_row_unknown_is_not_own_requirement");
        incomplete["unknown_fields"] = new JArray("units." + ids[0]); var unknownOwnedBytes = Encoding.UTF8.GetBytes(incomplete.ToString());
        File.WriteAllBytes(snapshotPath, unknownOwnedBytes); partialSupport.SnapshotSha256 = PcrClanGuide.Hash(unknownOwnedBytes);
        Throws(() => PcrClanGuide.BuildPlan(partialSupport, config, root, now), "specified_own_member_unknown_stops");
        incomplete["unknown_fields"] = new JArray("units." + ids[0] + ".ownership"); var unknownOwnershipBytes = Encoding.UTF8.GetBytes(incomplete.ToString());
        File.WriteAllBytes(snapshotPath, unknownOwnershipBytes); partialSupport.SnapshotSha256 = PcrClanGuide.Hash(unknownOwnershipBytes);
        Throws(() => PcrClanGuide.BuildPlan(partialSupport, config, root, now), "ownership_leaf_unknown_stops");
        incomplete["unknown_fields"] = new JArray("units." + ids[0] + ".battle_rarity"); var unknownStarBytes = Encoding.UTF8.GetBytes(incomplete.ToString());
        File.WriteAllBytes(snapshotPath, unknownStarBytes); partialSupport.SnapshotSha256 = PcrClanGuide.Hash(unknownStarBytes);
        Throws(() => PcrClanGuide.BuildPlan(partialSupport, config, root, now), "unknown_star_value_cannot_be_snapshot_fallback");
        partialSupport.Job["stars"]![0] = 5;
        Require(PcrClanGuide.BuildPlan(partialSupport, config, root, now)["stars"]![0]!.Value<int>() == 5, "explicit_guide_star_does_not_claim_unknown_snapshot_value");
        var missingTwo = (JObject)snapshot.DeepClone(); missingTwo["units"] = new JArray(((JArray)missingTwo["units"]!).Take(3));
        var missingTwoBytes = Encoding.UTF8.GetBytes(missingTwo.ToString()); File.WriteAllBytes(snapshotPath, missingTwoBytes);
        var qsjsMissingTwo = Copy(reopened); qsjsMissingTwo.SnapshotSha256 = PcrClanGuide.Hash(missingTwoBytes); qsjsMissingTwo.Job["support_mode"] = "qsjs"; qsjsMissingTwo.Job["stars"] = new JArray(ids.Select(_ => 5));
        Throws(() => PcrClanGuide.BuildPlan(qsjsMissingTwo, config, root, now), "qsjs_two_missing_stops");
        File.WriteAllBytes(snapshotPath, originalSnapshot);
        var timed = Copy(reopened); timed.Job["switches"] = new JArray(new JObject { ["remaining_seconds"] = 75, ["auto"] = JValue.CreateNull(), ["set_by_unit"] = new JObject { [ids[0].ToString()] = false } },
            new JObject { ["remaining_seconds"] = 50, ["auto"] = false, ["set_by_unit"] = new JObject() });
        var timedPlan = PcrClanGuide.BuildPlan(timed, config, root, now);
        Require(timedPlan["switches"]!.Count() == 2, "explicit_seconds_axis");
        timed.Job["switches"]![1]!["remaining_seconds"] = 75;
        Throws(() => PcrClanGuide.BuildPlan(timed, config, root, now), "duplicate_axis_second");
        File.WriteAllBytes(imagePath, originalImage.Concat(new byte[] { 0 }).ToArray());
        Throws(() => PcrClanGuide.BuildPlan(reopened, config, root, now), "concurrent_image_change"); File.WriteAllBytes(imagePath, originalImage);
        File.WriteAllText(bundlePath, bundle.ToString() + "\n");
        Throws(() => PcrClanGuide.BuildPlan(reopened, config, root, now), "concurrent_bundle_change"); File.WriteAllBytes(bundlePath, originalBundle);
        Throws(() => PcrClanGuide.BuildPlan(reopened, config, root, now.AddHours(25)), "expired_snapshot");
        var publicBefore = Directory.Exists(Path.Combine(config, "guide-library")) ? Directory.GetFiles(Path.Combine(config, "guide-library"), "*.json") : Array.Empty<string>();
        PcrClanGuide.SavePublicGuide(reopened, config, root);
        var library = new PcrGuideLibrary(Path.Combine(config, "guide-library")); library.Reload();
        Require(library.Entries.Count >= 1 && library.Entries.Any(e => e.Label.Contains("公会战")), "shared_public_guide_library");
        var publicText = Encoding.UTF8.GetString(library.Read(library.Entries.First(e => e.Label.Contains("公会战")).Key));
        Require(!publicText.Contains("123456") && !publicText.Contains(snapshotPath.Replace("\\", "\\\\")), "library_has_no_account_snapshot");
        var publicPath = Directory.GetFiles(Path.Combine(config, "guide-library"), "*.json").Single(); var publicOriginal = File.ReadAllBytes(publicPath);
        var changedPublic = Copy(reopened); changedPublic.Job["set_by_unit"]![ids[0].ToString()] = false; PcrClanGuide.SavePublicGuide(changedPublic, config, root);
        Require(File.ReadAllBytes(publicPath).SequenceEqual(publicOriginal), "existing_public_entry_not_overwritten");
        changedPublic.Job["source"]!["url"] = "https://www.bilibili.com/video/BV1Xunyz2E21/"; PcrClanGuide.SavePublicGuide(changedPublic, config, root); library.Reload();
        Require(library.Entries.Count == 2, "same_label_distinct_source_saved_as_separate_public_guide");
        Require(File.ReadAllBytes(legacy).SequenceEqual(originalLegacy) && File.ReadAllBytes(snapshotPath).SequenceEqual(originalSnapshot), "original_shared_files_unchanged");
        var view = new PcrClanGuideView(root, statePath, config); Dispatcher.UIThread.RunJobs();
        Require(view.HasCurrentConfirmation, "ui_reopen_confirmation");
        var boxes = (Dictionary<string, TextBox>)typeof(PcrClanGuideView).GetField("fields", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(view)!;
        boxes["boss"].Text = "2"; Dispatcher.UIThread.RunJobs();
        Require(!view.HasCurrentConfirmation && view.Capture().ConfirmedFor == null, "ui_edit_clears_confirmation");
        view.SaveDraft(); Require(PcrClanGuide.Read(statePath).ConfirmedFor == null, "ui_saves_only_draft_after_edit");
        view.Display(reopened); view.ConfirmAndSave(); Require(view.HasCurrentConfirmation, "ui_once_confirm");
        var second = new PcrClanGuideView(root, statePath, config); Dispatcher.UIThread.RunJobs();
        Require(second.HasCurrentConfirmation, "ui_save_reopen");
        var refreshedSnapshot = (JObject)snapshot.DeepClone(); refreshedSnapshot["observed_at"] = now.ToString("O");
        var savedGuideBytes = File.ReadAllBytes(statePath); var beforeRefreshJob = second.Capture().Job.ToString();
        try
        {
            cache.Save(Encoding.UTF8.GetBytes(refreshedSnapshot.ToString()), catalog, "123456", now, cache.Stamp(catalog, "123456"));
            second.RefreshAccounts();
            Require(second.Capture().SnapshotSha256 == PcrClanGuide.Hash(originalSnapshot) && !second.HasCurrentConfirmation, "list_reload_does_not_adopt_new_snapshot");
            var secondBoxes = (Dictionary<string, TextBox>)typeof(PcrClanGuideView).GetField("fields", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(second)!;
            secondBoxes["boss_name"].Text = "刷新期间未保存的 BOSS 备注"; beforeRefreshJob = second.Capture().Job.ToString();
            second.UseLatestAccount();
            Require(second.Capture().SnapshotSha256 == PcrClanGuide.Stamp(snapshotPath) && second.Capture().ConfirmedFor == null
                && second.Capture().Job.ToString() == beforeRefreshJob && File.ReadAllBytes(statePath).SequenceEqual(savedGuideBytes), "explicit_latest_account_preserves_draft_and_requires_review");
            second.ConfirmAndSave(); Require(second.HasCurrentConfirmation, "latest_account_can_be_reviewed_again");
        }
        finally { File.WriteAllBytes(snapshotPath, originalSnapshot); File.WriteAllBytes(statePath, savedGuideBytes); second.Reload(); }
        second.LoadBundle(bundlePath); second.SelectJob("candidate");
        Require(second.Capture().Job["unit_ids"]!.Values<int>().SequenceEqual(ids) && second.Capture().Job["set_by_unit"]![ids[0].ToString()]!.Type == JTokenType.Boolean
            && !second.HasCurrentConfirmation, "ui_prefills_candidates_but_never_confirmation");
        second.SelectJob("unknown-axis"); Require(second.Capture().Job["switches"]!.Type == JTokenType.Null, "unknown_axis_not_silently_empty");
        second.SaveDraft(); var unknownReopen = new PcrClanGuideView(root, statePath, config);
        Require(unknownReopen.Capture().Job["switches"]!.Type == JTokenType.Null && !unknownReopen.HasCurrentConfirmation, "unknown_axis_survives_draft_reopen");
        Throws(() => unknownReopen.ConfirmAndSave(), "unknown_axis_cannot_confirm_after_only_opening_review");
        var axisBox = (TextBox)typeof(PcrClanGuideView).GetField("switches", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(unknownReopen)!;
        axisBox.Text = "无切换"; unknownReopen.ConfirmAndSave();
        Require(unknownReopen.HasCurrentConfirmation && unknownReopen.Capture().Job["switches"] is JArray emptyAxis && emptyAxis.Count == 0, "explicit_no_switches_resolves_unknown_axis");
        second.LoadBundle(bundlePath);
        var failurePagePicker = (ComboBox)typeof(PcrClanGuideView).GetField("page", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(second)!;
        var sourceDetails = (Expander)typeof(PcrClanGuideView).GetField("sourceDetails", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(second)!;
        sourceDetails.IsExpanded = true;
        File.WriteAllBytes(imagePath, originalImage.Concat(new byte[] { 0 }).ToArray());
        try
        {
            failurePagePicker.SelectedIndex = 1; Dispatcher.UIThread.RunJobs();
            Require(second.StatusText.Contains("作业不可用") && !second.HasCurrentConfirmation, "selection_image_failure_stays_inside_ui");
        }
        finally { File.WriteAllBytes(imagePath, originalImage); }
        second.LoadBundle(bundlePath); second.SelectJob("d-a");
        Require(!second.HasCurrentConfirmation && (string?)second.Capture().Job["phase"] == "D", "ui_cross_page_never_reuses_confirmation");
        var pagePicker = (ComboBox)typeof(PcrClanGuideView).GetField("page", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(second)!;
        Require(pagePicker.SelectedItem!.ToString()!.Contains("P2"), "ui_programmatic_selection_keeps_page_picker_in_sync");
        sourceDetails.IsExpanded = false;
        second.Background = Avalonia.Media.Brushes.White;
        var host = new Window { Content = second, Background = Avalonia.Media.Brushes.White, Width = 1120, Height = 900, ShowInTaskbar = false, ShowActivated = false, Opacity = 0 };
        host.Show();
        try
        {
            second.Measure(new Size(1120, 900)); second.Arrange(new Rect(0, 0, 1120, 900)); second.UpdateLayout(); Dispatcher.UIThread.RunJobs();
            using var rendered = new RenderTargetBitmap(new PixelSize(1120, 900), new Vector(96, 96)); rendered.Render(second); rendered.Save(Path.Combine(root, "clan-guide-view.png"));
            Require((string?)second.Capture().Job["phase"] == "D", "offline_layout_keeps_selection");
        }
        finally { host.Close(); }
        Require(File.ReadAllBytes(legacy).SequenceEqual(originalLegacy) && File.ReadAllBytes(snapshotPath).SequenceEqual(originalSnapshot), "ui_keeps_shared_private_files");
        var displayOnlyRoot = Path.Combine(root, "display-roster-only"); var displayOnlyConfig = Path.Combine(displayOnlyRoot, "config");
        var sharedRoster = Path.Combine(displayOnlyRoot, "assets", "labyrinth", "character-pack", "characters.landosolroster.json"); Directory.CreateDirectory(Path.GetDirectoryName(sharedRoster)!);
        File.WriteAllText(sharedRoster, new JObject { ["schemaVersion"] = 1, ["characters"] = new JArray(catalog.Units.Select(u => new JObject { ["id"] = (u.UnitId / 100).ToString(), ["name"] = u.Name, ["officialName"] = u.Name })) }.ToString(), new UTF8Encoding(false));
        var freshView = new PcrClanGuideView(displayOnlyRoot, Path.Combine(displayOnlyConfig, "agent-guide-review.json"), displayOnlyConfig); freshView.LoadBundle(bundlePath);
        Require(freshView.Capture().Job["unit_ids"]!.Values<int>().SequenceEqual(ids) && !freshView.HasCurrentConfirmation, "fresh_profile_shares_roster_for_display_only");
        Throws(() => PcrClanGuide.Catalog(displayOnlyConfig, displayOnlyRoot, catalog.ContentVersion), "display_roster_never_forges_versioned_catalog");
        Throws(() => freshView.ConfirmAndSave(), "fresh_without_original_account_catalog_cannot_confirm");
        var plans = new JArray(plan, PcrClanGuide.BuildPlan(cross, config, root, now), qsjsPlan, specifiedPlan, timedPlan);
        File.WriteAllText(Path.Combine(root, "managed-plan-cases.json"), plans.ToString(), new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(root, "checks.json"), new JObject { ["checks"] = count, ["synthetic"] = true, ["game_input"] = false, ["original_cache_unchanged"] = true }.ToString(), new UTF8Encoding(false));
        Console.WriteLine($"PCR clan guide checks passed: {count}; synthetic controls, no game input");
    }
}
