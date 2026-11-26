using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MFAAvalonia.Configuration;
using MFAAvalonia.Extensions;
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
            InstanceConcurrencyChecks.Run(root);
            StartupSettingsChecks();
            CheckConcurrentGlobalReads(root);
            GlobalChecks(root);
            GlobalErrorFormattingCheck();
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

    private static void StartupSettingsChecks()
    {
        var profiles = new[] { "pcr-alpha-v1", "pcr-labyrinth-v1", MFAAvalonia.Pcr.PcrClanGuide.ProfileId };
        var options = new[]
        {
            (ConfigurationKeys.BeforeTask, new[] { "None", "StartupSoftware", "StartupSoftwareAndScript", "StartupScriptOnly" }),
            (ConfigurationKeys.AfterTask, new[] { "None", "CloseMFA", "CloseEmulator", "CloseEmulatorAndMFA",
                "ShutDown", "ShutDownOnce", "CloseEmulatorAndRestartMFA", "RestartPC" })
        };
        foreach (var id in profiles)
        {
            var config = new InstanceConfiguration(id);
            foreach (var (key, values) in options)
            {
                Require(config.GetValue(key, "None") == "None", "startup_default:" + id + ":" + key);
                foreach (var value in values)
                {
                    config.SetValue(key, value);
                    Require((string?)JObject.Parse(File.ReadAllText(config.GetConfigFilePath()))[key] == value,
                        "startup_not_written:" + id + ":" + key + ":" + value);
                    var reopened = new InstanceConfiguration(id);
                    Require(reopened.GetValue(key, "None") == value, "startup_not_restored:" + id + ":" + key + ":" + value);
                }
            }
            config.SetValue(ConfigurationKeys.BeforeTask, id == profiles[0] ? "StartupSoftwareAndScript" : "None");
            config.SetValue(ConfigurationKeys.AfterTask, id == profiles[0] ? "CloseEmulatorAndMFA" : "None");
        }
        foreach (var id in profiles)
        {
            var reopened = new InstanceConfiguration(id);
            Require(reopened.GetValue(ConfigurationKeys.BeforeTask, "None") == (id == profiles[0] ? "StartupSoftwareAndScript" : "None")
                && reopened.GetValue(ConfigurationKeys.AfterTask, "None") == (id == profiles[0] ? "CloseEmulatorAndMFA" : "None"),
                "startup_profile_settings_leaked:" + id);
        }
        Require(MFAAvalonia.Pcr.PcrAlphaPolicy.Value(ConfigurationKeys.NoAutoStart) == null
            && MFAAvalonia.Pcr.PcrAlphaPolicy.Value(ConfigurationKeys.GlobalStartEnabled) == bool.FalseString,
            "startup_execution_policy_changed");
        Console.WriteLine("PCR startup settings checks passed: every action survives reopening in all three profiles; game inputs=0");
    }

    internal static void RunGlobalSettingsPeer(string root)
    {
        const BindingFlags fields = BindingFlags.Static | BindingFlags.NonPublic;
        typeof(AppPaths).GetField("_configDirectory", fields)!.SetValue(null, Path.Combine(root, "config"));
        typeof(AppPaths).GetField("_initialized", fields)!.SetValue(null, true);
        Console.WriteLine("ready");
        Require(Console.ReadLine() == "go", "global_settings_peer_not_released");
        for (var i = 0; i < 100; i++)
        {
            Require(GlobalConfiguration.GetValue("keep") == "unchanged", "peer_lost_global_config");
            GlobalConfiguration.SetValue("peer", i.ToString());
        }
        Require(!GlobalConfiguration.HasFileAccessError, "peer_global_error:" + GlobalConfiguration.LastFileAccessErrorMessage);
    }

    private static void CheckConcurrentGlobalReads(string root)
    {
        var retry = (Func<int, bool, bool>)typeof(JsonHelper)
            .GetMethod("CanRetryWindowsPublication", BindingFlags.NonPublic | BindingFlags.Static)!
            .CreateDelegate(typeof(Func<int, bool, bool>));
        Require(retry(32, true) && retry(33, false) && retry(1175, true) && !retry(1175, false)
            && !retry(1176, true) && !retry(1177, true) && !retry(5, true),
            "only_repeat_windows_publication_errors_that_leave_both_files_in_place");
        const string key = "PcrUseWebGpu";
        Require(GlobalConfiguration.GetValue("keep", "default") == "default" && !File.Exists(GlobalConfiguration.ConfigPath)
            && !GlobalConfiguration.HasFileAccessError, "first_global_read_should_only_return_defaults");
        GlobalConfiguration.SetValue("keep", "unchanged");
        GlobalConfiguration.SetValue(key, "False");
        var info = new ProcessStartInfo(Environment.ProcessPath!) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        if (string.Equals(Path.GetFileNameWithoutExtension(info.FileName), "dotnet", StringComparison.OrdinalIgnoreCase))
            info.ArgumentList.Add(typeof(PersistenceRecoveryChecks).Assembly.Location);
        info.ArgumentList.Add(root);
        info.ArgumentList.Add("--global-settings-peer");
        using var peer = Process.Start(info)!;
        Require(peer.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(10)).GetAwaiter().GetResult() == "ready",
            "global_settings_peer_not_ready");
        using var barrier = new Barrier(5);
        var readers = Enumerable.Range(0, 4).Select(_ => Task.Run(() =>
        {
            barrier.SignalAndWait();
            for (var i = 0; i < 100; i++)
                Require(GlobalConfiguration.GetValue("keep") == "unchanged", "reader_lost_global_config");
        })).ToArray();
        peer.StandardInput.WriteLine("go");
        peer.StandardInput.Close();
        barrier.SignalAndWait();
        for (var i = 0; i < 100; i++)
            GlobalConfiguration.SetValue(key, (i % 2 == 0).ToString());
        Task.WaitAll(readers);
        if (!peer.WaitForExit(15000)) { peer.Kill(true); throw new TimeoutException("global_settings_peer_timeout"); }
        Require(peer.ExitCode == 0 && GlobalConfiguration.GetValue("peer") == "99",
            "concurrent_peer_lost_global_changes:" + peer.StandardError.ReadToEnd());
        Require(!GlobalConfiguration.HasFileAccessError, "parallel_global_read_blocks_replace:" + GlobalConfiguration.LastFileAccessErrorMessage);

        // Keep a snapshot handle open over replacement, as a separate launching process can.
        var open = typeof(JsonHelper).GetMethod("OpenReadSnapshot", BindingFlags.Static | BindingFlags.NonPublic)!;
        using var snapshot = (Stream)open.Invoke(null, [GlobalConfiguration.ConfigPath])!;
        GlobalConfiguration.SetValue(key, "True");
        using var reader = new StreamReader(snapshot);
        Require((string?)JObject.Parse(reader.ReadToEnd())[key] == "False"
            && GlobalConfiguration.GetValue(key) == "True" && !GlobalConfiguration.HasFileAccessError,
            "open_reader_keeps_old_snapshot_while_new_global_value_is_published");
    }

    private static void GlobalErrorFormattingCheck()
    {
        const string key = "GlobalConfigFileAccessError";
        if (key.ToLocalization() == key)
        {
            var language = new MFAAvalonia.Localization.MFAResxLangPlugin();
            var culture = System.Globalization.CultureInfo.GetCultureInfo("zh-CN");
            Lang.Avalonia.I18nManager.Instance.Register(language, culture, out _);
            if (!language.IsLoaded) language.Load(culture);
        }
        var template = key.ToLocalization().Replace(@"\n", "\n");
        Require(template.Contains('\n'), "global_config_error_resource_lacks_line_break");
        foreach (var path in new[] { @"C:\fixtures\tester\appsettings.json", @"C:\temp\new\report.json" })
        {
            const string detail = @"JSON detail \r\n in source, see \temp.";
            var expected = string.Format(template, path, detail);
            Require(key.ToLocalizationFormatted(false, [path, detail]) == expected,
                "global_config_error_must_keep_line_break_and_literal_paths:" + path);
        }
    }

    internal static void GlobalChecks(string root)
    {
        var file = Path.Combine(root, "appsettings.json");
        const string mixed = """
            {"NoAutoStart":true,"revision":12,"keep":"unchanged","nullable":null,
             "nested":{"variants":[false,3,"03",null]},"remove":"old"}
            """;
        File.WriteAllText(file, mixed);
        Require(bool.TryParse(GlobalConfiguration.GetValue("NoAutoStart"), out var disabled) && disabled,
            "typed_startup_boolean_could_not_be_read");
        var expected = JObject.Parse(mixed);
        GlobalConfiguration.SetValue("new", "value");
        expected["new"] = "value";
        Require(JToken.DeepEquals(JObject.Parse(File.ReadAllText(file)), expected)
            && GlobalConfiguration.GetValue("new") == "value" && !GlobalConfiguration.HasFileAccessError,
            "saving_over_typed_global_config_failed:" + GlobalConfiguration.LastFileAccessErrorMessage);
        GlobalConfiguration.SetValue("NEW", "updated");
        var mutated = JObject.Parse(File.ReadAllText(file));
        var updated = mutated.Properties().Single(p => p.Name.Equals("new", StringComparison.OrdinalIgnoreCase));
        Require((string?)updated.Value == "updated" && GlobalConfiguration.GetValue("new") == "updated",
            "typed_global_config_duplicate_key_after_case_variant_update");
        expected.Remove("new");
        expected[updated.Name] = "updated";
        GlobalConfiguration.RemoveValues(new[] { "REMOVE", "absent" });
        expected.Remove("remove");
        Require(JToken.DeepEquals(JObject.Parse(File.ReadAllText(file)), expected)
            && bool.TryParse(GlobalConfiguration.GetValue("NoAutoStart"), out disabled) && disabled
            && !GlobalConfiguration.HasFileAccessError, "removing_from_typed_global_config_lost_values");

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
