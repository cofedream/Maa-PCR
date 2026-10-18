using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Xml.Linq;
using Avalonia.Controls;
using Avalonia.LogicalTree;
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
    Require(liveOwner.LabyrinthSettings.Status == "黎明界设置已保存。", "real_switch_persistence_verified_at_actual_profile_path");
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
    Require(catalog.Guilds.Count == 5, "opening_catalog_has_five_guilds");
    Require(catalog.Guilds.Select(guild => guild.Characters.Count).SequenceEqual(new[] { 18, 15, 15, 12, 9 }),
        "opening_catalog_guild_sizes: " + string.Join(",", catalog.Guilds.Select(guild => guild.Characters.Count)));
    Require(catalog.Guilds.Select(guild => guild.Name).SequenceEqual(new[] { "美食殿堂", "破晓之星", "咲恋救济院", "王宫骑士团", "拉比林斯" }),
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
            ClassesOf(node).Contains("OpeningCandidate") && (string?)node.Attribute("Command") == "{Binding ToggleCommand}"),
        "opening_candidate_chips_bound_in_view");
    Require(rosterExpanderNode.Descendants(xaml + "ItemsControl").Any(node => (string?)node.Attribute("ItemsSource") == "{Binding Slots}"),
        "opening_slots_bound_in_view");
    Require(rosterExpanderNode.Descendants(xaml + "ItemsControl").Any(node => (string?)node.Attribute("ItemsSource") == "{Binding Pool}"),
        "opening_pool_bound_in_view");
    Require(!rosterExpanderNode.Descendants(xaml + "TextBox").Any(), "opening_no_text_input_in_roster_ui");
    Require(!rosterExpanderNode.Descendants().Any(node => (string?)node.Attribute("Name") == "OpeningPickerPanel"),
        "opening_popup_removed_from_view");
    Require(rosterSettings.Goals.Sum(goal => goal.Slots.Count) == 15, "opening_goals_have_three_slots_each");
    Require(rosterSettings.Goals.Select(goal => goal.Pool.Count).SequenceEqual(new[] { 18, 15, 15, 12, 9 }),
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
    legacyGoal.Pool.First(character => character.Id == "1025").ToggleCommand!.Execute(null);
    Require(legacyGoal.Slots[0].Candidates.Single().Id == "1077", "opening_legacy_priority_item_removed");
    legacyRoster.Save();
    var legacySaved = JObject.Parse(legacyRosterConfig.GetValue(PcrLabyrinthSettings.Key, ""))["strategy"]?["opening_rosters"]?["3"] as JArray;
    Require(legacySaved is not null && string.Join("/", legacySaved.Select(slot => string.Join(",", slot.Values<string>()))) == "1077/1121/1308",
        "opening_legacy_priority_saved: " + legacySaved);
    // 图鉴之外的旧 ID 保留为占位项：可显示、不可加入。
    var unknownRosterConfig = new InstanceConfiguration("pcr-labyrinth-unknown-roster");
    unknownRosterConfig.SetValueAndPersist(PcrLabyrinthSettings.Key,
        new JObject { ["goals"] = new JArray(new JObject { ["guild_id"] = 3, ["clears"] = 1 }),
            ["strategy"] = new JObject { ["opening_rosters"] = new JObject {
                ["3"] = new JArray(new JArray("1025"), new JArray("9999"), new JArray("1145")) } } }.ToString(Newtonsoft.Json.Formatting.None));
    var unknownRoster = new PcrLabyrinthSettings(Owner(unknownRosterConfig));
    var unknownGoal = unknownRoster.Goals[2];
    var placeholder = unknownGoal.Slots[1].Candidates.Single();
    Require(placeholder.Id == "9999" && placeholder.IsPlaceholder && unknownGoal.Slots[1].HeaderText.Contains("未在图鉴中"),
        "opening_unknown_saved_id_kept_as_placeholder");
    unknownGoal.Toggle(placeholder);
    Require(unknownGoal.Slots[1].Candidates.Single() == placeholder, "opening_placeholder_cannot_be_toggled");
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
