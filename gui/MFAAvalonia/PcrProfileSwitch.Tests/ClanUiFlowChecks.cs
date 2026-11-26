using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.LogicalTree;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class ClanUiFlowChecks
{
    public static void Run(string directory)
    {
        var root = Path.GetFullPath(Path.Combine(directory, "clan-ui-flow", Guid.NewGuid().ToString("N")));
        var config = Path.Combine(root, "config"); Directory.CreateDirectory(config);
        var state = Path.Combine(config, "agent-guide-review.json");
        var modePath = PcrClanDailyExecution.PathFor(config);
        var count = 0;
        void Check(bool value, string name) { if (!value) throw new InvalidOperationException("clan_ui_flow_" + name); count++; }
        var view = new PcrClanGuideView(root, state, config);
        var selector = Field<ComboBox>(view, "executionMode");
        var formal = Field<StackPanel>(view, "formalControls");
        Check(view.ExecutionMode == PcrClanDailyExecution.Simulation && !formal.IsVisible && !File.Exists(modePath),
            "first_open_defaults_to_simulation_without_writing_mode");
        Check(view.ExecutionConfirmationForStart == null && view.StartHint == "模拟当前队伍一局", "empty_selection_describes_simulation_without_confirmation");
        var selectionSummary = Field<TextBlock>(view, "selectionSummary");
        var fields = Field<Dictionary<string, TextBox>>(view, "fields");
        Check(selectionSummary.Text == "尚未选择作业。", "empty_job_shows_single_prompt");
        fields["boss"].Text = "4";
        Check(selectionSummary.Text!.Contains("BOSS 4"), "partial_job_shows_entered_target");
        fields["boss"].Text = "";
        Check(selectionSummary.Text == "尚未选择作业。", "clearing_last_field_restores_empty_prompt");
        var calibration = Field<TextBlock>(view, "calibrationSummary").Text ?? "";
        Check(new[] { "模拟战", "不可用", "缺失" }.All(calibration.Contains)
            && !calibration.Contains("正式三刀") && !calibration.Contains("补偿刀"),
            "missing_bundle_shows_current_mode_reason_only");
        Check(Field<TextBlock>(view, "calibrationDetails").Text!.Contains("补偿刀"), "other_capabilities_remain_in_details");
        Check(view.ExecutionSummaryText.Contains("已击败的 BOSS 仍可选择模拟战"), "simulation_explains_defeated_boss_availability");

        selector.SelectedIndex = 1;
        Check(view.ExecutionMode == PcrClanDailyExecution.FormalDaily && formal.IsVisible,
            "actual_selector_reveals_formal_review_controls");
        Check(view.StartHint.StartsWith("准备正式三刀") && view.StartHint.Contains("另行确认") && view.ExecutionSummaryText.Contains(view.StartHint)
            && (Field<TextBlock>(view, "calibrationSummary").Text ?? "").Contains("不可用"),
            "formal_mode_does_not_claim_capability_from_mode_selection");
        Check(PcrClanDailyExecution.Read(modePath, out _).Mode == PcrClanDailyExecution.FormalDaily
            && view.BattleSpeed == 2 && view.ExecutionConfirmationForStart == null, "formal_selection_saves_fixed_two_without_start_confirmation");
        Check(!Field<ComboBox>(view, "battleSpeed").IsVisible && Field<TextBlock>(view, "formalSpeed").IsVisible,
            "formal_speed_is_fixed_instead_of_an_editable_selector");
        var reopened = new PcrClanGuideView(root, state, config);
        Check(reopened.ExecutionMode == PcrClanDailyExecution.FormalDaily && Field<StackPanel>(reopened, "formalControls").IsVisible,
            "reopen_displays_saved_formal_mode");
        selector.SelectedIndex = 0;
        Check(!formal.IsVisible && view.StartHint == "模拟当前队伍一局" && PcrClanDailyExecution.Read(modePath, out _).Mode == PcrClanDailyExecution.Simulation,
            "simulation_hides_formal_controls_and_persists");

        // A failed mode save must not leave the newly selected mode presented as saved.
        PcrClanDailyExecution.Save(new() { Mode = PcrClanDailyExecution.FormalDaily }, modePath, PcrClanGuide.Stamp(modePath));
        var externalMode = File.ReadAllBytes(modePath);
        selector.SelectedIndex = 1;
        Check(view.ExecutionMode == "unavailable" && view.StartHint.Contains("尚未确认") && !formal.IsVisible && view.ExecutionConfirmationForStart == null,
            "conflicting_save_cancels_visible_mode_and_confirmation");
        Check(File.ReadAllBytes(modePath).SequenceEqual(externalMode) && view.StatusText.Contains("重新打开"),
            "conflicting_save_keeps_file_and_explains_retry");
        view.Reload();
        selector.SelectedIndex = 0;
        Check(view.ExecutionMode == PcrClanDailyExecution.Simulation && !formal.IsVisible, "reopen_then_retry_recovers_mode");

        var speedSelector = Field<ComboBox>(view, "battleSpeed");
        var speedBeforeReload = view.BattleSpeed;
        var modeBeforeReload = File.ReadAllBytes(modePath);
        var reload = view.ReloadAsync();
        Check(!selector.IsEnabled && !speedSelector.IsEnabled, "reload_disables_mode_and_speed_until_actual_readback");
        var modeRejected = false; var speedRejected = false;
        try { view.ChangeExecutionMode(PcrClanDailyExecution.FormalDaily); } catch (InvalidOperationException) { modeRejected = true; }
        try { view.ChangeBattleSpeed(speedBeforeReload == 2 ? 1 : 2); } catch (InvalidOperationException) { speedRejected = true; }
        Check(modeRejected && speedRejected && File.ReadAllBytes(modePath).SequenceEqual(modeBeforeReload),
            "reload_rejects_interleaved_execution_writes_without_changing_disk");
        ClanGuideChecks.CompleteLoading(view);
        Check(reload.IsCompleted && selector.IsEnabled && speedSelector.IsEnabled
            && view.ExecutionMode == PcrClanDailyExecution.Simulation && view.BattleSpeed == speedBeforeReload
            && PcrClanDailyExecution.Read(modePath, out _).BattleSpeed == speedBeforeReload,
            "reload_restores_enabled_controls_with_ui_matching_disk");

        var refreshRequests = 0;
        view.AccountRefreshRequested += (_, _) => refreshRequests++;
        var beforeRefresh = Files(root);
        Button(view, "账号资料与刷新").RaiseEvent(new RoutedEventArgs(Avalonia.Controls.Button.ClickEvent));
        Check(refreshRequests == 1 && Files(root).SequenceEqual(beforeRefresh), "refresh_entry_only_requests_existing_page");
        var reads = 0;
        var accountPage = new PcrGuideReviewView(Path.Combine(config, "guide-review.json"), (_, _) =>
        {
            reads++; return Task.FromResult(Array.Empty<byte>());
        });
        accountPage.ShowAccountPage();
        Check(Field<TabControl>(accountPage, "tabs").SelectedIndex == 1 && reads == 0
            && !accountPage.GetLogicalDescendants().OfType<TabControl>().Any()
            && !Field<StackPanel>(accountPage, "guideActions").IsVisible,
            "account_navigation_hides_guide_editor_without_protocol_read");
        Check(!accountPage.GetLogicalDescendants().OfType<Expander>().Single(x => (string?)x.Header == "高级资料").IsExpanded
            && !Field<Button>(accountPage, "stopAccount").IsVisible, "account_secondary_actions_start_collapsed");
        accountPage.ShowGuidePage();
        Check(accountPage.GetLogicalDescendants().OfType<TabControl>().Count() == 1
            && Field<StackPanel>(accountPage, "guideActions").IsVisible && reads == 0,
            "return_to_guide_keeps_original_editor_without_login");
        Check(Files(root).SequenceEqual(beforeRefresh), "account_navigation_does_not_save_or_duplicate_cache");

        var imagePath = Path.Combine(root, "synthetic-source.png");
        using (var bitmap = new RenderTargetBitmap(new PixelSize(8, 8), new Vector(96, 96))) bitmap.Save(imagePath);
        var imageBytes = File.ReadAllBytes(imagePath);
        view.Display(new PcrClanGuideDocument { Job = new JObject
        {
            ["period"] = "2026-10", ["phase"] = "D", ["boss"] = 2, ["team_label"] = "合成队伍",
            ["switches"] = new JArray(), ["image_base64"] = Convert.ToBase64String(imageBytes),
            ["source"] = new JObject { ["image_path"] = imagePath, ["image_sha256"] = PcrClanGuide.Hash(imageBytes) }
        } });
        var source = Field<Expander>(view, "sourceDetails");
        var image = Field<Image>(view, "image"); var preview = Field<Image>(view, "preview");
        Check(!source.IsExpanded && image.Source == null && preview.Source == null,
            "display_keeps_source_collapsed_without_image_allocation");
        source.IsExpanded = true; ClanGuideChecks.CompleteLoading(view);
        Check(image.Source != null && preview.Source != null, "expanding_source_decodes_requested_images");
        source.IsExpanded = false;
        Check(image.Source == null && preview.Source == null, "collapsing_source_releases_images");
        fields["boss"].Text = "4"; fields["team_label"].Text = "修改后的队伍";
        var summary = selectionSummary.Text ?? "";
        Check(summary.Contains("BOSS 4") && summary.Contains("修改后的队伍"), "editing_target_updates_first_screen_summary");

        var flow = (StackPanel)((ScrollViewer)view.Content!).Content!;
        var save = (WrapPanel)Button(view, "核对五人和操作并保存").Parent!;
        var roster = flow.Children.OfType<Grid>().Single();
        var reference = flow.Children.OfType<Expander>().Single(item => (string?)item.Header == "作业名称与参考伤害");
        var daily = Field<Expander>(view, "dailyDetails");
        Check(flow.Children.IndexOf(roster) < flow.Children.IndexOf(save)
            && flow.Children.IndexOf(save) < flow.Children.IndexOf(reference)
            && flow.Children.IndexOf(save) < flow.Children.IndexOf(daily), "save_follows_roster_before_optional_details");
        Check(!reference.IsExpanded && !daily.IsExpanded, "optional_details_start_collapsed");
        Dispatcher.UIThread.RunJobs();
        Console.WriteLine($"clan_ui_flow_checks={count}");
    }

    private static T Field<T>(object view, string name) => (T)view.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(view)!;
    private static Button Button(Control view, string label) => view.GetLogicalDescendants().OfType<Button>().Single(button => (string?)button.Content == label);
    private static string[] Files(string root) => Directory.EnumerateFileSystemEntries(root, "*", SearchOption.AllDirectories)
        .OrderBy(path => path, StringComparer.Ordinal).Select(path => path + ":" + (File.Exists(path) ? PcrClanGuide.Hash(File.ReadAllBytes(path)) : "directory")).ToArray();
}
