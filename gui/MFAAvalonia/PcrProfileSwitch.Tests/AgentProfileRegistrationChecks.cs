using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;
using MFAAvalonia.Configuration;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Other;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal static class AgentProfileRegistrationChecks
{
    internal static void Run(string directory)
    {
        const string dailyId = "pcr-alpha-v1", labyrinthId = "pcr-labyrinth-v1", agentId = "pcr-agent-v1";
        const BindingFlags hidden = BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
        var ids = new[] { dailyId, labyrinthId, agentId };
        var count = 0;
        void Check(bool value, string name)
        {
            if (!value) throw new InvalidOperationException("agent_profile_registration_" + name);
            count++;
        }
        void Set(object target, string name, object value) =>
            target.GetType().GetField(name, hidden)!.SetValue(target, value);
        void SetPath(string name, object value) => typeof(AppPaths).GetField(name, hidden)!.SetValue(null, value);

        Check(!MaaProcessorManager.IsInstanceCreated, "requires_dedicated_fresh_process");
        Check(!Instances.IsResolved<InstanceTabBarViewModel>(), "no_resolved_tab_view_model");
        var root = Path.Combine(Path.GetFullPath(directory), "agent-profile-registration-" + Guid.NewGuid().ToString("N"));
        var configDirectory = Path.Combine(root, "config");
        var instancesDirectory = Path.Combine(configDirectory, "instances");
        foreach (var path in new[] { instancesDirectory, Path.Combine(root, "logs"), Path.Combine(root, "temp") })
            Directory.CreateDirectory(path);
        // A dedicated process keeps ConfigurationManager's static cache inside this fixture.
        SetPath("_configDirectory", configDirectory);
        SetPath("_logsDirectory", Path.Combine(root, "logs"));
        SetPath("_tempDirectory", Path.Combine(root, "temp"));
        SetPath("<DataRoot>k__BackingField", root);
        SetPath("_initialized", true);
        File.WriteAllText(Path.Combine(configDirectory, "config.json"), "{}", new UTF8Encoding(false));

        string ProfilePath(string id) => Path.Combine(instancesDirectory, id + ".json");
        var dailySettings = new JObject
        {
            ["gamePackage"] = "example.fixture.pcr",
            ["gameActivity"] = ".FixtureActivity",
            ["validateDisplayMapping"] = true,
            ["schedule"] = new JArray("private-daily-task"),
            ["guideStage"] = "红焰深域 8-6",
            ["clears"] = 3
        };
        var daily = new JObject
        {
            [ConfigurationKeys.InstanceName] = "我的日常配置",
            ["PcrProfileNamedV1"] = true,
            [ConfigurationKeys.AdbDevice] = new JObject
            {
                ["Name"] = "OFFLINE FIXTURE",
                ["AdbPath"] = Path.Combine(root, "must-not-execute-adb.exe"),
                ["AdbSerial"] = "fixture:0",
                ["Config"] = "{\"fixture\":true}"
            },
            [ConfigurationKeys.CurrentController] = "Adb",
            [ConfigurationKeys.CurrentControllerName] = "fixture-adb-controller",
            [ConfigurationKeys.Resource] = "fixture-daily-resource",
            ["PcrPlayerSettingsV1"] = dailySettings.ToString(Formatting.None),
            ["UnrelatedPrivateState"] = new JObject { ["victory"] = "8-6", ["completed"] = "3/3" }
        };
        var labyrinth = new JObject
        {
            [ConfigurationKeys.InstanceName] = "我的黎明界配置",
            ["PcrProfileNamedV1"] = true,
            [ConfigurationKeys.AdbDevice] = new JObject { ["Name"] = "existing-labyrinth-device" },
            [ConfigurationKeys.CurrentController] = "Adb",
            [ConfigurationKeys.CurrentControllerName] = "existing-labyrinth-controller",
            [ConfigurationKeys.Resource] = "existing-labyrinth-resource",
            ["PcrPlayerSettingsV1"] = "{\"fixture\":\"keep-existing-labyrinth-settings\"}",
            ["UnrelatedPrivateState"] = new JArray("old-unknown", "old-ledger")
        };
        File.WriteAllText(ProfilePath(dailyId), daily.ToString(Formatting.Indented) + "\r\n", new UTF8Encoding(false));
        File.WriteAllText(ProfilePath(labyrinthId), labyrinth.ToString(Formatting.Indented) + "\r\n", new UTF8Encoding(false));
        var dailyBytes = File.ReadAllBytes(ProfilePath(dailyId));
        var labyrinthBytes = File.ReadAllBytes(ProfilePath(labyrinthId));
        File.WriteAllText(Path.Combine(root, "appsettings.json"), new JObject
        {
            [ConfigurationKeys.LastActiveInstance] = agentId,
            ["UnrelatedGlobalSetting"] = "preserved"
        }.ToString(Formatting.Indented), new UTF8Encoding(false));

        var manager = (MaaProcessorManager)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessorManager));
        void ReloadFixtureObjects()
        {
            var processors = new Dictionary<string, MaaProcessor>();
            foreach (var id in ids)
            {
                var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
                Set(processor, "<InstanceId>k__BackingField", id);
                Set(processor, "<InstanceConfiguration>k__BackingField", new InstanceConfiguration(id));
                processors[id] = processor;
            }
            Set(manager, "_lock", new object());
            Set(manager, "_instances", processors);
            Set(manager, "_instanceNames", new Dictionary<string, string>());
            Set(manager, "_instanceOrder", new List<string> { dailyId });
            Set(manager, "_initializedInstances", new HashSet<string>(ids));
            // GetViewModel must find null entries, otherwise it constructs a real ViewModel.
            Set(manager, "_viewModels", ids.ToDictionary(id => id, _ => (TaskQueueViewModel)null!));
            Set(manager, "<Current>k__BackingField", processors[dailyId]);
        }
        ReloadFixtureObjects();
        var singleton = (Lazy<MaaProcessorManager>)typeof(MaaProcessorManager).GetField("LazyInstance", hidden)!.GetValue(null)!;
        typeof(Lazy<MaaProcessorManager>).GetField("_factory", hidden)!.SetValue(singleton, new Func<MaaProcessorManager>(() => manager));
        Check(ReferenceEquals(MaaProcessorManager.Instance, manager), "uses_isolated_manager");
        Check(AppPaths.ConfigDirectory == configDirectory && GlobalConfiguration.ConfigPath == Path.Combine(root, "appsettings.json"), "all_config_paths_are_isolated");
        Check(!File.Exists(ProfilePath(agentId)), "agent_profile_initially_absent");

        void CheckLegacyBytes()
        {
            Check(File.ReadAllBytes(ProfilePath(dailyId)).SequenceEqual(dailyBytes), "daily_profile_bytes_unchanged");
            Check(File.ReadAllBytes(ProfilePath(labyrinthId)).SequenceEqual(labyrinthBytes), "labyrinth_profile_bytes_unchanged");
        }
        manager.LoadInstanceConfig();
        Check(manager.Instances.Select(p => p.InstanceId).SequenceEqual(ids), "registers_three_fixed_profiles_in_order");
        Check(manager.Current.InstanceId == agentId, "restores_agent_as_active_profile");
        Check(manager.GetInstanceName(dailyId) == "我的日常配置" && manager.GetInstanceName(labyrinthId) == "我的黎明界配置", "preserves_existing_profile_names");
        Check(manager.GetInstanceName(agentId) == "公会战", "names_new_agent_profile");
        Check(GlobalConfiguration.GetValue(ConfigurationKeys.InstanceList) == string.Join(",", ids)
            && GlobalConfiguration.GetValue(ConfigurationKeys.InstanceOrder) == string.Join(",", ids), "persists_three_profile_registration");
        Check(GlobalConfiguration.GetValue(ConfigurationKeys.LastActiveInstanceName) == "公会战", "persists_active_agent_name");
        var agent = JObject.Parse(File.ReadAllText(ProfilePath(agentId)));
        foreach (var key in new[] { ConfigurationKeys.AdbDevice, ConfigurationKeys.CurrentController,
            ConfigurationKeys.CurrentControllerName, ConfigurationKeys.Resource })
            Check(JToken.DeepEquals(agent[key], daily[key]), "copies_initial_" + key);
        var copiedSettings = JObject.Parse((string)agent["PcrPlayerSettingsV1"]!);
        Check(copiedSettings.Properties().Select(p => p.Name).OrderBy(n => n).SequenceEqual(
            new[] { "gameActivity", "gamePackage", "validateDisplayMapping" }), "copies_only_necessary_player_settings");
        foreach (var key in new[] { "gamePackage", "gameActivity", "validateDisplayMapping" })
            Check(JToken.DeepEquals(copiedSettings[key], dailySettings[key]), "copies_initial_" + key);
        Check(agent["UnrelatedPrivateState"] == null && copiedSettings["schedule"] == null
            && copiedSettings["guideStage"] == null && copiedSettings["clears"] == null, "does_not_copy_daily_private_state_or_progress");
        CheckLegacyBytes();

        var agentConfig = new InstanceConfiguration(agentId);
        Check(agentConfig.GetValue(ConfigurationKeys.Resource, "") == "fixture-daily-resource", "new_profile_reads_back_from_disk");
        agentConfig.SetValue(ConfigurationKeys.InstanceName, "PCR Agent");
        ReloadFixtureObjects(); manager.LoadInstanceConfig();
        Check(manager.GetInstanceName(agentId) == "公会战", "renames_previous_default_without_changing_profile_id");
        agentConfig = new InstanceConfiguration(agentId);
        agentConfig.SetValue(ConfigurationKeys.InstanceName, "我的 Agent 配置");
        agentConfig.SetValue(ConfigurationKeys.AdbDevice, new JObject { ["Name"] = "existing-agent-device", ["AdbSerial"] = "agent:1" });
        agentConfig.SetValue(ConfigurationKeys.CurrentControllerName, "existing-agent-controller");
        agentConfig.SetValue(ConfigurationKeys.Resource, "existing-agent-resource");
        agentConfig.SetValue("PcrPlayerSettingsV1", "{\"gamePackage\":\"example.agent.pcr\",\"gameActivity\":\".Existing\",\"validateDisplayMapping\":false}");
        var agentBytes = File.ReadAllBytes(ProfilePath(agentId));
        ReloadFixtureObjects();
        manager.LoadInstanceConfig();
        Check(File.ReadAllBytes(ProfilePath(agentId)).SequenceEqual(agentBytes), "reopening_keeps_existing_agent_profile_bytes");
        Check(manager.Current.InstanceId == agentId && manager.GetInstanceName(agentId) == "我的 Agent 配置", "reopening_restores_custom_agent_name_and_active_profile");
        Check(manager.Current.InstanceConfiguration.GetValue(ConfigurationKeys.Resource, "") == "existing-agent-resource", "reopening_reads_existing_agent_values");
        CheckLegacyBytes();

        foreach (var selected in new[] { dailyId, labyrinthId, agentId, "unsupported-profile" })
        {
            GlobalConfiguration.SetValue(ConfigurationKeys.LastActiveInstance, selected);
            ReloadFixtureObjects();
            manager.LoadInstanceConfig();
            Check(manager.Current.InstanceId == (selected == "unsupported-profile" ? dailyId : selected), "restores_active_" + selected);
            Check(File.ReadAllBytes(ProfilePath(agentId)).SequenceEqual(agentBytes), "active_restore_does_not_rewrite_agent_" + selected);
            CheckLegacyBytes();
        }
        Check(GlobalConfiguration.GetValue("UnrelatedGlobalSetting") == "preserved", "preserves_unrelated_global_setting");
        Check(Directory.EnumerateFiles(instancesDirectory, "*.json").Select(Path.GetFileNameWithoutExtension).OrderBy(id => id)
            .SequenceEqual(ids.OrderBy(id => id)), "writes_only_three_fixed_profile_files");
        Check(PcrAlphaOwner.ControllerCreations == 0 && PcrAlphaOwner.AgentCreations == 0
            && PcrAlphaOwner.Grants == 0 && PcrAlphaOwner.SessionCreations == 0, "no_controller_agent_grant_or_session_created");
        Console.WriteLine($"Agent profile registration checks passed: {count}; fixture={root}; controller/agent/grant/session starts=0");
    }
}
