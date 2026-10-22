using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using Avalonia.Controls;
using Avalonia.Styling;
using Avalonia.Threading;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using Newtonsoft.Json.Linq;

internal static class ClanFormalChecks
{
    public static void Run(string sourceRoot, string directory)
    {
        ClanWorkflowChecks.Run(sourceRoot, directory);
        var root = Path.Combine(Path.GetFullPath(directory), "clan-workflow"); var config = Path.Combine(root, "config");
        var guide = Path.Combine(config, "agent-guide-review.json"); var modePath = PcrClanDailyExecution.PathFor(config); var count = 0;
        void Check(bool pass, string label) { if (!pass) throw new InvalidOperationException("clan_formal_" + label); count++; }
        void Reject(Action action, string label, string? contains = null)
        {
            try { action(); } catch (Exception ex) when (contains == null || ex.GetBaseException().Message.Contains(contains)) { count++; return; }
            throw new InvalidOperationException("clan_formal_expected_rejection_" + label);
        }
        var view = new PcrClanGuideView(sourceRoot, guide, config);
        Check(view.ExecutionMode == PcrClanDailyExecution.Simulation && !File.Exists(modePath), "legacy_default_simulation_without_migration");
        Check(view.ExecutionConfirmationForStart == view.Capture().ConfirmedFor, "simulation_uses_only_current_team_confirmation");
        ModeSaveFailureChecks(sourceRoot, root, config, guide, Check);
        view.ChangeExecutionMode(PcrClanDailyExecution.FormalDaily);
        Check(view.ExecutionMode == PcrClanDailyExecution.FormalDaily && view.HasCurrentConfirmation && view.ExecutionConfirmationForStart == null, "single_confirmation_is_not_three_target_confirmation");
        Reject(() => PcrClanDailyExecution.CreatePlan(config, sourceRoot, DateTimeOffset.Now), "unconfirmed_formal_request");
        view.PreviewDaily(); Check(view.ExecutionSummaryText.Contains("第 1 刀") && view.ExecutionSummaryText.Contains("第 2 刀") && view.ExecutionSummaryText.Contains("第 3 刀")
            && view.ExecutionSummaryText.Contains("实际次数、日界与补偿未知"), "preview_exact_three_with_unknown_actual_state");
        view.ConfirmDaily(); var request = PcrClanDailyExecution.CreatePlan(config, sourceRoot, DateTimeOffset.Now);
        Check(view.ExecutionConfirmationForStart == (string?)request["confirmed_for"] && view.ExecutionConfirmationForStart != view.Capture().ConfirmedFor, "independent_batch_confirmation");
        Check(request.Properties().Select(p => p.Name).Order().SequenceEqual(new[] { "confirmed_for", "daily_plan", "slot_materials", "slot_plans", "version" }), "strict_daily_request_schema");
        Check(((JObject)request["slot_plans"]!).Count == 3 && ((JObject)request["slot_materials"]!).Count == 3 && (string?)request["confirmed_for"] == PcrClanGuide.Fingerprint(request), "complete_plans_materials_and_canonical_hash");
        var reopened = new PcrClanGuideView(sourceRoot, guide, config);
        Check(reopened.ExecutionMode == PcrClanDailyExecution.FormalDaily && reopened.ExecutionConfirmationForStart == (string?)request["confirmed_for"], "formal_mode_and_confirmation_reopen");
        var raw = PcrClanGuide.Read(guide); PcrClanGuide.Select(raw, raw.BundlePath, "BV1Rbh86XEsq-p2-t35-r1"); raw.Job["stars"]![0] = JValue.CreateNull();
        PcrClanGuide.Save(raw, guide, PcrClanGuide.Stamp(guide), config, sourceRoot, DateTimeOffset.Now, true);
        var dailyPath = PcrClanDailyPlan.PathFor(config); var daily = PcrClanDailyPlan.Read(dailyPath, out var dailyStamp);
        PcrClanDailyPlan.Assign(daily, 1, raw, guide, config, sourceRoot, DateTimeOffset.Now); PcrClanDailyPlan.Save(daily, dailyPath, dailyStamp);
        Check(reopened.ExecutionConfirmationForStart == null, "changed_three_target_fingerprint_invalidates_old_confirmation");
        reopened.Reload(); reopened.ConfirmDaily(); request = PcrClanDailyExecution.CreatePlan(config, sourceRoot, DateTimeOffset.Now);
        Check(request["slot_materials"]!["1"]!["stars"]![0]!.Type == JTokenType.Null && (int?)request["slot_plans"]!["1"]!["stars"]![0] == 5, "raw_material_preserves_null_resolved_by_snapshot");
        foreach (var slot in (JArray)request["daily_plan"]!["slots"]!)
        {
            var key = slot["slot"]!.ToString(); var material = request["slot_materials"]![key]!;
            Check((string?)slot["material_sha256"] == PcrClanGuide.Hash(Encoding.UTF8.GetBytes(PcrClanGuide.Canonical(material).ToString(Newtonsoft.Json.Formatting.None)))
                && (string?)slot["confirmed_for"] == (string?)request["slot_plans"]![key]!["confirmed_for"], "reference_material_and_plan_binding_" + key);
        }
        File.WriteAllText(Path.Combine(root, "daily-request.json"), request.ToString(), new UTF8Encoding(false));
        var legacyPath = Path.Combine(config, "guide-review.json"); var legacyBytes = File.ReadAllBytes(legacyPath);
        var invalidSnapshot = PcrClanGuide.ReadJson(raw.SnapshotPath); invalidSnapshot["content_version"] = "synthetic-invalid-version";
        var catalogStore = new PcrGuideStore(legacyPath); var legacyDocument = catalogStore.Load(); legacyDocument.Input.Catalog!.ContentVersion = "synthetic-invalid-version"; catalogStore.Save(legacyDocument);
        new PcrGuideAccountStore(config).Save(Encoding.UTF8.GetBytes(invalidSnapshot.ToString()), legacyDocument.Input.Catalog, "999000001", DateTimeOffset.Now, null);
        var invalidPath = PcrClanGuide.SnapshotPaths(config).Single(p => (string?)PcrClanGuide.ReadJson(p)["content_version"] == "synthetic-invalid-version");
        var invalidPlan = PcrClanGuide.Read(guide); invalidPlan.SnapshotPath = invalidPath; invalidPlan.SnapshotSha256 = PcrClanGuide.Stamp(invalidPath)!;
        try { Reject(() => PcrClanGuide.BuildPlan(invalidPlan, config, sourceRoot, DateTimeOffset.Now), "invalid_content_version_at_execution_boundary", "12 位数字"); }
        finally { File.WriteAllBytes(legacyPath, legacyBytes); File.Delete(invalidPath); }
        Check(PcrClanDailyExecution.CreatePlan(config, sourceRoot, DateTimeOffset.Now)["confirmed_for"]!.ToString() == request["confirmed_for"]!.ToString(), "display_catalog_restore_keeps_valid_batch");
        var stateBytes = File.ReadAllBytes(modePath); var state = PcrClanDailyExecution.Read(modePath, out var stateStamp);
        PcrClanDailyExecution.Save(new() { Mode = PcrClanDailyExecution.Simulation }, modePath, stateStamp); var newer = File.ReadAllBytes(modePath);
        Reject(() => PcrClanDailyExecution.Save(state, modePath, stateStamp), "concurrent_mode_save"); Check(File.ReadAllBytes(modePath).SequenceEqual(newer), "concurrent_mode_writer_preserved");
        File.WriteAllBytes(modePath, stateBytes); reopened.Reload();
        Reject(() => PcrClanDailyExecution.Save(new() { Mode = "unknown" }, modePath, PcrClanGuide.Stamp(modePath)), "unknown_mode");
        Reject(() => PcrClanDailyExecution.Save(new() { Mode = PcrClanDailyExecution.Simulation, ConfirmedFor = new string('a', 64) }, modePath, PcrClanGuide.Stamp(modePath)), "simulation_cannot_carry_batch_confirmation");
        File.WriteAllText(modePath, "{\"version\":1,\"mode\":true,\"confirmed_for\":null}");
        Reject(() => PcrClanDailyExecution.Read(modePath, out _), "malformed_persisted_mode"); File.WriteAllBytes(modePath, stateBytes);
        var nodes = new JObject();
        foreach (var node in new[] { "Schedule", "Event", "Abyss", "Deep", "Rewards", "VeryHard", "Hard", "Normal", "GuideTrial", "ClanBattleSimulation", "ClanBattleDaily", "RewardsFinal" })
            nodes["PCRAlphaSelect" + node] = new JObject { ["attach"] = new JObject { ["selected"] = node == "ClanBattleDaily" } };
        var selected = PcrAlphaOwner.Selection("PCRAlphaEntry", nodes.ToString());
        Check(selected["selected_modules"]!.Values<string>().SequenceEqual(new[] { "clan_battle_daily" }), "formal_daily_distinct_selection");
        foreach (var mixed in new[] { "Schedule", "Rewards", "GuideTrial", "ClanBattleSimulation", "RewardsFinal" })
        { nodes["PCRAlphaSelect" + mixed]!["attach"]!["selected"] = true; Reject(() => PcrAlphaOwner.Selection("PCRAlphaEntry", nodes.ToString()), "reject_mixed_" + mixed); nodes["PCRAlphaSelect" + mixed]!["attach"]!["selected"] = false; }
        const BindingFlags hidden = BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
        var configField = typeof(AppPaths).GetField("_configDirectory", hidden)!; var beforeConfig = configField.GetValue(null);
        var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        var vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        void Set(object target, string name, object value) => target.GetType().GetField(name, hidden)!.SetValue(target, value);
        Set(processor, "<InstanceId>k__BackingField", PcrClanGuide.ProfileId); Set(vm, "_processorField", processor);
        var ownerCounts = (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants, PcrAlphaOwner.SessionCreations);
        try
        {
            configField.SetValue(null, config);
            Check(!MaaProcessorManager.IsInstanceCreated, "fresh_process_has_no_manager");
            Reject(() => PcrAlphaOwner.RequestStartAsync(vm).GetAwaiter().GetResult(), "formal_start_rejected_before_owner", "production_daily_adapter_unavailable");
            Check(!MaaProcessorManager.IsInstanceCreated && typeof(PcrAlphaOwner).GetField("pendingStart", hidden)!.GetValue(null) == null
                && (int)typeof(PcrAlphaOwner).GetField("prompting", hidden)!.GetValue(null)! == 0, "no_manager_pending_owner_or_prompt_allocated");
            Check(ownerCounts == (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants, PcrAlphaOwner.SessionCreations), "no_controller_agent_grant_session_allocated");
            var player = (PcrPlayerSettings)RuntimeHelpers.GetUninitializedObject(typeof(PcrPlayerSettings)); Set(player, "vm", vm);
            Reject(() => player.ExportForStart(sourceRoot, selected), "export_rejects_before_device_access", "production_daily_adapter_unavailable");
            var requireProfile = typeof(PcrAlphaOwner).GetMethod("RequireProfileSelection", hidden)!;
            requireProfile.Invoke(null, new object[] { processor, selected }); count++;
            foreach (var id in new[] { "pcr-alpha-v1", "pcr-labyrinth-v1" })
            { Set(processor, "<InstanceId>k__BackingField", id); Reject(() => requireProfile.Invoke(null, new object[] { processor, selected }), "wrong_profile_" + id); }
            Set(processor, "<InstanceId>k__BackingField", PcrClanGuide.ProfileId);
            // An isolated manager supplies only the saved task model for the actual preview method.
            var manager = (MaaProcessorManager)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessorManager));
            Set(manager, "_viewModels", new Dictionary<string, TaskQueueViewModel> { [PcrClanGuide.ProfileId] = vm }); Set(manager, "_lock", new object());
            var lazy = (Lazy<MaaProcessorManager>)typeof(MaaProcessorManager).GetField("LazyInstance", hidden)!.GetValue(null)!;
            typeof(Lazy<MaaProcessorManager>).GetField("_factory", hidden)!.SetValue(lazy, new Func<MaaProcessorManager>(() => manager));
            var item = (DragItemViewModel)RuntimeHelpers.GetUninitializedObject(typeof(DragItemViewModel));
            Set(item, "_interfaceItem", new MaaInterface.MaaInterfaceTask { Name = "PCRAlpha", Entry = "PCRAlphaEntry", Repeatable = true, RepeatCount = 3,
                PipelineOverride = nodes.Properties().ToDictionary(p => p.Name, p => p.Value.DeepClone()), Option = new() });
            Set(vm, "_taskItemViewModels", new ObservableCollection<DragItemViewModel> { item }); Set(vm, "_currentResources", new ObservableCollection<MaaInterface.MaaInterfaceResource>());
            var preview = processor.PreviewPcrTask(); var previewSelection = PcrAlphaOwner.Selection(preview.Entry!, preview.Param!);
            Check(preview.Count == 1 && JToken.DeepEquals(previewSelection, selected), "actual_preview_is_one_formal_batch_not_three_repeats");
            // Preview initializes LoggerHelper/AppPaths; keep the real Toggle on this isolated configuration.
            configField.SetValue(null, config);
            Set(vm, "_pcrSettings", player);
            Set(manager, "_instanceNames", new Dictionary<string, string>());
            var logsField = typeof(MaaProcessor).GetField("<LogItemViewModels>k__BackingField", hidden)!;
            logsField.SetValue(processor, Activator.CreateInstance(logsField.FieldType));
            var cache = (ConcurrentDictionary<Type, Lazy<object>>)typeof(Instances).GetField("ServiceCache", hidden)!.GetValue(null)!;
            var guiType = typeof(GuiSettingsUserControlModel); var hadGui = cache.TryGetValue(guiType, out var previousGui);
            var gui = (GuiSettingsUserControlModel)RuntimeHelpers.GetUninitializedObject(guiType);
            Set(gui, "_baseTheme", ThemeVariant.Light); cache[guiType] = new Lazy<object>(() => gui);
            try
            {
                foreach (var scenario in new[] { "formal", "malformed" })
                {
                    if (scenario == "formal") File.WriteAllBytes(modePath, stateBytes);
                    else File.WriteAllText(modePath, "{\"version\":1,\"mode\":true,\"confirmed_for\":null}");
                    vm.PcrRunSummary = "上次模拟已完成。";
                    vm.PcrStartError = "上次启动错误";
                    var toggleInput = new JObject { ["scenario"] = scenario, ["can_execute"] = vm.ToggleCommand.CanExecute(null),
                        ["settings_only"] = PcrFormalPreflight.SettingsOnly, ["agent_profile"] = vm.IsAgentProfile, ["running"] = vm.IsRunning,
                        ["config_is_fixture"] = AppPaths.ConfigDirectory == config, ["mode"] = File.ReadAllText(modePath) };
                    vm.ToggleCommand.Execute(null);
                    Dispatcher.UIThread.RunJobs();
                    var expected = scenario == "formal" ? "production_daily_adapter_unavailable" : "会战模式配置格式无效";
                    Console.WriteLine("PCR clan Toggle feedback fixture: " + new JObject { ["input"] = toggleInput,
                        ["summary"] = vm.PcrRunSummary, ["error"] = vm.PcrStartError, ["status"] = vm.PcrRunStatus,
                        ["feedback_run"] = (long)typeof(TaskQueueViewModel).GetField("_pcrFeedbackRun", hidden)!.GetValue(vm)!,
                        ["disposed"] = (int)typeof(TaskQueueViewModel).GetField("_isDisposed", hidden)!.GetValue(vm)!,
                        ["log_count"] = processor.LogItemViewModels.Count }.ToString(Newtonsoft.Json.Formatting.None));
                    Check(!vm.PcrHasRunSummary && vm.PcrHasStartError && vm.PcrStartError.Contains(expected)
                        && vm.PcrRunStatus == vm.PcrStartError, scenario + "_toggle_clears_previous_summary_and_shows_current_error");
                    Check(processor.LogItemViewModels.Last().Content == vm.PcrStartError, scenario + "_toggle_log_matches_fixed_run_status");
                    Check(vm.Idle && typeof(PcrAlphaOwner).GetField("current", hidden)!.GetValue(null) == null
                        && typeof(PcrAlphaOwner).GetField("pendingStart", hidden)!.GetValue(null) == null
                        && (int)typeof(PcrAlphaOwner).GetField("prompting", hidden)!.GetValue(null)! == 0
                        && ownerCounts == (PcrAlphaOwner.ControllerCreations, PcrAlphaOwner.AgentCreations, PcrAlphaOwner.Grants, PcrAlphaOwner.SessionCreations),
                        scenario + "_toggle_rejects_before_owner_or_device_allocation");
                }
            }
            finally
            {
                File.WriteAllBytes(modePath, stateBytes);
                processor.LogItemViewModels.Clear();
                if (hadGui) cache[guiType] = previousGui!; else cache.TryRemove(guiType, out _);
            }
        }
        finally { configField.SetValue(null, beforeConfig); }
        reopened.ChangeExecutionMode(PcrClanDailyExecution.Simulation);
        Check(reopened.ExecutionConfirmationForStart == reopened.Capture().ConfirmedFor && PcrClanDailyExecution.Read(modePath, out _).ConfirmedFor == null, "return_to_simulation_clears_batch_confirmation");
        File.WriteAllText(Path.Combine(root, "formal-checks.json"), new JObject { ["checks"] = count, ["synthetic"] = true, ["mode_default"] = "simulation", ["formal_request_schema"] = new JArray(request.Properties().Select(p => p.Name)),
            ["formal_start_enabled"] = false, ["owner_created"] = false, ["game_input"] = false, ["daily_request_sha256"] = PcrClanGuide.Stamp(Path.Combine(root, "daily-request.json")) }.ToString(), new UTF8Encoding(false));
        Console.WriteLine($"PCR clan formal configuration checks passed: {count}; default simulation, three-target confirmation, formal Start before allocation blocked, game input=0");
    }

    private static void ModeSaveFailureChecks(string sourceRoot, string root, string config, string guide, Action<bool, string> check)
    {
        var isolated = Path.Combine(root, "mode-save-fault"); Directory.CreateDirectory(isolated);
        foreach (var path in Directory.EnumerateFiles(config, "*", SearchOption.AllDirectories))
        {
            var copy = Path.Combine(isolated, Path.GetRelativePath(config, path)); Directory.CreateDirectory(Path.GetDirectoryName(copy)!); File.Copy(path, copy);
        }
        var copiedGuide = Path.Combine(isolated, Path.GetFileName(guide)); var modePath = PcrClanDailyExecution.PathFor(isolated);
        var view = new PcrClanGuideView(sourceRoot, copiedGuide, isolated); var document = PcrClanGuide.Read(copiedGuide);
        document.SnapshotPath = Path.Combine(isolated, Path.GetRelativePath(config, document.SnapshotPath)); view.Display(document); view.ConfirmAndSave();
        var draft = view.Capture().Job.ToString(); var guideBytes = File.ReadAllBytes(copiedGuide); var events = 0;
        ComboBox Selector(PcrClanGuideView target) => (ComboBox)typeof(PcrClanGuideView).GetField("executionMode", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(target)!;
        void Select(int index) { Selector(view).SelectedIndex = index; Dispatcher.UIThread.RunJobs(); }
        void Unavailable(string label) => check(view.ExecutionMode == "unavailable" && Selector(view).SelectedItem == null
            && view.ExecutionConfirmationForStart == null && view.ExecutionSummaryText.Contains("重新打开"), label);
        view.ExecutionModeChanged += (_, _) => events++;
        PcrClanDailyExecution.Save(new() { Mode = PcrClanDailyExecution.FormalDaily }, modePath, null); var newer = File.ReadAllBytes(modePath);
        Select(1); Unavailable("concurrent_mode_event_revokes_visible_mode_and_start_confirmation");
        check(events == 1 && view.StatusText.Contains("其他窗口修改") && File.ReadAllBytes(modePath).SequenceEqual(newer), "concurrent_writer_preserved_and_owner_notified_once");
        Select(0); Unavailable("reselection_after_failed_save_stays_unavailable");
        check(events == 2 && view.Capture().Job.ToString() == draft && File.ReadAllBytes(copiedGuide).SequenceEqual(guideBytes), "failed_reselection_preserves_draft_and_guide");
        view.Reload(); check(view.ExecutionMode == PcrClanDailyExecution.FormalDaily && Selector(view).SelectedIndex == 1 && view.ExecutionConfirmationForStart == null, "explicit_reload_reads_external_mode_without_confirmation");
        Select(0); check(view.ExecutionMode == PcrClanDailyExecution.Simulation && Selector(view).SelectedIndex == 0 && view.ExecutionConfirmationForStart == view.Capture().ConfirmedFor, "successful_control_event_restores_simulation_confirmation");
        var reopened = new PcrClanGuideView(sourceRoot, copiedGuide, isolated);
        check(reopened.ExecutionMode == PcrClanDailyExecution.Simulation && Selector(reopened).SelectedIndex == 0 && reopened.ExecutionConfirmationForStart == reopened.Capture().ConfirmedFor, "successful_mode_event_reopens_consistently");
        var beforeIo = File.ReadAllBytes(modePath); var beforeEvents = events;
        using (var held = new FileStream(modePath, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            Select(1); Unavailable("io_mode_event_revokes_start_confirmation");
            check(events == beforeEvents + 1 && view.StatusText.Contains("会战模式未完成"), "io_failure_notifies_original_owner_event");
        }
        check(File.ReadAllBytes(modePath).SequenceEqual(beforeIo), "io_failure_keeps_actual_mode_file");
        view.Reload(); check(view.ExecutionConfirmationForStart == view.Capture().ConfirmedFor && Selector(view).SelectedIndex == 0, "io_failure_recovers_only_after_reload");
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("The mode post-save ACL fault requires Windows.");
        File.Delete(modePath); File.Delete(modePath + ".lock"); view.Reload();
        var info = new DirectoryInfo(isolated); var originalSddl = info.GetAccessControl(AccessControlSections.Access).GetSecurityDescriptorSddlForm(AccessControlSections.Access);
        var sid = WindowsIdentity.GetCurrent().User ?? throw new InvalidOperationException("Current Windows SID unavailable.");
        var oldFiles = Directory.EnumerateFiles(isolated).ToHashSet(StringComparer.OrdinalIgnoreCase); var readDenied = false;
        try
        {
            var acl = info.GetAccessControl(AccessControlSections.Access);
            acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.ReadData, InheritanceFlags.ObjectInherit, PropagationFlags.InheritOnly, AccessControlType.Deny)); info.SetAccessControl(acl);
            Select(1);
            check(File.Exists(modePath), "postsave_mode_written_before_read_failure: " + view.StatusText);
            try { File.ReadAllBytes(modePath); } catch (UnauthorizedAccessException) { readDenied = true; }
            check(readDenied, "postsave_mode_read_actually_denied");
            Unavailable("postsave_mode_event_revokes_old_confirmation");
        }
        finally
        {
            var restore = new DirectorySecurity(); restore.SetSecurityDescriptorSddlForm(originalSddl, AccessControlSections.Access); info.SetAccessControl(restore);
            foreach (var path in Directory.EnumerateFiles(isolated).Where(p => !oldFiles.Contains(p)))
            {
                var file = new FileInfo(path); var acl = file.GetAccessControl(AccessControlSections.Access); acl.SetAccessRuleProtection(true, false);
                acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, AccessControlType.Allow)); file.SetAccessControl(acl);
            }
        }
        check(info.GetAccessControl(AccessControlSections.Access).GetSecurityDescriptorSddlForm(AccessControlSections.Access) == originalSddl, "postsave_fixture_acl_restored");
        check(PcrClanDailyExecution.Read(modePath, out _).Mode == PcrClanDailyExecution.FormalDaily && File.ReadAllBytes(copiedGuide).SequenceEqual(guideBytes), "postsave_actual_write_and_original_guide_preserved");
        view.Reload(); check(view.ExecutionMode == PcrClanDailyExecution.FormalDaily && Selector(view).SelectedIndex == 1 && view.ExecutionConfirmationForStart == null, "postsave_reload_reads_saved_mode_without_old_confirmation");
        Select(0); check(view.ExecutionConfirmationForStart == view.Capture().ConfirmedFor && view.Capture().Job.ToString() == draft, "postsave_explicit_mode_selection_preserves_team");
    }
}
