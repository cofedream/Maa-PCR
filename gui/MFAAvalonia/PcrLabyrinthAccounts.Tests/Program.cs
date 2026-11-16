using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using Avalonia;
using Avalonia.Controls;
using Avalonia.LogicalTree;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Styling;
using Avalonia.Threading;
using MFAAvalonia.Configuration;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

if (args.Length!=2 || args.Any(arg=>!Path.IsPathFullyQualified(arg)) || !OperatingSystem.IsWindows()) return 2;
try {
    AppBuilder.Configure<Application>().UsePlatformDetect().SetupWithoutStarting();
    Application.Current!.RequestedThemeVariant=ThemeVariant.Light;
    Application.Current.Styles.Add(new SukiUI.SukiTheme());
    var root=Path.Combine(args[0],"accounts-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
    const BindingFlags fields=BindingFlags.Instance|BindingFlags.Static|BindingFlags.NonPublic;
    typeof(AppPaths).GetField("_configDirectory",fields)!.SetValue(null,Path.Combine(root,"config"));
    typeof(AppPaths).GetField("_initialized",fields)!.SetValue(null,true);
    PcrLabyrinthCharacterCatalog.UseRepositoryRoot(args[1]);
    PcrLabyrinthBossCatalog.UseRepositoryRoot(args[1]);
    var count=0;
    void Require(bool condition,string name) { if (!condition) throw new InvalidOperationException(name); count++; }
    TaskQueueViewModel Owner(InstanceConfiguration config) {
        var processor=(MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        typeof(MaaProcessor).GetField("<InstanceId>k__BackingField",fields)!.SetValue(processor,Path.GetFileNameWithoutExtension(config.GetConfigFilePath()));
        typeof(MaaProcessor).GetField("<InstanceConfiguration>k__BackingField",fields)!.SetValue(processor,config);
        var vm=(TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        typeof(TaskQueueViewModel).GetField("_processorField",fields)!.SetValue(vm,processor); return vm;
    }
    string Encrypt(string player,string login,string password) {
        var bytes=Encoding.UTF8.GetBytes(new JObject { ["version"]=1,["expected_player_id"]=player,["username"]=login,["password"]=password }.ToString(Formatting.None));
        try { return Convert.ToBase64String(ProtectedData.Protect(bytes,null,DataProtectionScope.CurrentUser)); }
        finally { CryptographicOperations.ZeroMemory(bytes); }
    }
    const string profile="pcr-labyrinth-accounts", bookKey="PcrLabyrinthAccountsV1";
    const string playerA="98765432111111111",loginA="fixture-private-A",passwordA="fixture-secret-A";
    var config=new InstanceConfiguration(profile);
    var legacy=new JObject { ["version"]=1,["goals"]=new JArray(new JObject { ["guild_id"]=1,["clears"]=3 }),
        ["difficulty"]=4,["max_seconds"]=61,["strategy"]=new JObject {
            ["personal_scores"]=new JObject { ["1001"]=87.5,["9999"]=61.25 },
            ["opening_rosters"]=new JObject { ["1"]=new JArray(new JArray("1075"),new JArray("1059"),new JArray("1064")) } } };
    config.SetValueAndPersist("unrelated-fixture-marker","retain-me");
    config.SetValueAndPersist(PcrLabyrinthSettings.Key,legacy.ToString(Formatting.None));
    config.SetValueAndPersist(PcrLabyrinthSettings.CredentialsKey,Encrypt(playerA,loginA,passwordA));
    var file=config.GetConfigFilePath(); var beforeLoad=File.ReadAllBytes(file);
    PcrLabyrinthSettings Reload() => new(Owner(new InstanceConfiguration(profile)));
    var settings=new PcrLabyrinthSettings(Owner(config));
    Require(File.ReadAllBytes(file).SequenceEqual(beforeLoad),"loading_legacy_profile_does_not_modify_file");
    Require(settings.Accounts.Count==1 && settings.Server=="cn-bilibili" && settings.ExpectedPlayerId==playerA
        && settings.Username==loginA && settings.Password==passwordA,"legacy_account_and_dpapi_values_loaded");
    Require(settings.Difficulty=="4" && (int?)settings.Snapshot()["max_seconds"]==61
        && settings.Goals[0].Clears=="3", "legacy_strategy_goal_and_non_minute_deadline_preserved");
    Require(settings.GetPersonalScores()["9999"]!.Value<decimal>()==61.25m && settings.Goals[0].HasCustomRoster,
        "legacy_unknown_decimal_ratings_and_roster_preserved");
    settings.AccountName="测试主账号";
    Require(config.GetValue(bookKey,"").Length>0 && Reload().AccountName=="测试主账号","account_rename_migrates_and_persists_library");
    var primary=settings.SelectedAccount!;
    settings.ResponseIntervalMs=500; settings.AutoDatabaseUpdate=false;
    settings.SpecialEquipmentMode="自动装备"; settings.SpecialEquipmentEx=false;
    Require((int?)Reload().Snapshot()["strategy"]?["response_interval_ms"]==500
        && (bool?)Reload().Snapshot()["strategy"]?["auto_database_update"]==false,"response_and_database_options_persist");
    Require(Reload().Snapshot()["strategy"]?["special_equipment_battles"]?.Values<string>().SequenceEqual(new[]{"NORMAL","BOSS"})==true,
        "special_equipment_mode_and_scope_persist");
    settings.AddAccountCommand.Execute(null);
    Require(settings.Accounts.Count==2 && settings.SelectedAccount!=primary && settings.Username=="" && settings.Password==""
        && settings.ExpectedPlayerId=="" && !settings.ShowCredentials,"adding_account_starts_empty_and_masked");
    Require(settings.Difficulty=="1" && settings.Goals[0].Clears=="1" && settings.GetPersonalScores().Count==0
        && !settings.Goals.Any(goal=>goal.HasCustomRoster) && settings.ResponseIntervalMs==300 && settings.AutoDatabaseUpdate,
        "new_account_does_not_inherit_settings_or_personal_ratings");
    settings.AccountName="渠道测试账号"; settings.Server="cn-vivo";
    settings.ExpectedPlayerId="98765432222222222"; settings.Username="fixture-channel-uid"; settings.Password="fixture-channel-access-key";
    settings.Difficulty="2"; settings.Goals[0].Clears="8";
    var secondary=settings.SelectedAccount!;
    Require(Reload().Server=="cn-vivo" && Reload().SelectedAccount!.Id==secondary.Id && Reload().Password==settings.Password,
        "selected_channel_account_roundtrip");
    Require(settings.LoginLabel.Contains("UID") && settings.PasswordLabel.Contains("access_key") && settings.LoginHint.Contains("个人简介"),
        "channel_credentials_have_correct_visible_labels");
    var raw=File.ReadAllText(file);
    Require(new[]{playerA,loginA,passwordA,"fixture-channel-uid","fixture-channel-access-key"}.All(secret=>!raw.Contains(secret)),
        "no_plaintext_player_uid_login_or_password_on_disk");
    Require(new InstanceConfiguration(profile).GetValue("unrelated-fixture-marker","")=="retain-me","unrelated_instance_values_retained");
    settings.SelectedAccount=primary;
    Require(settings.Username==loginA && settings.Password==passwordA && settings.ExpectedPlayerId==playerA
        && settings.Difficulty=="4" && settings.Goals[0].Clears=="3" && settings.ResponseIntervalMs==500,
        "account_switch_restores_credentials_and_independent_settings");
    Require(settings.GetPersonalScores()["1001"]!.Value<decimal>()==87.5m && settings.Goals[0].HasCustomRoster
        && !settings.ShowCredentials,"switch_back_retains_personal_data_and_hides_credentials");
    var isolatedScores=settings.GetPersonalScores(); isolatedScores["1001"]=4;
    Require(settings.GetPersonalScores()["1001"]!.Value<decimal>()==87.5m,"ratings_get_returns_independent_draft");
    isolatedScores["1001"]=75; settings.ApplyPersonalScores(isolatedScores);
    Require(Reload().GetPersonalScores()["1001"]!.Value<int>()==75 && Reload().GetPersonalScores()["9999"]!.Value<decimal>()==61.25m,
        "ratings_apply_updates_current_account_and_retains_unedited_unknown_decimal");
    var rejectedRatingDraft=settings.GetPersonalScores(); rejectedRatingDraft["1001"]=11;
    bool ratingSaveFailed=false;
    using (var ratingLock=new FileStream(file,FileMode.Open,FileAccess.Read,FileShare.Read)) {
        try { settings.ApplyPersonalScores(rejectedRatingDraft); } catch (InvalidOperationException) { ratingSaveFailed=true; }
    }
    Require(ratingSaveFailed && settings.GetPersonalScores()["1001"]!.Value<int>()==75,"failed_rating_save_rolls_back_active_scores");
    settings.Difficulty="5";
    Require(Reload().GetPersonalScores()["1001"]!.Value<int>()==75,"other_setting_save_does_not_commit_cancelled_rating_draft");
    settings.Difficulty="4";
    settings.Difficulty="unfinished-draft";
    settings.SelectedAccount=secondary;
    Require(settings.SelectedAccount==primary && settings.Difficulty=="unfinished-draft" && settings.Status.StartsWith("保存失败"),
        "switch_blocked_if_task_draft_invalid");
    settings.Difficulty="4";
    settings.SelectedAccount=secondary;
    settings.Server="cn-huawei";
    Require(settings.Username=="" && settings.Password=="" && settings.ExpectedPlayerId==""
        && Reload().Password=="" && Reload().Server=="cn-huawei","changing_server_clears_old_server_credentials");
    settings.Server="cn-vivo"; settings.ExpectedPlayerId="98765432222222222";
    settings.Username="fixture-channel-uid"; settings.Password="fixture-channel-access-key";
    var beforeDelete=File.ReadAllBytes(file);
    settings.DeleteAccountCommand.Execute(null);
    Require(settings.DeleteAccountPending && settings.Accounts.Count==2 && File.ReadAllBytes(file).SequenceEqual(beforeDelete),
        "delete_first_click_only_requests_confirmation");
    settings.DeleteAccountCommand.Execute(null);
    Require(settings.Accounts.Count==1 && settings.SelectedAccount==primary && Reload().Username==loginA,
        "delete_confirm_removes_only_selected_account");
    var sameFile=File.ReadAllBytes(file); settings.IsPaused=true; settings.PausePending=true;
    Require(settings.PauseLabel=="正在等待暂停…" && File.ReadAllBytes(file).SequenceEqual(sameFile),"pause_status_is_runtime_only");
    settings.PausePending=false; Require(settings.PauseLabel=="继续","paused_label_offers_resume"); settings.IsPaused=false;
    var stale=Reload(); settings.AccountName="并发更新后的账号";
    var currentBytes=File.ReadAllBytes(file);
    stale.Difficulty="3";
    Require(stale.Status.Contains("其他窗口") && File.ReadAllBytes(file).SequenceEqual(currentBytes),
        "stale_account_writer_cannot_overwrite_current_library");
    settings.Username="fixture-locked-previous";
    var previousBytes=File.ReadAllBytes(file);
    using (var locked=new FileStream(file,FileMode.Open,FileAccess.Read,FileShare.Read)) {
        settings.Password="fixture-locked-new";
        Require(settings.CredentialsStatus.Contains("保存失败") && File.ReadAllBytes(file).SequenceEqual(previousBytes),
            "failed_atomic_credential_save_keeps_previous_file");
    }
    settings.Save(); Require(Reload().Password=="fixture-locked-new","pending_credential_save_retries_after_lock_released");
    var outside=new InstanceConfiguration(profile); var book=JObject.Parse(outside.GetValue(bookKey,""));
    var selected=(string)book["selected_id"]!;
    book["accounts"]!.Single(item=>(string?)item["id"]==selected)["server"]="cn-unrecognized-fixture";
    outside.SetValueAndPersist(bookKey,book.ToString(Formatting.None));
    var unknown=Reload();
    Require(unknown.Server=="cn-unrecognized-fixture" && unknown.SelectedServer is null,"unknown_saved_server_never_defaults_to_bilibili");
    bool rejected=false; try { unknown.Snapshot(); } catch (InvalidOperationException) { rejected=true; }
    Require(rejected,"unknown_saved_server_cannot_start");
    var unsupportedBefore=File.ReadAllBytes(file); unknown.Save();
    Require(unknown.Status.StartsWith("保存失败") && File.ReadAllBytes(file).SequenceEqual(unsupportedBefore),"unknown_saved_server_preserved_until_explicit_correction");
    unknown.Server="cn-bilibili";
    Require(unknown.Password=="" && Reload().Server=="cn-bilibili","correcting_unknown_server_requires_new_credentials");
    var unsupportedConfig=new InstanceConfiguration("pcr-labyrinth-accounts-future");
    unsupportedConfig.SetValueAndPersist(bookKey,new JObject { ["version"]=2,["private-future-field"]="retain-fixture" }.ToString(Formatting.None));
    var futureBefore=File.ReadAllBytes(unsupportedConfig.GetConfigFilePath());
    var future=new PcrLabyrinthSettings(Owner(unsupportedConfig)); future.Save();
    Require(future.Status.StartsWith("保存失败") && File.ReadAllBytes(unsupportedConfig.GetConfigFilePath()).SequenceEqual(futureBefore),
        "unsupported_account_library_cannot_be_overwritten_with_defaults");
    var guiOwner=Owner(new InstanceConfiguration(profile)); var view=new PcrLabyrinthView { DataContext=guiOwner,Background=Brushes.White };
    var window=new Window { Content=view,Width=1080,Height=1100,ShowActivated=false,ShowInTaskbar=false,Position=new PixelPoint(-12000,-12000) };
    try {
        window.Show(); Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        var accountPanel=view.GetLogicalDescendants().OfType<Expander>().Single(control=>control.Name=="AccountSettings");
        accountPanel.IsExpanded=true; Dispatcher.UIThread.RunJobs(); System.Threading.Thread.Sleep(350); Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        var accountSelector=view.GetLogicalDescendants().OfType<ComboBox>().Single(control=>control.Name=="AccountSelector");
        Require(accountSelector.Items.Count==1 && accountSelector.SelectedItem==guiOwner.LabyrinthSettings.SelectedAccount,
            "real_account_selector_is_bound");
        // 账号 A 给 1 号公会选过三人后切到使用默认阵容的账号 B：B 没有 opening_rosters 条目，
        // 也必须刷新方案标签与默认名单，不能停在 A 的「已自定义」。
        var guiSettings=guiOwner.LabyrinthSettings;
        var openingPanel=view.GetLogicalDescendants().OfType<Expander>().Single(control=>control.Name=="OpeningSettings");
        openingPanel.IsExpanded=true; Dispatcher.UIThread.RunJobs(); System.Threading.Thread.Sleep(350); Dispatcher.UIThread.RunJobs();
        openingPanel.BringIntoView(); Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        var guildOne=guiSettings.Goals[0];
        foreach (var pick in guildOne.Pool.Take(3).ToArray()) pick.ToggleCommand!.Execute(null);
        Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        var guildOneTexts=view.GetLogicalDescendants().OfType<TextBlock>().Where(node=>node.DataContext==guildOne).ToArray();
        Require(guildOne.HasCustomRoster && guildOne.PlanLabel=="已自定义"
            && guildOneTexts.Any(node=>node.Text=="已自定义"), "account_a_custom_roster_shows_custom_label");
        guiSettings.AddAccountCommand.Execute(null);
        Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        Require(!guildOne.HasCustomRoster && guildOne.PlanLabel=="使用内置默认"
            && !string.IsNullOrEmpty(guildOne.DefaultPlanText),
            "account_switch_refreshes_builtin_plan: "+guildOne.PlanLabel);
        Require(guildOneTexts.Any(node=>node.Text=="使用内置默认"),
            "account_switch_updates_bound_plan_label: "+string.Join("|",guildOneTexts.Select(node=>node.Text)));
        guiSettings.SelectedAccount=guiSettings.Accounts[0];
        Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        Require(guildOne.HasCustomRoster && guildOneTexts.Any(node=>node.Text=="已自定义"),
            "account_switch_back_restores_custom_plan_label");
        openingPanel.IsExpanded=false;
        var serverSelector=view.GetLogicalDescendants().OfType<ComboBox>().Single(control=>control.Name=="ServerSelector");
        serverSelector.SetCurrentValue(ComboBox.SelectedItemProperty,PcrLabyrinthServer.All.Single(item=>item.Id=="cn-mumu"));
        Dispatcher.UIThread.RunJobs();
        Require(guiOwner.LabyrinthSettings.Server=="cn-mumu" && Reload().Server=="cn-mumu","real_server_selector_change_persists");
        Require(view.GetLogicalDescendants().OfType<TextBox>().Single(control=>control.Name=="UsernameInput").PasswordChar=='*',
            "real_channel_uid_control_starts_masked");
        using (var frame=new RenderTargetBitmap(new PixelSize(1080,1100))) { frame.Render(view); frame.Save(Path.Combine(root,"accounts-view.png")); }
        accountPanel.IsExpanded=false;
        var rerollPanel=view.GetLogicalDescendants().OfType<Expander>().Single(control=>control.Name=="RerollSettings");
        rerollPanel.IsExpanded=true; Dispatcher.UIThread.RunJobs(); System.Threading.Thread.Sleep(350); Dispatcher.UIThread.RunJobs();
        rerollPanel.BringIntoView(); Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        var interval=view.GetLogicalDescendants().OfType<ComboBox>().Single(control=>control.Items.OfType<int>().SequenceEqual(new[]{300,500}));
        interval.SetCurrentValue(ComboBox.SelectedItemProperty,300); Dispatcher.UIThread.RunJobs();
        Require(guiOwner.LabyrinthSettings.ResponseIntervalMs==300 && Reload().ResponseIntervalMs==300,"real_response_interval_control_persists");
        var database=view.GetLogicalDescendants().OfType<CheckBox>().Single(control=>(control.Content as string)?.StartsWith("在后台检查游戏数据库") == true);
        database.SetCurrentValue(CheckBox.IsCheckedProperty,true); Dispatcher.UIThread.RunJobs();
        Require(guiOwner.LabyrinthSettings.AutoDatabaseUpdate && Reload().AutoDatabaseUpdate,"real_database_update_control_persists");
        using (var frame=new RenderTargetBitmap(new PixelSize(1080,1100))) { frame.Render(view); frame.Save(Path.Combine(root,"runtime-options-view.png")); }
        rerollPanel.IsExpanded=false;
        var routePanel=view.GetLogicalDescendants().OfType<Expander>().Single(control=>control.Name=="RouteSettings");
        routePanel.IsExpanded=true; Dispatcher.UIThread.RunJobs(); System.Threading.Thread.Sleep(350); Dispatcher.UIThread.RunJobs();
        routePanel.BringIntoView(); Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        var bossChoices=view.GetLogicalDescendants().OfType<CheckBox>()
            .Where(control=>control.Content is StackPanel panel && panel.Children.OfType<Image>().Any()).ToArray();
        Require(bossChoices.Length==10,"real_boss_filter_shows_ten_icon_checkboxes");
        Require(bossChoices.All(control=>control.Content is StackPanel panel
                && panel.Children.OfType<Image>().Single().Source is Bitmap bitmap && bitmap.PixelSize.Width==128),
            "real_boss_filter_icons_decode_from_boss_pack");
        using (var frame=new RenderTargetBitmap(new PixelSize(1080,1100))) { frame.Render(view); frame.Save(Path.Combine(root,"boss-filter-view.png")); }
        routePanel.IsExpanded=false;
        var teamPanel=view.GetLogicalDescendants().OfType<Expander>().Single(control=>control.Name=="TeamSettings");
        teamPanel.IsExpanded=true; Dispatcher.UIThread.RunJobs(); System.Threading.Thread.Sleep(350); Dispatcher.UIThread.RunJobs();
        teamPanel.BringIntoView(); Dispatcher.UIThread.RunJobs(); view.UpdateLayout();
        var equipment=view.GetLogicalDescendants().OfType<ComboBox>().Single(control=>control.Items.OfType<string>().SequenceEqual(new[]{"关闭","自动装备"}));
        equipment.SetCurrentValue(ComboBox.SelectedItemProperty,"关闭"); Dispatcher.UIThread.RunJobs();
        Require(guiOwner.LabyrinthSettings.SpecialEquipmentMode=="关闭" && Reload().SpecialEquipmentMode=="关闭","real_equipment_mode_control_persists");
        Require(view.GetLogicalDescendants().OfType<Button>().Any(control=>control.Name=="RoleRatingsButton" && control.IsEffectivelyEnabled),
            "personal_rating_editor_has_enabled_visible_entry");
        using (var frame=new RenderTargetBitmap(new PixelSize(1080,1100))) { frame.Render(view); frame.Save(Path.Combine(root,"team-options-view.png")); }
    } finally { window.Close(); Dispatcher.UIThread.RunJobs(); }
    Require(!MaaProcessorManager.IsInstanceCreated && !Directory.EnumerateFiles(root,"*.tmp",SearchOption.AllDirectories).Any(),
        "no_device_manager_or_atomic_temporary_file_left");
    Console.WriteLine($"Labyrinth account and settings checks passed: {count}; network logins=0; game inputs=0; evidence={root}"); return 0;
} catch (Exception error) { Console.Error.WriteLine("Labyrinth account checks failed: "+error); return 1; }
