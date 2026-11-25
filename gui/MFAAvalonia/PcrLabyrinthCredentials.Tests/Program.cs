using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Xml.Linq;
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
using Newtonsoft.Json.Linq;

if (args.Length != 2 || args.Any(arg => !Path.IsPathFullyQualified(arg)) || !OperatingSystem.IsWindows())
{
    Console.Error.WriteLine("PCR credential checks require absolute evidence/source directories and Windows.");
    return 2;
}
try
{
    AppBuilder.Configure<Application>().UsePlatformDetect().SetupWithoutStarting();
    Application.Current!.RequestedThemeVariant = ThemeVariant.Light;
    Application.Current!.Styles.Add(new SukiUI.SukiTheme { ThemeColor = SukiUI.Enums.SukiColor.Blue });
    var count = 0;
    void Require(bool result, string name)
    {
        if (!result) throw new InvalidOperationException(name);
        count++;
    }
    var root = Path.Combine(args[0], "gui-credentials-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(root);
    // Bypass owner/device initialization; use real settings and persistence with an isolated config root.
    const BindingFlags fields = BindingFlags.Instance | BindingFlags.Static | BindingFlags.NonPublic;
    typeof(AppPaths).GetField("_configDirectory", fields)!.SetValue(null, Path.Combine(root, "config"));
    typeof(AppPaths).GetField("_initialized", fields)!.SetValue(null, true);
    PcrLabyrinthBossCatalog.UseRepositoryRoot(args[1]);
    TaskQueueViewModel Owner(InstanceConfiguration config)
    {
        var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
        typeof(MaaProcessor).GetField("<InstanceId>k__BackingField", fields)!.SetValue(processor,
            Path.GetFileNameWithoutExtension(config.GetConfigFilePath()));
        typeof(MaaProcessor).GetField("<InstanceConfiguration>k__BackingField", fields)!.SetValue(processor, config);
        var owner = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        typeof(TaskQueueViewModel).GetField("_processorField", fields)!.SetValue(owner, processor);
        return owner;
    }
    const string fixtureId = "pcr-labyrinth-v1";
    PcrLabyrinthSettings Reload() => new(Owner(new InstanceConfiguration(fixtureId)));
    var config = new InstanceConfiguration(fixtureId);
    var firstOwner = Owner(config);
    Require(firstOwner.CanEditPcrTarget, "isolated_owner_allows_settings_edits");
    var probe = Encoding.UTF8.GetBytes("DPAPI-fixture-only");
    var protectedProbe = ProtectedData.Protect(probe, null, DataProtectionScope.CurrentUser);
    var decodedProbe = ProtectedData.Unprotect(protectedProbe, null, DataProtectionScope.CurrentUser);
    try { Require(probe.SequenceEqual(decodedProbe), "windows_current_user_dpapi_available"); }
    finally { CryptographicOperations.ZeroMemory(probe); CryptographicOperations.ZeroMemory(decodedProbe); }
    var identityRoot = Path.Combine(root, "development-identity");
    Directory.CreateDirectory(identityRoot);
    File.WriteAllText(Path.Combine(identityRoot, "pcr-repository.txt"), identityRoot);
    var identityGit = Path.Combine(identityRoot, ".git");
    Directory.CreateDirectory(identityGit);
    File.WriteAllText(Path.Combine(identityGit, "HEAD"), "ref: refs/heads/feat/title-test\n");
    Require(DevelopmentIdentity.Resolve(identityRoot) == " · feat/title-test",
        "development_title_suffix_uses_branch");
    var linkedGit = Path.Combine(root, "linked-git");
    Directory.CreateDirectory(linkedGit);
    File.WriteAllText(Path.Combine(linkedGit, "HEAD"), "ref: refs/heads/worktree-branch\n");
    Directory.Delete(identityGit, recursive: true);
    File.WriteAllText(Path.Combine(identityRoot, ".git"), "gitdir: " + linkedGit);
    Require(DevelopmentIdentity.Resolve(identityRoot) == " · worktree-branch",
        "linked_worktree_title_suffix_uses_branch");
    File.Delete(Path.Combine(identityRoot, "pcr-repository.txt"));
    Require(DevelopmentIdentity.Resolve(identityRoot) == "",
        "installed_title_has_no_development_suffix");
    SettingsUxChecks.Run(args[1], root, Owner, Require);
    var settings = new PcrLabyrinthSettings(firstOwner);
    Require(!settings.KeepOldRun, "keep_old_run_defaults_off_for_old_and_new_config");
    settings.KeepOldRun = true;
    Require(Reload().KeepOldRun, "keep_old_run_switch_is_persisted");
    Require(!settings.Snapshot().ContainsKey("keep_old_run"), "restart_policy_does_not_change_old_batch_goal_snapshot");
    settings.KeepOldRun = false;
    Require(!Reload().KeepOldRun, "keep_old_run_can_be_saved_off");
    Require(!settings.ShowCredentials && settings.CredentialMask == '*', "fresh_credentials_masked");
    const string player = "98765432123456789", username = "fixture-CRED-username", password = "fixture-CRED-password";
    settings.ExpectedPlayerId = player;
    settings.Username = username;
    settings.Password = password;
    var file = config.GetConfigFilePath();
    Require(File.Exists(file), "credential_edit_persists_config_file: " + settings.CredentialsStatus);
    var stored = File.ReadAllText(file);
    Require(!stored.Contains(player) && !stored.Contains(username) && !stored.Contains(password), "all_three_values_absent_from_disk_plaintext");
    var ciphertext = config.GetValue(PcrLabyrinthSettings.CredentialsKey, "");
    Require(ciphertext.Length > 0 && JToken.Parse(stored)[PcrLabyrinthSettings.CredentialsKey]?.Type == JTokenType.String,
        "only_ciphertext_stored");
    var plaintext = ProtectedData.Unprotect(Convert.FromBase64String(ciphertext), null, DataProtectionScope.CurrentUser);
    try
    {
        var decoded = JObject.Parse(Encoding.UTF8.GetString(plaintext));
        Require((string?)decoded["expected_player_id"] == player && (string?)decoded["username"] == username
            && (string?)decoded["password"] == password, "windows_current_user_dpapi_roundtrip");
    }
    finally { CryptographicOperations.ZeroMemory(plaintext); }
    var loaded = Reload();
    Require(loaded.ExpectedPlayerId == player && loaded.Username == username && loaded.Password == password,
        "restart_restores_all_three_values");
    Require(!loaded.ShowCredentials && loaded.CredentialMask == '*', "restart_hides_credentials");
    loaded.ToggleCredentialsCommand.Execute(null);
    Require(loaded.ShowCredentials && loaded.CredentialMask == '\0' && loaded.CredentialsVisibilityText == "隐藏",
        "view_button_reveals_credentials");
    loaded.HideCredentials();
    Require(!loaded.ShowCredentials && loaded.CredentialMask == '*' && loaded.Username == username && loaded.Password == password,
        "start_switch_hide_keeps_credentials");
    var cacheRoot=PcrRuntimePaths.ResolveDataRepository(AppContext.BaseDirectory);
    var tokenSettings=new PcrLabyrinthSettings(Owner(new InstanceConfiguration("sdk-cache-only")));
    tokenSettings.ExpectedPlayerId=player; tokenSettings.Username=username; tokenSettings.Password=password;
    var cacheName=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes("cn-bilibili\0"+player))).ToLowerInvariant()+".bin";
    var cacheFile=Path.Combine(cacheRoot,"data","auth",cacheName);
    Require(!File.Exists(cacheFile),"synthetic_sdk_cache_does_not_preexist");
    Directory.CreateDirectory(Path.GetDirectoryName(cacheFile)!);
    File.WriteAllBytes(cacheFile,protectedProbe);
    tokenSettings.Password=password+"-changed";
    Require(!File.Exists(cacheFile),"credential_change_removes_old_sdk_session");
    File.WriteAllBytes(cacheFile,protectedProbe);
    tokenSettings.DeleteAccount(); tokenSettings.DeleteAccount();
    Require(!File.Exists(cacheFile),"account_deletion_removes_sdk_session");
    settings.Difficulty = "invalid-draft";
    settings.Username = "fixture-CRED-after-invalid-draft";
    Require(Reload().Username == settings.Username, "credential_save_independent_of_invalid_task_draft");
    settings.Difficulty = "1";
    var before = File.ReadAllBytes(file);
    var previousCiphertext = config.GetValue(PcrLabyrinthSettings.CredentialsKey, "");
    using (var locked = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read))
    {
        settings.Password = "fixture-CRED-retry";
        Require(settings.CredentialsStatus.Contains("保存失败"), "failed_save_is_reported_without_secret");
        Require(File.ReadAllBytes(file).SequenceEqual(before), "failed_save_keeps_previous_file");
        Require(config.GetValue(PcrLabyrinthSettings.CredentialsKey, "") == previousCiphertext,
            "failed_save_keeps_previous_in_memory_ciphertext");
        Require(Reload().Password == password, "failed_save_preserves_restart_password");
    }
    settings.Save();
    Require(Reload().Password == "fixture-CRED-retry", "explicit_save_retries_pending_credentials");
    config.SetValueAndPersist(PcrLabyrinthSettings.CredentialsKey, "invalid-ciphertext-fixture");
    loaded = Reload();
    Require(loaded.ExpectedPlayerId == "" && loaded.Username == "" && loaded.Password == ""
        && loaded.CredentialsStatus.Contains("无法解密"), "invalid_ciphertext_is_not_exposed");
    loaded.Save();
    Require(InstanceConfiguration.ReadValueFromFile(fixtureId, PcrLabyrinthSettings.CredentialsKey)
        == "invalid-ciphertext-fixture", "task_save_preserves_unreadable_ciphertext");
    loaded.ExpectedPlayerId = player;
    loaded.Username = username;
    loaded.Password = password;
    Require(Reload().Password == password, "explicit_credential_edit_recovers_unreadable_ciphertext");
    var view = XDocument.Load(Path.Combine(args[1], "gui", "MFAAvalonia", "MFAAvalonia", "Pcr", "PcrLabyrinthView.axaml"));
    XNamespace avalonia = "https://github.com/avaloniaui";
    var inputs = view.Descendants(avalonia + "TextBox").Where(node => new[] { "ExpectedPlayerId", "Username", "Password" }
        .Any(name => ((string?)node.Attribute("Text"))?.Contains("LabyrinthSettings." + name + ",") == true)).ToArray();
    Require(inputs.Length == 3 && inputs.All(node => (string?)node.Attribute("PasswordChar")
        == "{Binding LabyrinthSettings.CredentialMask}"), "all_three_ui_fields_use_mask_binding");
    Require(view.Descendants(avalonia + "Button").Any(node => (string?)node.Attribute("Command")
        == "{Binding LabyrinthSettings.ToggleCredentialsCommand}"), "ui_view_button_is_connected");
    var liveOwner = Owner(new InstanceConfiguration(fixtureId));
    var liveView = new PcrLabyrinthView { DataContext = liveOwner };
    Dispatcher.UIThread.RunJobs();
    var keepToggle = liveView.GetLogicalDescendants().OfType<CheckBox>().Single(control => control.Name == "KeepOldRunToggle");
    Require(keepToggle.IsChecked == false && (string?)keepToggle.Content == "保留旧局", "real_keep_old_run_control_defaults_off_with_accessible_label");
    keepToggle.SetCurrentValue(CheckBox.IsCheckedProperty, true);
    Dispatcher.UIThread.RunJobs();
    Require(liveOwner.LabyrinthSettings.KeepOldRun && Reload().KeepOldRun, "real_switch_control_change_updates_and_persists_policy");
    liveOwner.LabyrinthSettings.KeepOldRun = false;
    Dispatcher.UIThread.RunJobs();
    Require(keepToggle.IsChecked == false, "real_switch_model_update_restores_off: " + keepToggle.IsChecked);
    Require(!Reload().KeepOldRun, "real_switch_restart_restores_off: " + liveOwner.LabyrinthSettings.Status);
    Require(liveOwner.LabyrinthSettings.Status == "已自动保存黎明界设置。", "real_switch_persistence_verified_at_actual_profile_path");
    var skipAnimationToggle = liveView.GetLogicalDescendants().OfType<CheckBox>()
        .Single(control => control.Name == "SkipJoinAnimationSetupToggle");
    Require(skipAnimationToggle.IsChecked == false, "skip_join_animation_setup_defaults_off");
    skipAnimationToggle.SetCurrentValue(CheckBox.IsCheckedProperty, true);
    Dispatcher.UIThread.RunJobs();
    Require(liveOwner.LabyrinthSettings.SkipJoinAnimationSetup && Reload().SkipJoinAnimationSetup,
        "skip_join_animation_setup_ui_persists");
    Require(!liveOwner.LabyrinthSettings.Snapshot().ContainsKey("skip_join_animation_setup"),
        "skip_join_animation_setup_keeps_old_batch_snapshot_compatible");
    skipAnimationToggle.SetCurrentValue(CheckBox.IsCheckedProperty, false);
    Dispatcher.UIThread.RunJobs();
    Require(!Reload().SkipJoinAnimationSetup, "skip_join_animation_setup_can_be_saved_off");
    var liveInputs = liveView.GetLogicalDescendants().OfType<TextBox>()
        .Where(input => new[] { player, username, password }.Contains(input.Text)).ToArray();
    Require(liveInputs.Length == 3 && liveInputs.All(input => input.PasswordChar == '*'),
        "real_avalonia_controls_start_masked");
    liveOwner.LabyrinthSettings.ToggleCredentialsCommand.Execute(null);
    Dispatcher.UIThread.RunJobs();
    Require(liveInputs.All(input => input.PasswordChar == '\0'), "real_avalonia_view_action_reveals_all_fields");
    liveOwner.LabyrinthSettings.HideCredentials();
    Dispatcher.UIThread.RunJobs();
    Require(liveInputs.All(input => input.PasswordChar == '*'), "real_avalonia_hide_action_masks_all_fields");
    // 开局三人候选：图鉴/公会名单/内嵌布局/保存格式；不连接设备，也不读游戏设置。
    PcrLabyrinthCharacterCatalog.UseRepositoryRoot(args[1]);
    var rosterConfig = new InstanceConfiguration("pcr-labyrinth-rosters");
    var rosterOwner = Owner(rosterConfig);
    var rosterSettings = new PcrLabyrinthSettings(rosterOwner);
    var catalog = rosterSettings.CharacterCatalog;
    Require(catalog.Guilds.Count == 10, "opening_catalog_has_ten_guilds");
    Require(catalog.Guilds.Select(guild => guild.Characters.Count).SequenceEqual(new[] { 18, 15, 15, 12, 9, 13, 17, 11, 14, 17 }),
        "opening_catalog_guild_sizes: " + string.Join(",", catalog.Guilds.Select(guild => guild.Characters.Count)));
    Require(catalog.Guilds.Select(guild => guild.Name).SequenceEqual(new[] {
            "美食殿堂", "破晓之星", "咲恋救济院", "王宫骑士团", "拉比林斯",
            "慈乐之音", "恶魔伪王国军", "伊丽莎白牧场", "墨丘利财团", "暮光流星群" }),
        "opening_catalog_guild_names");
    var rosterCharacters = catalog.Guilds.SelectMany(guild => guild.Characters).ToArray();
    Require(rosterCharacters.Select(character => character.Id).Distinct().Count() == rosterCharacters.Length,
        "opening_catalog_units_unique_across_guilds");
    Require(rosterCharacters.All(character => character.IconPath is not null && File.Exists(character.IconPath)),
        "opening_catalog_units_have_icon_files");
    Require(catalog.Guilds[0].Characters.Any(character => character.Id == "1075" && character.Name.Contains("佩可莉姆")),
        "opening_catalog_contains_guild_one_default");
    Require(catalog.Guilds[0].Characters.Select(character => character.Id)
            .SequenceEqual(new[] { "1060", "1078", "1120", "1211", "1272", "1059", "1076", "1119", "1155", "1253",
                "1058", "1075", "1118", "1210", "1279", "1064", "1207", "1351" }),
        "opening_catalog_guild_one_matches_game_screenshot");
    var iconProbe = catalog.Guilds[0].Characters[0].Icon;
    Require(iconProbe is null || iconProbe.PixelSize.Width == 128, "opening_catalog_icon_probe");
    // 内置默认候选与公会附赠角色只用于 GUI 回显；未自定义时 Python 使用同一份默认（Python 回归校验一致）。
    Require(catalog.Guilds.Sum(guild => guild.Defaults.Count) == 30
        && catalog.Guilds.All(guild => guild.Defaults.Count == 3 && guild.Defaults.All(slot => slot.Count > 0)),
        "opening_catalog_builtin_defaults_cover_three_slots_per_guild");
    Require(catalog.Guilds[1].Defaults[2].SequenceEqual(new[] { "1003", "1801", "1225" }),
        "opening_catalog_upstream_defaults_parsed: " + string.Join(",", catalog.Guilds[1].Defaults[2]));
    Require(catalog.Guilds[4].Granted.SequenceEqual(new[] { "1068" })
        && catalog.Guilds.Where(guild => guild.GuildId != 5).All(guild => guild.Granted.Count == 0),
        "opening_catalog_granted_character_parsed");
    Require(catalog.DisplayName("1801").Contains("日和") && catalog.DisplayName("9999") == "9999",
        "opening_catalog_default_plan_names_resolve");
    // strategy 快照携带每个公会的完整角色 ID 名单（guild_units），供 Python 做名单限定的整页一对一分配；
    // 该字段只由图鉴生成，不写回加载路径。
    var snapshotStrategy = rosterSettings.Snapshot()["strategy"] as JObject;
    var snapshotGuildUnits = snapshotStrategy?["guild_units"] as JObject;
    Require(snapshotGuildUnits is not null && snapshotGuildUnits.Count == 10,
        "guild_units_snapshot_has_ten_guilds: " + snapshotStrategy?["guild_units"]?.ToString(Newtonsoft.Json.Formatting.None));
    Require(snapshotGuildUnits!.Properties().Select(property => property.Name).SequenceEqual(new[] { "1", "2", "3", "4", "5", "6", "7", "8", "9", "10" }),
        "guild_units_snapshot_keys_are_guild_ids");
    var snapshotGuildArrays = snapshotGuildUnits.Properties().Select(property => property.Value as JArray).ToArray();
    Require(snapshotGuildArrays.All(array => array is not null)
        && snapshotGuildArrays.Select(array => array!.Count).SequenceEqual(new[] { 18, 15, 15, 12, 9, 13, 17, 11, 14, 17 }),
        "guild_units_snapshot_guild_sizes: " + string.Join(",", snapshotGuildArrays.Select(array => array?.Count)));
    Require(snapshotGuildArrays.Select((array, index) => array!.Values<string>()
            .SequenceEqual(catalog.Guilds[index].Characters.Select(character => character.Id))).All(matches => matches),
        "guild_units_snapshot_matches_catalog");
    var snapshotGuildIds = snapshotGuildArrays.SelectMany(array => array!.Values<string>()).ToArray();
    Require(snapshotGuildIds.Length == 141 && snapshotGuildIds.Distinct().Count() == snapshotGuildIds.Length,
        "guild_units_snapshot_ids_unique_across_guilds: " + snapshotGuildIds.Length);
    // 1–10 号公会三个槽位留空都等于沿用上游默认（6–10 号默认候选于 2026-11-13 从上游同步）。
    var newGuildConfig = new InstanceConfiguration("pcr-labyrinth-new-guild-roster");
    var newGuildSettings = new PcrLabyrinthSettings(Owner(newGuildConfig));
    Require(newGuildSettings.Goals.Select(goal => goal.GuildId).SequenceEqual(Enumerable.Range(1, 10)),
        "opening_goals_cover_guild_ids_one_to_ten");
    Require(newGuildSettings.Goals.Select(goal => goal.Name).SequenceEqual(new[] {
            "美食殿堂", "破晓之星", "咲恋救济院", "王宫骑士团", "拉比林斯",
            "慈乐之音", "恶魔伪王国军", "伊丽莎白牧场", "墨丘利财团", "暮光流星群" }),
        "opening_goal_names_match_guild_selection_order");
    foreach (var newGoal in newGuildSettings.Goals)
    {
        var wasEnabled = newGoal.Enabled;
        newGoal.Enabled = true;
        Require(newGuildSettings.Snapshot()["strategy"]?["opening_rosters"] is JObject { Count: 0 },
            "opening_empty_slots_keep_upstream_default_for_guild_" + newGoal.GuildId);
        newGoal.Enabled = wasEnabled;
    }
    // 未自定义时 GUI 回显内置默认：逐槽列出实际会使用的默认角色，5 号公会显示附赠角色。
    var defaultPlanGoal = newGuildSettings.Goals[1];
    Require(defaultPlanGoal.PlanLabel == "使用内置默认" && defaultPlanGoal.UsingBuiltInPlan && !defaultPlanGoal.HasCustomRoster,
        "opening_builtin_plan_label_for_empty_slots");
    Require(defaultPlanGoal.DefaultPlanText.Contains("怜") && defaultPlanGoal.DefaultPlanText.Contains("优衣")
        && defaultPlanGoal.DefaultPlanText.Contains("日和莉（公主）（当前公会不可选）")
        && defaultPlanGoal.DefaultPlanText.Split("；").Length == 3,
        "opening_builtin_default_plan_echo: " + defaultPlanGoal.DefaultPlanText);
    Require(!defaultPlanGoal.HasGrantedText, "opening_guild_without_grant_has_no_granted_echo");
    var grantedGoal = newGuildSettings.Goals[4];
    Require(grantedGoal.HasGrantedText && grantedGoal.GrantedText.Contains("菈比莉斯塔"),
        "opening_guild_five_granted_character_echo: " + grantedGoal.GrantedText);
    // 6 号公会选满三个槽位后可以正常保存、重开与开始。
    var newGuildGoal = newGuildSettings.Goals[5];
    Require(!newGuildGoal.DefaultPlanText.Contains("不可选"),
        "opening_default_plan_marks_only_unselectable: " + newGuildGoal.DefaultPlanText);
    Require(newGuildGoal.Pool.Count == 13, "opening_guild_six_pool_size: " + newGuildGoal.Pool.Count);
    var newGuildPicks = newGuildGoal.Pool.Take(3).Select(character => character.Id).ToArray();
    newGuildGoal.Pool.First(character => character.Id == newGuildPicks[0]).ToggleCommand!.Execute(null);
    Require(newGuildGoal.PlanLabel == "已自定义" && !newGuildGoal.UsingBuiltInPlan,
        "opening_custom_plan_label_after_pick: " + newGuildGoal.PlanLabel);
    Require(newGuildSettings.Status.Contains("三个槽位都至少选择一个角色"),
        "opening_new_guild_partial_pick_blocked: " + newGuildSettings.Status);
    foreach (var id in newGuildPicks.Skip(1))
        newGuildGoal.Pool.First(character => character.Id == id).ToggleCommand!.Execute(null);
    newGuildGoal.Enabled = true;
    newGuildSettings.Save();
    Require(!newGuildSettings.Status.StartsWith("保存失败"), "opening_new_guild_roster_saves: " + newGuildSettings.Status);
    var savedNewGuild = JObject.Parse(newGuildConfig.GetValue(PcrLabyrinthSettings.Key, ""));
    Require(savedNewGuild["goals"] is JArray savedNewGoals
        && savedNewGoals.OfType<JObject>().Any(row => (int?)row["guild_id"] == 6),
        "opening_new_guild_goal_saved_as_goal");
    var savedNewRoster = savedNewGuild["strategy"]?["opening_rosters"]?["6"] as JArray;
    Require(savedNewRoster is not null && savedNewRoster.Count == 3
        && string.Join("/", savedNewRoster.Select(slot => string.Join(",", slot.Values<string>()))) == string.Join("/", newGuildPicks),
        "opening_new_guild_roster_saved: " + savedNewRoster);
    var restoredNewGuild = new PcrLabyrinthSettings(Owner(newGuildConfig));
    Require(restoredNewGuild.Goals[5].Enabled && restoredNewGuild.Goals[5].HasCustomRoster
        && restoredNewGuild.Goals[5].Slots.SelectMany(slot => slot.Candidates).Select(character => character.Id).SequenceEqual(newGuildPicks),
        "opening_new_guild_roster_restored: " + string.Join("/", restoredNewGuild.Goals[5].Slots.Select(slot => slot.HeaderText)));
    Require(restoredNewGuild.Snapshot()["strategy"]?["opening_rosters"]?["6"] is JArray,
        "opening_new_guild_roster_snapshot_after_restore");
    var rosterView = new PcrLabyrinthView { DataContext = rosterOwner };
    Dispatcher.UIThread.RunJobs();
    var rosterXaml = XDocument.Load(Path.Combine(args[1], "gui", "MFAAvalonia", "MFAAvalonia", "Pcr", "PcrLabyrinthView.axaml"));
    XNamespace xaml = "https://github.com/avaloniaui";
    string[] ClassesOf(XElement node) => ((string?)node.Attribute("Classes"))?.Split(' ', StringSplitOptions.RemoveEmptyEntries) ?? [];
    var rosterExpanderNodes = rosterXaml.Descendants(xaml + "Expander")
        .Where(node => (string?)node.Attribute("Header") == "各公会开局三人候选").ToArray();
    Require(rosterExpanderNodes.Length == 1, "opening_roster_expander_present");
    var rosterExpanderNode = rosterExpanderNodes[0];
    Require(rosterExpanderNode.Descendants(xaml + "Border").Any(node => ClassesOf(node).Contains("OpeningSlotBox")),
        "opening_slot_boxes_in_view");
    Require(rosterExpanderNode.Descendants(xaml + "Button").Any(node =>
            ClassesOf(node).Contains("CandidateChip") && (string?)node.Attribute("Command") == "{Binding ToggleCommand}"),
        "opening_candidate_chips_bound_in_view");
    Require(rosterExpanderNode.Descendants(xaml + "ItemsControl").Any(node => (string?)node.Attribute("ItemsSource") == "{Binding Slots}"),
        "opening_slots_bound_in_view");
    Require(rosterExpanderNode.Descendants(xaml + "ItemsControl").Any(node => (string?)node.Attribute("ItemsSource") == "{Binding Pool}"),
        "opening_pool_bound_in_view");
    Require(rosterExpanderNode.Descendants(xaml + "TextBlock").Any(node => (string?)node.Attribute("Text") == "{Binding PlanLabel}"),
        "opening_builtin_plan_label_bound_in_view");
    Require(rosterExpanderNode.Descendants(xaml + "TextBlock").Any(node => (string?)node.Attribute("Text") == "{Binding DefaultPlanText}"),
        "opening_builtin_default_plan_bound_in_view");
    Require(rosterExpanderNode.Descendants(xaml + "TextBlock").Any(node => (string?)node.Attribute("Text") == "{Binding GrantedText}"),
        "opening_granted_character_bound_in_view");
    Require(!rosterExpanderNode.Descendants(xaml + "TextBox").Any(), "opening_no_text_input_in_roster_ui");
    Require(!rosterExpanderNode.Descendants().Any(node => (string?)node.Attribute("Name") == "OpeningPickerPanel"),
        "opening_popup_removed_from_view");
    Require(rosterExpanderNode.Descendants(xaml + "TextBlock").Any(node =>
            ((string?)node.Attribute("Text"))?.Contains("逐槽显示默认角色") == true),
        "opening_default_roster_hint_in_view");
    Require(rosterSettings.Goals.Sum(goal => goal.Slots.Count) == 30, "opening_goals_have_three_slots_each");
    Require(rosterSettings.Goals.Select(goal => goal.Pool.Count).SequenceEqual(new[] { 18, 15, 15, 12, 9, 13, 17, 11, 14, 17 }),
        "opening_goal_pools_match_guild_sizes");
    var rosterGoal = rosterSettings.Goals[0];
    PcrLabyrinthCharacter Pick(string id) => rosterGoal.Pool.First(character => character.Id == id);
    Require(rosterGoal.Slots.All(slot => slot.IsEmpty) && rosterGoal.Slots.All(slot => slot.HeaderText == "未选"),
        "opening_slots_start_empty");
    Pick("1075").ToggleCommand!.Execute(null);
    Require(rosterGoal.Slots[0].Candidates.Count == 1 && rosterGoal.Slots[0].Candidates[0].Id == "1075"
        && rosterGoal.Slots[0].HeaderText.Contains("佩可莉姆（夏日）"), "opening_pick_fills_first_slot");
    Require(Pick("1075").IsSelected && Pick("1075").Badge == "1", "opening_picked_chip_marked_with_slot");
    Pick("1351").ToggleCommand!.Execute(null);
    Pick("1059").ToggleCommand!.Execute(null);
    Require(rosterGoal.Slots[1].Candidates.Single().Id == "1351" && rosterGoal.Slots[2].Candidates.Single().Id == "1059",
        "opening_pick_fills_three_slots");
    Pick("1060").ToggleCommand!.Execute(null);
    Require(rosterGoal.HasRosterHint && rosterGoal.Slots.All(slot => slot.Candidates.Count == 1), "opening_fourth_pick_rejected");
    Pick("1075").ToggleCommand!.Execute(null);
    Require(rosterGoal.Slots[0].IsEmpty && !Pick("1075").IsSelected && !rosterGoal.HasRosterHint, "opening_toggle_removes_pick");
    Pick("1075").ToggleCommand!.Execute(null);
    Require(rosterGoal.Slots[0].Candidates.Single().Id == "1075", "opening_refill_after_remove");
    rosterSettings.Save();
    var savedRosterJson = JObject.Parse(rosterConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["opening_rosters"] as JObject;
    Require(savedRosterJson?["1"] is JArray savedSlots && savedSlots.Count == 3
        && string.Join("/", savedSlots.Select(slot => string.Join(",", slot.Values<string>()))) == "1075/1351/1059",
        "opening_roster_saved_legacy_format: " + savedRosterJson);
    var savedGuildUnits = JObject.Parse(rosterConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["guild_units"] as JObject;
    Require(savedGuildUnits is not null && savedGuildUnits.Count == 10,
        "guild_units_persisted_in_saved_strategy: " + savedGuildUnits?.ToString(Newtonsoft.Json.Formatting.None));
    var restored = new PcrLabyrinthSettings(Owner(rosterConfig));
    Require(restored.Goals[0].Slots.Select(slot => string.Join(",", slot.Candidates.Select(character => character.Name)))
            .SequenceEqual(new[] { "佩可莉姆（夏日）", "雪菲（夏日）", "可可萝" }),
        "opening_roster_restored_names: " + string.Join(" / ", restored.Goals[0].Slots.Select(slot => slot.HeaderText)));
    var restoredPool = restored.Goals[0].Pool;
    Require(restoredPool.First(character => character.Id == "1075") is { IsSelected: true, Badge: "1" }
        && restoredPool.First(character => character.Id == "1351") is { IsSelected: true, Badge: "2" }
        && restoredPool.First(character => character.Id == "1059") is { IsSelected: true, Badge: "3" },
        "opening_roster_restored_chips_highlighted");
    foreach (var id in new[] { "1075", "1351", "1059" })
        restored.Goals[0].Pool.First(character => character.Id == id).ToggleCommand!.Execute(null);
    Dispatcher.UIThread.RunJobs();
    restored.Save();
    var clearedRosterJson = JObject.Parse(rosterConfig.GetValue(PcrLabyrinthSettings.Key, ""));
    Require(clearedRosterJson["strategy"]?["opening_rosters"] is JObject { Count: 0 },
        "opening_roster_cleared_saved_empty: "
        + clearedRosterJson["strategy"]?["opening_rosters"]?.ToString(Newtonsoft.Json.Formatting.None)
        + " slots=" + string.Join(",", restored.Goals.Select(goal => string.Join("|", goal.Slots.Select(slot => slot.Candidates.Count)))));
    // 旧配置的多候选优先级仍可读取、逐个移除，并按原顺序保存。
    var legacyRosterConfig = new InstanceConfiguration("pcr-labyrinth-legacy-roster");
    legacyRosterConfig.SetValueAndPersist(PcrLabyrinthSettings.Key,
        new JObject { ["goals"] = new JArray(new JObject { ["guild_id"] = 3, ["clears"] = 1 }),
            ["strategy"] = new JObject { ["opening_rosters"] = new JObject {
                ["3"] = new JArray(new JArray("1025", "1077"), new JArray("1121"), new JArray("1308")) } } }.ToString(Newtonsoft.Json.Formatting.None));
    var legacyRoster = new PcrLabyrinthSettings(Owner(legacyRosterConfig));
    var legacyGoal = legacyRoster.Goals[2];
    Require(legacyGoal.Slots[0].Candidates.Select(character => character.Id).SequenceEqual(new[] { "1025", "1077" })
        && legacyGoal.Slots[0].HeaderText.Contains("铃莓") && legacyGoal.Slots[0].HeaderText.Contains("铃莓（夏日）"),
        "opening_legacy_priority_list_read");
    Require(legacyGoal.Slots[0].Candidates[0].ToggleCommand is not null, "opening_loaded_command_ready_before_pool");
    legacyGoal.Slots[0].Candidates[0].ToggleCommand!.Execute(null);
    Require(legacyGoal.Slots[0].Candidates.Single().Id == "1077", "opening_legacy_priority_item_removed");
    legacyRoster.Save();
    var legacySaved = JObject.Parse(legacyRosterConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["opening_rosters"]?["3"] as JArray;
    Require(legacySaved is not null && string.Join("/", legacySaved.Select(slot => string.Join(",", slot.Values<string>()))) == "1077/1121/1308",
        "opening_legacy_priority_saved: " + legacySaved);
    var legacySavedStrategy = JObject.Parse(legacyRosterConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"] as JObject;
    Require(legacySavedStrategy?["guild_units"] is JObject { Count: 10 },
        "guild_units_present_for_legacy_config: " + legacySavedStrategy);
    // 图鉴之外的旧 ID 保留为占位项：可显示、不可加入。
    var unknownRosterConfig = new InstanceConfiguration("pcr-labyrinth-unknown-roster");
    unknownRosterConfig.SetValueAndPersist(PcrLabyrinthSettings.Key,
        new JObject { ["goals"] = new JArray(new JObject { ["guild_id"] = 3, ["clears"] = 1 }),
            ["strategy"] = new JObject { ["opening_rosters"] = new JObject {
                ["3"] = new JArray(new JArray("1025"), new JArray("9999"), new JArray("1145")) } } }.ToString(Newtonsoft.Json.Formatting.None));
    var unknownRoster = new PcrLabyrinthSettings(Owner(unknownRosterConfig));
    var unknownGoal = unknownRoster.Goals[2];
    var placeholder = unknownGoal.Slots[1].Candidates.Single();
    Require(placeholder.Id == "9999" && placeholder.IsPlaceholder && unknownGoal.Slots[1].HeaderText.Contains("未列入预置名单"),
        "opening_unknown_saved_id_kept_as_placeholder");
    Require(placeholder.ToggleCommand is not null, "opening_placeholder_has_remove_command");
    placeholder.ToggleCommand!.Execute(null);
    Require(unknownGoal.Slots[1].IsEmpty, "opening_placeholder_can_be_removed");
    unknownGoal.Toggle(placeholder);
    Require(unknownGoal.Slots[1].IsEmpty, "opening_unselected_placeholder_cannot_be_added");
    foreach (var id in new[] { "1801", "9999" }) {
        var retainedConfig = new InstanceConfiguration("pcr-labyrinth-retained-" + id);
        var originalSlots = new JArray(new JArray("1089", "1003"), new JArray("1088"), new JArray(id));
        retainedConfig.SetValueAndPersist(PcrLabyrinthSettings.Key,
            new JObject { ["goals"] = new JArray(new JObject { ["guild_id"] = 2, ["clears"] = 1 }),
                ["strategy"] = new JObject { ["opening_rosters"] = new JObject { ["2"] = originalSlots } } }
                .ToString(Newtonsoft.Json.Formatting.None));
        var retained = new PcrLabyrinthSettings(Owner(retainedConfig));
        var retainedGoal = retained.Goals[1];
        Require(retainedGoal.Slots.SelectMany(slot => slot.Candidates).All(character => character.ToggleCommand is not null),
            "opening_all_retained_commands_ready_" + id);
        retained.Save();
        Require(JToken.DeepEquals(JObject.Parse(retainedConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["opening_rosters"]?["2"], originalSlots),
            "opening_unedited_retained_ids_and_order_preserved_" + id);
        retainedGoal.Slots[0].Candidates[0].ToggleCommand!.Execute(null);
        retained.Save();
        var reloaded = new PcrLabyrinthSettings(Owner(retainedConfig));
        Require(reloaded.Goals[1].Slots[0].Candidates.Single().Id == "1003"
            && reloaded.Goals[1].Slots[2].Candidates.Single().Id == id, "opening_remove_save_reload_keeps_other_ids_" + id);
        foreach (var slot in reloaded.Goals[1].Slots)
            foreach (var character in slot.Candidates.ToArray()) character.ToggleCommand!.Execute(null);
        reloaded.Save();
        Require(JObject.Parse(retainedConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["opening_rosters"] is JObject { Count: 0 },
            "opening_retained_roster_clears_to_default_" + id);
    }
    PcrLabyrinthCharacterCatalog.UseRepositoryRoot(Path.Combine(root, "missing-character-pack"));
    var unavailable = new PcrLabyrinthSettings(Owner(legacyRosterConfig));
    Require(unavailable.HasRosterStatus && unavailable.Goals[2].Pool.Count == 0, "opening_missing_catalog_visible");
    Require(unavailable.Goals[2].Slots.SelectMany(slot => slot.Candidates).All(character => character.ToggleCommand is not null),
        "opening_missing_catalog_keeps_remove_commands");
    unavailable.Save();
    Require(JToken.DeepEquals(JObject.Parse(legacyRosterConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["opening_rosters"]?["3"], legacySaved),
        "opening_missing_catalog_preserves_saved_ids");
    foreach (var slot in unavailable.Goals[2].Slots)
        foreach (var character in slot.Candidates.ToArray()) character.ToggleCommand!.Execute(null);
    unavailable.Save();
    Require(JObject.Parse(legacyRosterConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["opening_rosters"] is JObject { Count: 0 },
        "opening_missing_catalog_can_clear_to_default");
    PcrLabyrinthCharacterCatalog.UseRepositoryRoot(args[1]);
    Require(rosterExpanderNode.Descendants(xaml + "ItemsControl").Any(node => (string?)node.Attribute("ItemsSource") == "{Binding Candidates}")
        && rosterExpanderNode.Descendants(xaml + "Button").Any(node => ClassesOf(node).Contains("OpeningSelected")
            && (string?)node.Attribute("Command") == "{Binding ToggleCommand}"), "opening_saved_candidates_have_remove_buttons");
    var guiConfig = new InstanceConfiguration("pcr-labyrinth-roster-buttons");
    guiConfig.SetValueAndPersist(PcrLabyrinthSettings.Key,
        new JObject { ["goals"] = new JArray(new JObject { ["guild_id"] = 2, ["clears"] = 1 }),
            ["strategy"] = new JObject { ["opening_rosters"] = new JObject {
                ["2"] = new JArray(new JArray("1089"), new JArray("1088"), new JArray("1801")) } } }.ToString(Newtonsoft.Json.Formatting.None));
    var guiOwner = Owner(guiConfig);
    var guiView = new PcrLabyrinthView { DataContext = guiOwner, Background = Brushes.White };
    var guiExpander = guiView.GetLogicalDescendants().OfType<Expander>().Single(control => (string?)control.Header == "各公会开局三人候选");
    guiExpander.IsExpanded = true;
    var guiWindow = new Window { Content = guiView, Width = 1100, Height = 1400, ShowActivated = false,
        ShowInTaskbar = false, Position = new PixelPoint(-12000, -12000) };
    try
    {
        guiWindow.Show();
        Dispatcher.UIThread.RunJobs();
        guiView.UpdateLayout();
        guiExpander.BringIntoView();
        Dispatcher.UIThread.RunJobs();
        using (var frame = new RenderTargetBitmap(new PixelSize(1100, 1400))) {
            frame.Render(guiView); frame.Save(Path.Combine(root, "opening-roster-before.png"));
        }
        Button[] SelectedButtons() => guiView.GetLogicalDescendants().OfType<Button>()
            .Where(button => button.Classes.Contains("OpeningSelected")).ToArray();
        var actualButtons = SelectedButtons();
        Require(actualButtons.Length == 3 && actualButtons.All(button => button.Command?.CanExecute(button.CommandParameter) == true),
            "opening_actual_saved_buttons_are_enabled");
        var click = typeof(Button).GetMethod("OnClick", BindingFlags.Instance | BindingFlags.NonPublic)!;
        click.Invoke(actualButtons.Single(button => button.DataContext is PcrLabyrinthCharacter { Id: "1801" }), null);
        Dispatcher.UIThread.RunJobs();
        Require(guiOwner.LabyrinthSettings.Goals[1].Slots[2].IsEmpty, "opening_actual_button_removes_retained_placeholder");
        foreach (var button in SelectedButtons()) click.Invoke(button, null);
        Dispatcher.UIThread.RunJobs();
        Require(JObject.Parse(guiConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["opening_rosters"] is JObject { Count: 0 },
            "opening_actual_buttons_clear_and_persist_default");
        Require(!guiOwner.LabyrinthSettings.Goals[1].Pool.Any(character => character.IsSelected), "opening_actual_buttons_clear_pool_selection");
        System.Threading.Thread.Sleep(250);
        Dispatcher.UIThread.RunJobs();
        guiView.UpdateLayout();
        using var after = new RenderTargetBitmap(new PixelSize(1100, 1400));
        after.Render(guiView); after.Save(Path.Combine(root, "opening-roster-after.png"));
    }
    finally { guiWindow.Close(); Dispatcher.UIThread.RunJobs(); }
    Require(!File.ReadAllText(Path.Combine(args[1], "gui", "MFAAvalonia", "MFAAvalonia", "Pcr", "PcrLabyrinthView.axaml")).Contains("OpeningRoster"),
        "opening_legacy_textbox_removed_from_view");
    Require(!MaaProcessorManager.IsInstanceCreated && !Directory.EnumerateFiles(root, "*.tmp", SearchOption.AllDirectories).Any(),
        "no_device_manager_or_atomic_temporary_file_left");
    Console.WriteLine($"PCR credential checks passed: {count}; network logins=0; game inputs=0; isolated config={root}");
    return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine("PCR credential checks failed: " + error);
    return 1;
}
