using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Xml.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Configuration;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;

internal static class SettingsUxChecks
{
    internal static void Run(string sourceRoot, string fixtureRoot,
        Func<InstanceConfiguration, TaskQueueViewModel> owner, Action<bool, string> require)
    {
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

        const string id = "pcr-settings-ux";
        var config = new InstanceConfiguration(id);
        var vm = owner(config);
        var settings = vm.LabyrinthSettings;
        require(settings.AccountSummary.Contains("待填写") && !settings.Status.StartsWith("已自动保存"),
            "settings_empty_account_and_unsaved_initial_state_are_explicit");
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

        PcrLabyrinthCharacterCatalog.UseRepositoryRoot(sourceRoot);
        var view = new PcrLabyrinthView { DataContext = vm, Background = Brushes.White };
        var window = new Window { Content = view, Width = 960, Height = 900, ShowActivated = false,
            ShowInTaskbar = false, Position = new PixelPoint(-12000, -12000) };
        try
        {
            window.Show();
            Dispatcher.UIThread.RunJobs();
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
