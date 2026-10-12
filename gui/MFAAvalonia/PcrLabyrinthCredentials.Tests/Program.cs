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
        typeof(MaaProcessor).GetField("<InstanceId>k__BackingField", fields)!.SetValue(processor, "pcr-labyrinth-v1");
        typeof(MaaProcessor).GetField("<InstanceConfiguration>k__BackingField", fields)!.SetValue(processor, config);
        var owner = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
        typeof(TaskQueueViewModel).GetField("_processorField", fields)!.SetValue(owner, processor);
        return owner;
    }
    PcrLabyrinthSettings Reload() => new(Owner(new InstanceConfiguration("credential-fixture")));
    var config = new InstanceConfiguration("credential-fixture");
    var firstOwner = Owner(config);
    Require(firstOwner.CanEditPcrTarget, "isolated_owner_allows_settings_edits");
    var probe = Encoding.UTF8.GetBytes("DPAPI-fixture-only");
    var protectedProbe = ProtectedData.Protect(probe, null, DataProtectionScope.CurrentUser);
    var decodedProbe = ProtectedData.Unprotect(protectedProbe, null, DataProtectionScope.CurrentUser);
    try { Require(probe.SequenceEqual(decodedProbe), "windows_current_user_dpapi_available"); }
    finally { CryptographicOperations.ZeroMemory(probe); CryptographicOperations.ZeroMemory(decodedProbe); }
    var settings = new PcrLabyrinthSettings(firstOwner);
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
    Require(InstanceConfiguration.ReadValueFromFile("credential-fixture", PcrLabyrinthSettings.CredentialsKey)
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
    var liveOwner = Owner(new InstanceConfiguration("credential-fixture"));
    var liveView = new PcrLabyrinthView { DataContext = liveOwner };
    Dispatcher.UIThread.RunJobs();
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
