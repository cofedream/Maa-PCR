using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Reflection;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Automation;
using Avalonia.LogicalTree;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using Avalonia.VisualTree;
using MFAAvalonia.Pcr;

internal static class GuideReviewChecks
{
    public static void Run(string directory)
    {
        var root = Path.Combine(directory, "guide-review"); Directory.CreateDirectory(root);
        var now = DateTimeOffset.Now; var date = now.ToString("yyyy-MM-dd");
        var count = 0;
        void Require(bool value, string name) { if (!value) throw new Exception("guide_review_" + name); count++; }
        void Throws(Action action, string name) { try { action(); } catch { count++; return; } throw new Exception("guide_review_expected_rejection_" + name); }
        PcrGuideInput Input() => new()
        {
            Title = "合成验证，非真实作业", SourceUrl = "https://example.com/synthetic-guide",
            RetrievedOn = date, ValidThrough = date, SourceConfirmed = true,
            Material = "测试角色原版，持有，星级恰为3。", GuideServer = "测试服", TargetServer = "测试服",
            GuideStage = "红焰深域 2-1", TargetStage = "红焰深域 2-1", GuideVersion = "test-v1", TargetVersion = "test-v1",
            Mode = "SET/AUTO", CoverageConfirmed = true, AccountLabel = "合成账号",
            AccountServer = "测试服", AccountVersion = "test-v1", AccountObservedOn = date,
            Conditions = new() {
                new() { Character = "测试角色", Form = "原版", Field = "持有", Expected = "是", Evidence = "测试角色原版，持有" },
                new() { Character = "测试角色", Form = "原版", Field = "星级", Expected = "3", Evidence = "星级恰为3" }
            },
            Facts = new() {
                new() { Character = "测试角色", Form = "原版", Field = "持有", Value = "是", Source = "合成角色页", Confirmed = true },
                new() { Character = "测试角色", Form = "原版", Field = "星级", Value = "3", Source = "合成角色页", Confirmed = true }
            }
        };
        PcrGuideReport Check(Action<PcrGuideInput> change)
        { var input = Input(); change(input); return PcrGuideReview.Check(input, now); }
        bool Has(PcrGuideReport report, string status, string item) => report.Findings.Any(f => f.Status == status && f.Item.Contains(item));
        Require(Check(_ => { }).Findings.All(f => f.Status == "相符"), "known_conditions_match");
        Require(Has(Check(i => i.Facts[0].Value = "否"), "冲突", "持有"), "explicit_missing_character");
        Require(Has(Check(i => i.Facts.Clear()), "未知", "持有"), "partial_roster_absence_is_unknown");
        Require(Has(Check(i => i.Facts.ForEach(f => f.Form = "夏日")), "未知", "持有"), "same_name_wrong_form");
        Require(Has(Check(i => i.TargetServer = "另一区服"), "冲突", "区服"), "server_mismatch");
        Require(Has(Check(i => i.TargetStage = "红焰深域 2-2"), "冲突", "关卡"), "stage_mismatch");
        Require(Has(Check(i => i.GuideVersion = "old"), "冲突", "版本"), "guide_version_mismatch");
        Require(Has(Check(i => i.AccountVersion = "old"), "冲突", "账号资料版本"), "account_version_mismatch");
        Require(Has(Check(i => i.Facts[1].Value = "5"), "冲突", "星级"), "higher_not_always_better");
        Require(Has(Check(i => { i.Facts[1].Value = "2"; i.Conditions[1].Comparison = ">="; }), "冲突", "星级"), "minimum_not_met");
        Require(Has(Check(i => { i.Facts[1].Value = "5"; i.Conditions[1].Comparison = ">="; }), "相符", "星级"), "explicit_minimum");
        Require(Has(Check(i => { i.Facts[1].Value = "5"; i.Conditions[1].Comparison = "<="; }), "冲突", "星级"), "explicit_maximum");
        Require(Has(Check(i => i.Facts[1].Value = ""), "未知", "星级"), "missing_value");
        Require(Has(Check(i => i.Facts[1].Confirmed = false), "未知", "星级"), "unconfirmed_value");
        Require(Has(Check(i => i.Facts[1].Source = ""), "未知", "星级"), "missing_account_source");
        Require(Has(Check(i => i.Conditions[1].Evidence = "并不存在的说明"), "未知", "星级"), "invented_evidence");
        Require(Has(Check(i => i.SourceConfirmed = false), "未知", "来源核实"), "unconfirmed_source");
        Require(Has(Check(i => i.SourceUrl = "file:///C:/private"), "未知", "来源核实"), "nonweb_link");
        Require(Has(Check(i => i.SourceUrl = "https://secret:password@example.com"), "未知", "来源核实"), "credentials_in_link");
        Require(Has(Check(i => i.ValidThrough = now.AddDays(-1).ToString("yyyy-MM-dd")), "未知", "来源时效"), "expired_source");
        Require(Has(Check(i => i.RetrievedOn = "wrong"), "未知", "来源时效"), "invalid_date");
        Require(Has(Check(i => i.AccountObservedOn = now.AddDays(1).ToString("yyyy-MM-dd")), "未知", "账号资料日期"), "future_account");
        Require(Has(Check(i => i.Conditions.Clear()), "未知", "条件覆盖"), "empty_conditions_never_pass");
        Require(Has(Check(i => i.CoverageConfirmed = false), "未知", "条件覆盖"), "partial_conditions_never_pass");
        Require(Has(Check(i => i.Conditions[1].Comparison = "~"), "未知", "星级"), "unknown_operator");
        Require(Has(Check(i => { i.Conditions[1].Comparison = ">="; i.Facts[1].Value = "NaN"; }), "未知", "星级"), "nonnumeric_range");
        Require(Has(Check(i => { i.Conditions[1].Expected = "-1"; i.Facts[1].Value = "-1"; }), "未知", "星级"), "invalid_equal_numeric");
        Require(Has(Check(i => i.Facts.Add(i.Facts[1])), "未知", "星级"), "ambiguous_facts");
        var reference = Check(i => i.Conditions[1].Comparison = "参考");
        Require(Has(reference, "参考一致", "星级") && reference.Summary.Contains("不是最低要求"), "reference_is_not_requirement");
        foreach (var actual in new[] { "2", "5" })
        {
            var different = Check(i => { i.Conditions[1].Comparison = "参考"; i.Facts[1].Value = actual; });
            Require(Has(different, "未知", "星级") && different.Findings.All(f => f.Status != "冲突"), "reference_difference_is_not_failure_" + actual);
        }
        Require(Has(Check(i => { i.Conditions[1].Comparison = "参考"; i.Facts[1].Value = "NaN"; }), "未知", "星级"), "reference_still_validates_numbers");
        Require(Has(Check(i => { i.Conditions[1].Comparison = "参考"; i.Facts[1].Confirmed = false; }), "未知", "星级"), "reference_requires_confirmed_fact");
        Require(Has(Check(i => { i.Conditions[1].Comparison = "参考"; i.Conditions[1].Evidence = "invented"; }), "未知", "星级"), "reference_requires_source_evidence");
        Require(Has(Check(i => i.Conditions[0].Comparison = "参考"), "未知", "持有"), "ownership_cannot_be_reference");
        PcrGuideInput Trial()
        {
            var input = Input(); input.Conditions.Clear(); input.Facts.Clear();
            input.GuideVersion = ""; input.Mode = ""; input.CoverageConfirmed = false;
            for (var n = 1; n <= 5; n++)
            {
                var row = Input(); var name = "测试角色" + n;
                row.Conditions.ForEach(c => c.Character = name); row.Facts.ForEach(f => f.Character = name);
                row.Conditions[1].Comparison = "参考"; row.Facts[1].Value = "5";
                input.Conditions.AddRange(row.Conditions); input.Facts.AddRange(row.Facts);
            }
            return input;
        }
        PcrGuideReport TrialCheck(Action<PcrGuideInput> change)
        { var input = Trial(); change(input); return PcrGuideReview.Check(input, now); }
        bool CanTry(PcrGuideReport report) => report.Summary.StartsWith("可以试打");
        var trial = TrialCheck(_ => { });
        Require(CanTry(trial) && Has(trial, "未知", "版本") && Has(trial, "未知", "星级"), "trial_keeps_reference_uncertainties");
        var unspecified = TrialCheck(i => i.Conditions[1].Expected = "");
        Require(CanTry(unspecified) && unspecified.Findings.Any(f => f.Detail.Contains("账号现状：5")), "unspecified_reference_shows_actual_without_blocking_trial");
        Require(CanTry(TrialCheck(i => i.Facts.RemoveAt(1))), "missing_reference_fact_does_not_block_trial");
        Require(!CanTry(TrialCheck(i => i.Facts[0].Value = "否")), "trial_blocks_missing_character");
        Require(!CanTry(TrialCheck(i => i.Facts[0].Form = "夏日")), "trial_blocks_wrong_form");
        Require(!CanTry(TrialCheck(i => i.TargetServer = "另一区服")), "trial_blocks_wrong_server");
        Require(!CanTry(TrialCheck(i => i.TargetStage = "另一个关卡")), "trial_blocks_wrong_stage");
        Require(CanTry(TrialCheck(i => i.Conditions[1].Comparison = "=")), "trial_warns_explicit_training_difference");
        Require(CanTry(TrialCheck(i => { i.Conditions[1].Comparison = "="; i.Facts.RemoveAt(1); })), "trial_warns_unknown_training");
        Require(CanTry(TrialCheck(i => i.Facts[1].Value = "NaN")), "invalid_training_is_not_verified_or_a_gate");
        Require(CanTry(TrialCheck(i => { i.Conditions[1].Expected = ""; i.Facts[1].Value = "NaN"; })), "unspecified_training_is_advisory");
        Require(CanTry(TrialCheck(i => i.Conditions[1].Evidence = "invented")), "unverified_text_stays_advisory");
        Require(CanTry(TrialCheck(i => i.Facts.Add(i.Facts[1]))), "ambiguous_training_stays_unknown");
        Require(CanTry(TrialCheck(i => i.SourceConfirmed = false)), "no_separate_source_confirmation_gate");
        Require(CanTry(TrialCheck(i => i.ValidThrough = now.AddDays(-1).ToString("yyyy-MM-dd"))), "source_expiry_is_advisory");
        Require(!CanTry(TrialCheck(i => i.Facts.Add(i.Facts[0]))), "ambiguous_ownership_still_blocks");
        Require(!CanTry(TrialCheck(i => i.AccountVersion = "wrong")), "account_version_still_blocks");
        Require(CanTry(TrialCheck(i => i.GuideVersion = "old")), "guide_version_is_advisory");
        Require(!CanTry(TrialCheck(i => i.Conditions.RemoveRange(8, 2))), "trial_requires_complete_team");
        var textExample = PcrGuideReview.TextExample(); var textReport = PcrGuideReview.Check(textExample, now);
        Require(textExample.Conditions.Count == 24 && textExample.Conditions.Select(c => c.Character).Distinct().Count() == 5,
            "real_text_has_five_member_team");
        Require(textExample.Conditions.All(c => textExample.Material.Contains(c.Evidence)) && textExample.Facts.Count == 0,
            "real_text_evidence_without_invented_account");
        Require(textExample.Conditions.Where(c => c.Field is "星级" or "Rank" or "专武1等级" or "限界突破").All(c => c.Comparison == "参考"),
            "real_clear_stats_are_reference_only");
        Require(Has(textReport, "冲突", "区服") && Has(textReport, "未知", "版本") && Has(textReport, "未知", "条件覆盖"),
            "real_jp_source_never_masquerades_as_cn");
        File.WriteAllText(Path.Combine(root, "text-example.json"), PcrGuideReview.Serialize(new PcrGuideDocument { Input = textExample, Report = textReport }));
        File.WriteAllText(Path.Combine(root, "text-example-report.txt"), textReport.Text);
        var real = PcrGuideReview.RealExample(); var realReport = PcrGuideReview.Check(real, now);
        Require(realReport.Summary.Contains("资料不足") && real.Facts.Count == 0, "real_source_does_not_invent_account");
        File.WriteAllText(Path.Combine(root, "real-example-report.txt"), realReport.Text);
        File.WriteAllText(Path.Combine(root, "real-example.json"), PcrGuideReview.Serialize(new PcrGuideDocument { Input = real, Report = realReport }));
        Throws(() => PcrGuideReview.Parse("{\"Input\":null}"), "null_input");
        Throws(() => PcrGuideReview.Parse("{\"Input\":{\"Facts\":null}}"), "null_facts");
        Throws(() => PcrGuideReview.Parse("{\"password\":\"not-a-real-secret\"}"), "unexpected_sensitive_field");
        var path = Path.Combine(root, "review.json");
        var store = new PcrGuideStore(path); store.Load();
        var valid = Input(); store.Save(new() { Input = valid, Report = PcrGuideReview.Check(valid, now) });
        var reopened = new PcrGuideStore(path).Load();
        Require(reopened.Input.SourceUrl == valid.SourceUrl && reopened.Input.Facts.All(f => f.Confirmed), "sources_confirmation_roundtrip");
        Require(reopened.Report != null && reopened.Report.InputHash == PcrGuideReview.Fingerprint(valid), "report_roundtrip");
        var other = new PcrGuideStore(path); other.Load(); valid.Title = "新版本"; store.Save(new() { Input = valid });
        var kept = File.ReadAllText(path);
        Throws(() => other.Save(new() { Input = Input() }), "concurrent_write");
        Require(File.ReadAllText(path) == kept, "concurrent_write_preserves_file");
        reopened.Input.TargetVersion = "changed";
        File.WriteAllText(path, PcrGuideReview.Serialize(reopened));
        Require(new PcrGuideStore(path).Load().Report == null, "changed_input_invalidates_stored_report");
        valid.ValidThrough = now.AddDays(-1).ToString("yyyy-MM-dd");
        File.WriteAllText(path, PcrGuideReview.Serialize(new PcrGuideDocument { Input = valid,
            Report = PcrGuideReview.Check(valid, now.AddDays(-1)) }));
        Require(new PcrGuideStore(path).Load().Report!.Summary.Contains("资料不足"), "reopen_rechecks_expiry");
        File.WriteAllText(path, "{broken"); var broken = new PcrGuideStore(path);
        Throws(() => broken.Load(), "corrupt_load"); Throws(() => broken.Save(new()), "corrupt_save_blocked");
        Require(File.ReadAllText(path) == "{broken", "corrupt_original_preserved");

        var uiPath = Path.Combine(root, "ui-review.json");
        var view = new PcrGuideReviewView(uiPath); view.Display(Input()); view.Check();
        Require(view.HasCurrentReport, "ui_check"); view.Save();
        var newView = new PcrGuideReviewView(uiPath);
        Dispatcher.UIThread.RunJobs();
        Require(newView.HasCurrentReport && newView.Capture().Facts.All(f => f.Confirmed), "ui_save_reopen");
        var textBoxes = (Dictionary<string, TextBox>)typeof(PcrGuideReviewView).GetField("fields", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(newView)!;
        var version = textBoxes["GuideVersion"];
        version.Text = "new-version"; Dispatcher.UIThread.RunJobs();
        Require(!newView.HasCurrentReport && newView.ReportText.Contains("失效"), "ui_edit_invalidates_report");
        Require(!newView.Capture().SourceConfirmed, "source_edit_requires_reconfirmation");
        newView.Save(); Require(new PcrGuideStore(uiPath).Load().Report == null, "ui_stale_report_not_saved");
        newView.Display(new() { Conditions = Input().Conditions }); newView.AddFactsFromConditions();
        Require(newView.Capture().Facts.All(f => f.Value == "" && !f.Confirmed), "generated_facts_are_unknown");
        newView.Reload(); newView.LoadExample(); newView.Check(); newView.Save();
        Require(newView.ReportText.Contains("资料不足"), "ui_real_example_unknown");
        // An invisible, non-activating host supplies templates; it never steals focus or starts the app.
        newView.Background = Avalonia.Media.Brushes.White;
        var host = new Window { Content = newView, Width = 920, Height = 700, ShowInTaskbar = false, ShowActivated = false, Opacity = 0 };
        host.Show();
        try
        {
        foreach (var index in new[] { 0, 1, 2 })
        {
            var tab = (TabControl)typeof(PcrGuideReviewView).GetField("tabs", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(newView)!; tab.SelectedIndex = index;
            newView.Width = 920; newView.Height = 700;
            newView.Measure(new Size(920, 700)); newView.Arrange(new Rect(0, 0, 920, 700));
            newView.UpdateLayout(); Dispatcher.UIThread.RunJobs();
            using var bitmap = new RenderTargetBitmap(new PixelSize(920, 700), new Vector(96, 96));
            bitmap.Render(newView); bitmap.Save(Path.Combine(root, $"guide-tab-{index}.png"));
        }
        Require(newView.HasCurrentReport && newView.ReportText.Contains("资料不足"), "layout_does_not_invalidate_loaded_report");
        newView.Display(Input()); newView.Check();
        var factGrid = (DataGrid)typeof(PcrGuideReviewView).GetField("facts", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(newView)!;
        var accountTab = (TabControl)typeof(PcrGuideReviewView).GetField("tabs", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(newView)!;
        accountTab.SelectedIndex = 1; newView.UpdateLayout(); Dispatcher.UIThread.RunJobs();
        var row = ((System.Collections.IEnumerable)factGrid.ItemsSource!).Cast<PcrGuideFact>().Last();
        factGrid.SelectedItem = row; factGrid.CurrentColumn = factGrid.Columns[3];
        Require(factGrid.BeginEdit(), "account_grid_can_edit"); newView.UpdateLayout();
        var editor = factGrid.GetVisualDescendants().OfType<TextBox>().Single(); editor.Text = "5";
        factGrid.CommitEdit(DataGridEditingUnit.Row, true); Dispatcher.UIThread.RunJobs();
        Require(row.Value == "5" && !row.Confirmed && !newView.HasCurrentReport, "account_grid_edit_clears_confirmation_and_report");
        newView.Reload(); newView.LoadExample(true); newView.Check();
        Require(newView.ReportText.Contains("存在明确冲突") && newView.Capture().Conditions.Count == 24, "ui_text_example_checks_server");
        var before = PcrGuideReview.Fingerprint(newView.Capture()); newView.LoadExample();
        Require(PcrGuideReview.Fingerprint(newView.Capture()) == before, "example_does_not_discard_unsaved_edits");
        newView.Save(); var textReopened = new PcrGuideStore(uiPath).Load();
        Require(textReopened.Input.Conditions.Count(c => c.Comparison == "参考") == 14 && textReopened.Input.Facts.Count == 0
            && textReopened.Report!.Summary.Contains("冲突"), "reference_and_real_source_roundtrip");
        accountTab.SelectedIndex = 0; newView.UpdateLayout(); Dispatcher.UIThread.RunJobs();
        using (var bitmap = new RenderTargetBitmap(new PixelSize(920, 700), new Vector(96, 96)))
        { bitmap.Render(newView); bitmap.Save(Path.Combine(root, "text-example-tab.png")); }
        }
        finally { host.Close(); }
        Console.WriteLine($"PCR guide review checks passed: {count}; synthetic controls, no game input");
    }
}
