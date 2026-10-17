using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MFAAvalonia.Configuration;
using MFAAvalonia.Helper;
using Newtonsoft.Json.Linq;

internal static class PersistenceRecoveryChecks
{
    internal static void Run(string directory)
    {
        const BindingFlags fields = BindingFlags.Static | BindingFlags.NonPublic;
        var configDirectory = typeof(AppPaths).GetField("_configDirectory", fields)!;
        var initialized = typeof(AppPaths).GetField("_initialized", fields)!;
        var previousDirectory = configDirectory.GetValue(null);
        var previousInitialized = initialized.GetValue(null);
        var root = Path.Combine(directory, "config-recovery-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root, "config", "instances"));
        configDirectory.SetValue(null, Path.Combine(root, "config"));
        initialized.SetValue(null, true);
        try
        {
            InstanceChecks(root);
            GlobalChecks(root);
            Console.WriteLine("PCR configuration recovery checks passed: locked/malformed reads, reload, atomic failure, retained credentials/settings/records; game inputs=0");
        }
        finally
        {
            configDirectory.SetValue(null, previousDirectory);
            initialized.SetValue(null, previousInitialized);
        }
    }

    private static void Require(bool value, string name)
    {
        if (!value) throw new InvalidOperationException(name);
    }

    private static void Refused(Action operation, string name)
    {
        try { operation(); }
        catch (IOException) { return; }
        catch (Newtonsoft.Json.JsonException) { return; }
        throw new InvalidOperationException(name);
    }

    internal static void InstanceChecks(string root)
    {
        var file = Path.Combine(root, "config", "instances", "read-failure.json");
        var original = "{\"PcrLabyrinthCredentialsV1\":\"synthetic-ciphertext\",\"PcrLabyrinthSettingsV1\":{\"goals\":[1,2]},\"unknown-field\":\"keep\"}";
        var history = Path.Combine(root, "transactions.jsonl");
        File.WriteAllText(history, "{\"state\":\"in_flight\",\"synthetic\":true}\n");
        var historyBytes = File.ReadAllBytes(history);
        File.WriteAllText(file, original);
        using (var held = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.None))
            Refused(() => _ = new InstanceConfiguration("read-failure"), "unreadable_profile_became_writable_empty_config");
        Require(File.ReadAllText(file) == original, "failed_load_changed_original_bytes");
        var loaded = new InstanceConfiguration("read-failure");
        using (var held = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.None))
            Refused(loaded.ReloadFromDisk, "failed_reload_was_silently_accepted");
        loaded.SetValueAndPersist("ordinary-checkbox", true);
        var after = JObject.Parse(File.ReadAllText(file));
        foreach (var item in JObject.Parse(original))
            Require(JToken.DeepEquals(after[item.Key], item.Value), "ordinary_save_lost_original_field:" + item.Key);
        foreach (var invalid in new[] { "{\"truncated\":", "null", "[]", "" })
        {
            File.WriteAllText(file, invalid);
            Refused(() => _ = new InstanceConfiguration("read-failure"), "malformed_profile_accepted:" + invalid);
            Require(File.ReadAllText(file) == invalid, "malformed_profile_was_replaced");
        }
        File.WriteAllText(file, original);
        loaded.ReloadFromDisk();
        loaded.SetValues(new Dictionary<string, object> { ["batch-option"] = 2 });
        loaded.RemoveValue("batch-option");
        Require(JToken.DeepEquals(JObject.Parse(File.ReadAllText(file)), JObject.Parse(original)), "valid_reload_or_mutation_lost_values");
        var fresh = new InstanceConfiguration("new-profile");
        fresh.SetValueAndPersist("created", true);
        Require(File.Exists(fresh.GetConfigFilePath()), "missing_profile_can_no_longer_be_created");
        Require(File.ReadAllBytes(history).SequenceEqual(historyBytes), "unknown_run_record_was_modified");
    }

    internal static void GlobalChecks(string root)
    {
        var file = Path.Combine(root, "appsettings.json");
        foreach (var invalid in new[] { "{\"InstanceList\":", "null", "[]", "" })
        {
            File.WriteAllText(file, invalid);
            _ = GlobalConfiguration.GetValue("InstanceList", "fallback");
            GlobalConfiguration.SetValue("InstanceList", "pcr-alpha-v1,pcr-labyrinth-v1");
            GlobalConfiguration.RemoveValues(new[] { "InstanceList" });
            Require(File.ReadAllText(file) == invalid && GlobalConfiguration.HasFileAccessError,
                "damaged_global_config_not_preserved");
        }
        const string valid = "{\"keep\":\"untouched\",\"remove\":\"yes\",\"nullable\":null}";
        File.WriteAllText(file, valid);
        using (var held = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            GlobalConfiguration.SetValue("new", "value");
            GlobalConfiguration.RemoveValues(new[] { "remove" });
            Require(File.ReadAllText(file) == valid, "failed_atomic_global_write_changed_original");
        }
        GlobalConfiguration.SetValue("new", "value");
        GlobalConfiguration.RemoveValues(new[] { "remove" });
        var after = JObject.Parse(File.ReadAllText(file));
        Require((string?)after["new"] == "value" && (string?)after["keep"] == "untouched"
            && after.ContainsKey("nullable") && after["nullable"]!.Type == JTokenType.Null
            && !after.ContainsKey("remove"), "repaired_global_config_not_safely_writable");
        Require(!Directory.EnumerateFiles(root, "*.tmp", SearchOption.AllDirectories).Any(), "atomic_temp_files_left");
    }
}
