using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Xml.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.LogicalTree;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Configuration;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json.Linq;

internal static class SettingsUxChecks
{
    internal static void Run(string sourceRoot, string fixtureRoot,
        Func<InstanceConfiguration, TaskQueueViewModel> owner, Action<bool, string> require)
    {
        PcrLabyrinthEventCatalog.UseRepositoryRoot(sourceRoot);
        var markup = XDocument.Load(Path.Combine(sourceRoot, "gui", "MFAAvalonia", "MFAAvalonia", "Pcr", "PcrLabyrinthView.axaml"));
        var nodes = markup.Descendants().ToArray();
        XElement Named(string name) => nodes.Single(node => (string?)node.Attribute("Name") == name);
        require(Array.IndexOf(nodes, Named("AccountSettings")) < Array.IndexOf(nodes, Named("GoalsInput")),
            "settings_account_entry_precedes_task_goals");
        require(Named("MaxAttemptsInput").Ancestors().Contains(Named("RerollSettings"))
            && Named("MaxMinutesInput").Ancestors().Contains(Named("RerollSettings")), "settings_reroll_limits_are_in_advanced_section");
        require(nodes.Count(node => node.Name.LocalName == "PcrCountInput") == 4
            && nodes.Where(node => node.Name.LocalName == "PcrCountInput")
                .All(node => (string?)node.Attribute("ClipValueToMinMax") == "False"), "settings_numeric_inputs_preserve_invalid_drafts");
        require((string?)Named("EventSettings").Attribute("Header") == "事件策略"
            && (string?)Named("RelicSettings").Attribute("Header") == "遗物与商店策略", "settings_event_strategy_is_independent_section");
        require(new[] { "NormalWinRateInput", "ExtremeWinRateInput" }.All(name => Named(name).Ancestors().Contains(Named("EventSettings")))
            && !new[] { "NormalWinRateInput", "ExtremeWinRateInput" }.Any(name => Named(name).Ancestors().Contains(Named("RelicSettings"))),
            "settings_win_rates_live_in_event_section");
        require(Named("EventChainPolicySelector").Ancestors().Contains(Named("EventSettings")),
            "settings_event_chain_policy_is_in_event_section");
        require(Named("EventChoicesInput").Ancestors().Contains(Named("EventSettings")),
            "settings_event_choice_list_is_in_event_section");
        var relicPriorityNodes = nodes.Where(node => node.Name.LocalName == "PcrMarkPriorityList").ToArray();
        require(relicPriorityNodes.Length == 1 && (string?)relicPriorityNodes[0].Attribute("Name") == "RelicPriorityInput"
            && relicPriorityNodes[0].Ancestors().Contains(Named("RelicSettings")),
            "settings_relic_priority_blocks_live_in_relic_section");
        require(!nodes.Where(node => node.Name.LocalName == "TextBox")
                .Any(node => ((string?)node.Attribute("Text") ?? "").Contains("RelicMarkPriority", StringComparison.Ordinal))
            && !nodes.Any(node => (string?)node.Attribute("Watermark") == "加速,会心,守备,强化,弱体"),
            "settings_relic_priority_free_text_entry_removed");
        require(nodes.Any(node => node.Name.LocalName == "TextBlock" && (string?)node.Attribute("Text") == "遗物印记优先级"),
            "settings_relic_priority_title");

        const string id = "pcr-settings-ux";
        var config = new InstanceConfiguration(id);
        var vm = owner(config);
        var settings = vm.LabyrinthSettings;
        require(settings.AccountSummary.Contains("待填写") && !settings.Status.StartsWith("已自动保存"),
            "settings_empty_account_and_unsaved_initial_state_are_explicit");
        require(settings.Reroll && (bool)settings.Snapshot()["reroll"]!, "settings_fresh_reroll_defaults_on");
        var legacyConfig = new InstanceConfiguration("pcr-settings-legacy-no-reroll");
        legacyConfig.SetValueAndPersist(PcrLabyrinthSettings.Key,
            new JObject { ["version"] = 1, ["goals"] = new JArray(new JObject { ["guild_id"] = 1, ["clears"] = 1 }) }
                .ToString(Newtonsoft.Json.Formatting.None));
        require(owner(legacyConfig).LabyrinthSettings.Reroll, "settings_missing_reroll_defaults_on");
        settings.Difficulty = "3";
        require(settings.Status == "已自动保存黎明界设置。" && settings.SaveButtonText == "重新保存",
            "settings_automatic_save_is_visible");
        var path = config.GetConfigFilePath();
        var before = File.ReadAllBytes(path);
        using (var locked = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            settings.Difficulty = "4";
            require(settings.Status.StartsWith("保存失败：") && settings.SaveButtonText == "重试保存", "settings_failed_save_offers_retry");
            require(File.ReadAllBytes(path).SequenceEqual(before)
                && owner(new InstanceConfiguration(id)).LabyrinthSettings.Difficulty == "3", "settings_failed_save_keeps_previous_value");
        }
        settings.SaveCommand.Execute(null);
        require(settings.Status == "黎明界设置已保存。" && settings.SaveButtonText == "重新保存"
            && owner(new InstanceConfiguration(id)).LabyrinthSettings.Difficulty == "4", "settings_manual_retry_persists_and_reopens");
        using (var locked = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            settings.Username = "fixture-ui-save-retry";
            settings.SaveCommand.Execute(null);
            require(settings.SaveButtonText == "重试保存" && settings.AccountSummary.Contains("未保存")
                && settings.Status.Contains("账号尚未保存"), "settings_credential_failure_is_not_hidden_by_unchanged_task");
        }
        settings.SaveCommand.Execute(null);
        require(settings.SaveButtonText == "重新保存" && !settings.AccountSummary.Contains("未保存")
            && owner(new InstanceConfiguration(id)).LabyrinthSettings.Username == "fixture-ui-save-retry", "settings_credential_retry_clears_failure_and_reopens");
        require(settings.EventChainPolicy == settings.EventChainPolicies[0], "settings_event_chain_policy_defaults_auto");
        settings.EventChainPolicy = settings.EventChainPolicies[1];
        require(settings.Snapshot()["strategy"]?["event_chain_policy"]?.ToString() == "continue"
            && owner(new InstanceConfiguration(id)).LabyrinthSettings.EventChainPolicy == settings.EventChainPolicies[1],
            "settings_event_chain_policy_persists_continue");
        var singleOptionEvents = new[] { "1314", "1315", "1316", "2314", "3314" };
        require(settings.EventChoiceItems.Count == 17
            && settings.EventChoiceItems.All(item => item.Choices.Length >= 3)
            && !settings.EventChoiceItems.Any(item => singleOptionEvents.Contains(item.EventId)),
            "settings_event_choice_catalogue_lists_only_multi_option_events");
        var battleEvent = settings.EventChoiceItems.Single(item => item.EventId == "1105");
        require(battleEvent.Choices.Length == 3 && battleEvent.Selected == battleEvent.Choices[0],
            "settings_event_choice_defaults_auto");
        battleEvent.Selected = battleEvent.Choices[2];
        require((settings.Snapshot()["strategy"]?["event_choice_overrides"] as JObject)?["1105"]?.Value<int>() == 11052,
            "settings_event_choice_override_persists");
        require(owner(new InstanceConfiguration(id)).LabyrinthSettings
                .EventChoiceItems.Single(item => item.EventId == "1105").Selected == battleEvent.Choices[2],
            "settings_event_choice_override_reopens");
        battleEvent.Selected = battleEvent.Choices[0];
        require(((settings.Snapshot()["strategy"]?["event_choice_overrides"]) as JObject)?.ContainsKey("1105") == false,
            "settings_event_choice_override_can_be_cleared");

        const string legacyPriorityId = "pcr-settings-legacy-relic-priority";
        var legacyPriorityConfig = new InstanceConfiguration(legacyPriorityId);
        legacyPriorityConfig.SetValueAndPersist(PcrLabyrinthSettings.Key,
            new JObject
            {
                ["version"] = 1,
                ["goals"] = new JArray(new JObject { ["guild_id"] = 1, ["clears"] = 1 }),
                ["strategy"] = new JObject { ["relic_mark_priority"] = new JArray("会心", "加速") },
            }.ToString(Newtonsoft.Json.Formatting.None));
        var migrated = owner(legacyPriorityConfig).LabyrinthSettings;
        require(migrated.RelicMarkPriority == "会心,加速,守备,强化,弱体" && migrated.HasRelicPriorityNotice
            && migrated.RelicPriorityNotice.Contains("会心，加速", StringComparison.Ordinal),
            "settings_legacy_relic_subset_expands_with_notice");
        var reopened = owner(new InstanceConfiguration(legacyPriorityId)).LabyrinthSettings;
        require(reopened.RelicMarkPriority == "会心,加速,守备,强化,弱体" && !reopened.HasRelicPriorityNotice,
            "settings_legacy_relic_subset_migrates_once");

        PcrLabyrinthCharacterCatalog.UseRepositoryRoot(sourceRoot);
        var view = new PcrLabyrinthView { DataContext = vm, Background = Brushes.White };
        var window = new Window { Content = view, Width = 960, Height = 900, ShowActivated = false,
            ShowInTaskbar = false, Position = new PixelPoint(-12000, -12000) };
        try
        {
            window.Show();
            Dispatcher.UIThread.RunJobs();
            var reroll = view.GetLogicalDescendants().OfType<CheckBox>()
                .Single(control => (control.Content as string)?.StartsWith("启用刷开局") == true);
            require(reroll.IsChecked == true, "settings_reroll_checkbox_initially_checked");
            reroll.SetCurrentValue(CheckBox.IsCheckedProperty, false);
            Dispatcher.UIThread.RunJobs();
            require(!settings.Reroll && !(bool)settings.Snapshot()["reroll"]!
                && !owner(new InstanceConfiguration(id)).LabyrinthSettings.Reroll,
                "settings_explicit_reroll_off_persists_and_reopens");
            reroll.SetCurrentValue(CheckBox.IsCheckedProperty, true);
            Dispatcher.UIThread.RunJobs();
            require(settings.Reroll && owner(new InstanceConfiguration(id)).LabyrinthSettings.Reroll,
                "settings_reroll_can_be_reenabled_and_saved");
            var priority = view.FindControl<PcrMarkPriorityList>("RelicPriorityInput")!;
            var priorityToggle = priority.FindControl<CheckBox>("EnableToggle")!;
            JArray PriorityArray() => (JArray)settings.Snapshot()["strategy"]!["relic_mark_priority"]!;
            require(priorityToggle.IsChecked == false && PriorityArray().Count == 0,
                "settings_relic_priority_starts_builtin");
            priorityToggle.SetCurrentValue(CheckBox.IsCheckedProperty, true);
            Dispatcher.UIThread.RunJobs();
            require(settings.RelicMarkPriority == "加速,会心,守备,强化,弱体"
                && PriorityArray().Select(token => (string)token!).SequenceEqual(PcrMarkPriorityList.Marks),
                "settings_relic_priority_enable_writes_five");
            require(owner(new InstanceConfiguration(id)).LabyrinthSettings.RelicMarkPriority == "加速,会心,守备,强化,弱体",
                "settings_relic_priority_enable_persists");
            priority.Move(0, 2);
            Dispatcher.UIThread.RunJobs();
            require(settings.RelicMarkPriority == "会心,守备,加速,强化,弱体"
                && PriorityArray().Select(token => (string)token!).SequenceEqual(new[] { "会心", "守备", "加速", "强化", "弱体" })
                && owner(new InstanceConfiguration(id)).LabyrinthSettings.RelicMarkPriority == "会心,守备,加速,强化,弱体",
                "settings_relic_priority_reorder_persists");
            priorityToggle.SetCurrentValue(CheckBox.IsCheckedProperty, false);
            Dispatcher.UIThread.RunJobs();
            require(priorityToggle.IsChecked == false && PriorityArray().Count == 0 && settings.RelicMarkPriority == ""
                && owner(new InstanceConfiguration(id)).LabyrinthSettings.RelicMarkPriority == "",
                "settings_relic_priority_disable_clears");
            priorityToggle.SetCurrentValue(CheckBox.IsCheckedProperty, true);
            Dispatcher.UIThread.RunJobs();
            var relicSettings = view.FindControl<Expander>("RelicSettings")!;
            relicSettings.IsExpanded = true;
            Dispatcher.UIThread.RunJobs();
            Thread.Sleep(500);
            Dispatcher.UIThread.RunJobs();
            view.UpdateLayout();
            var chipTops = priority.GetVisualDescendants().OfType<Border>()
                .Where(chip => chip.Classes.Contains("MarkChip"))
                .Select(chip => Math.Round(chip.TranslatePoint(new Point(0, 0), priority)?.Y ?? double.NaN, 1))
                .Distinct().ToArray();
            require(chipTops.Length == 1 && !double.IsNaN(chipTops[0]), "settings_relic_priority_blocks_share_one_row");
            var priorityOutput = Path.Combine(fixtureRoot, "settings-ux");
            Directory.CreateDirectory(priorityOutput);
            var settingsScroll = view.FindControl<ScrollViewer>("SettingsScroll")!;
            if (priority.TranslatePoint(new Point(0, 0), (Visual)settingsScroll.Content!) is { } priorityTop)
                settingsScroll.Offset = new Vector(0, Math.Max(0, priorityTop.Y - 180));
            Dispatcher.UIThread.RunJobs();
            view.UpdateLayout();
            require(settingsScroll.Offset.Y > 0, "settings_relic_priority_frame_scrolled");
            using (var pageFrame = new RenderTargetBitmap(new PixelSize(960, 900)))
            {
                pageFrame.Render(view);
                pageFrame.Save(Path.Combine(priorityOutput, "labyrinth-relic-priority.png"));
            }
            var input = view.FindControl<PcrCountInput>("DifficultyInput")!;
            var blur = typeof(PcrCountInput).GetMethod("OnLostFocus", BindingFlags.Instance | BindingFlags.NonPublic)!;
            foreach (var draft in new[] { "", "invalid", "8" })
            {
                input.SetCurrentValue(NumericUpDown.TextProperty, draft);
                Dispatcher.UIThread.RunJobs();
                blur.Invoke(input, new object[] { new RoutedEventArgs(InputElement.LostFocusEvent) });
                Dispatcher.UIThread.RunJobs();
                require(input.Text == draft && input.DraftText == draft && settings.Difficulty == draft,
                    "settings_numeric_draft_survives_blur_" + draft);
            }
            input.SetCurrentValue(NumericUpDown.TextProperty, "2");
            Dispatcher.UIThread.RunJobs();
            require(settings.Difficulty == "2" && owner(new InstanceConfiguration(id)).LabyrinthSettings.Difficulty == "2",
                "settings_valid_numeric_edit_is_saved");
            var saved = File.ReadAllBytes(path);
            view.RevealValidationField("本次未能开始：请填写游戏玩家 ID（个人简介，非B站 UID），必须为正整数。");
            Dispatcher.UIThread.RunJobs();
            require(view.FindControl<Expander>("AccountSettings")!.IsExpanded
                && view.FindControl<TextBox>("ExpectedPlayerIdInput")!.PasswordChar == '*', "settings_error_reveals_account_without_unmasking");
            view.FindControl<Expander>("AccountSettings")!.IsExpanded = false;
            view.RevealValidationField("本次未能开始：刷取分钟请输入 1–30 的整数。");
            Dispatcher.UIThread.RunJobs();
            require(view.FindControl<Expander>("RerollSettings")!.IsExpanded
                && File.ReadAllBytes(path).SequenceEqual(saved), "settings_error_navigation_does_not_revalidate_or_save");
            view.FindControl<Expander>("RerollSettings")!.IsExpanded = false;
            view.RevealValidationField("本次未能开始：普通战胜率请输入 0–100 的整数。");
            Dispatcher.UIThread.RunJobs();
            require(view.FindControl<Expander>("EventSettings")!.IsExpanded, "settings_win_rate_error_reveals_event_section");
            view.FindControl<Expander>("EventSettings")!.IsExpanded = false;
            view.RevealValidationField("本次未能开始：连续事件策略无效。");
            Dispatcher.UIThread.RunJobs();
            require(view.FindControl<Expander>("EventSettings")!.IsExpanded, "settings_chain_policy_error_reveals_event_section");
            view.FindControl<Expander>("EventSettings")!.IsExpanded = false;
            view.FindControl<ScrollViewer>("SettingsScroll")!.Offset = default;
            Dispatcher.UIThread.RunJobs();
            view.UpdateLayout();
            var output = Path.Combine(fixtureRoot, "settings-ux");
            Directory.CreateDirectory(output);
            using var frame = new RenderTargetBitmap(new PixelSize(960, 900));
            frame.Render(view);
            frame.Save(Path.Combine(output, "labyrinth-basic.png"));
        }
        finally { window.Close(); Dispatcher.UIThread.RunJobs(); }
    }
}
