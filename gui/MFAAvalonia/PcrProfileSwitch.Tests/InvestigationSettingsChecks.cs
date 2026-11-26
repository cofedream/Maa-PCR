using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using Avalonia.Controls;
using Avalonia.Threading;
using MaaFramework.Binding;
using MFAAvalonia.Configuration;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.Helper;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.Views.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal static class InvestigationSettingsChecks
{
    private const string Sanctum = "sanctum_survey", Temple = "temple_survey", Tower = "tower_cloister";
    private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic;

    internal static void Run(TaskQueueView view, string sourceRoot, string directory)
    {
        var fixture = Path.Combine(Path.GetFullPath(directory), "investigations", Guid.NewGuid().ToString("N"));
        var configDir = Path.Combine(fixture, "config");
        Directory.CreateDirectory(Path.Combine(configDir, "instances"));
        var configPath = typeof(AppPaths).GetField("_configDirectory", BindingFlags.NonPublic | BindingFlags.Static)!;
        var previousPath = configPath.GetValue(null);
        configPath.SetValue(null, configDir);
        try
        {
            var adb = Path.Combine(fixture, "adb.exe");
            File.WriteAllText(adb, "inert fixture");
            TaskQueueViewModel Open()
            {
                var vm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
                var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
                const string profile = "pcr-alpha-v1";
                typeof(MaaProcessor).GetField("<InstanceId>k__BackingField", Fields)!.SetValue(processor, profile);
                typeof(MaaProcessor).GetField("<InstanceConfiguration>k__BackingField", Fields)!.SetValue(processor, new InstanceConfiguration(profile));
                typeof(TaskQueueViewModel).GetField("_processorField", Fields)!.SetValue(vm, processor);
                typeof(TaskQueueViewModel).GetField("_currentDevice", Fields)!.SetValue(vm,
                    new AdbDeviceInfo("isolated", adb, "127.0.0.1:5555", AdbScreencapMethods.Encode, AdbInputMethods.AdbShell, "{}"));
                return vm;
            }
            var vm = Open();
            var settings = vm.PcrSettings;
            var pi = JObject.Parse(File.ReadAllText(Path.Combine(sourceRoot, "interface.json")));
            var defaults = (JObject)pi["task"]![0]!["pipeline_override"]!;
            var cases = (JArray)pi["option"]!["AlphaModules"]!["cases"]!;
            JObject Select(params string[] ids)
            {
                var layers = new JArray(defaults.DeepClone());
                foreach (var id in ids)
                    layers.Add(cases.Single(row => (string?)row["name"] == id)["pipeline_override"]!.DeepClone());
                layers.Add(new JObject { ["PCRAlphaEntry"] = new JObject { ["custom_action_param"] = new JObject
                    { ["selection_mode"] = "pi_nodes_v1", ["selected_modules"] = new JArray(ids) } } });
                return PcrAlphaOwner.Selection("PCRAlphaEntry", layers.ToString(Formatting.None));
            }
            JObject Export(params string[] ids) => JObject.Parse(File.ReadAllText(settings.ExportForStart(fixture, Select(ids))));
            foreach (var (id, label) in new[] { (Sanctum, "圣迹调查"), (Temple, "神殿调查"), (Tower, "露娜塔") })
            {
                vm.SelectPcrModule(id);
                Check(vm.PcrModuleTitle.Contains(label), "editor title for " + id);
                Check(Select(id)["selected_modules"]!.Values<string>().SequenceEqual([id]), "selected exactly " + id);
            }
            Check(Select(Tower, Temple, Sanctum)["selected_modules"]!.Values<string>().SequenceEqual([Tower, Temple, Sanctum]), "user order");
            var oldNodes = (JObject)defaults.DeepClone();
            foreach (var node in new[] { "SanctumSurvey", "TempleSurvey", "TowerCloister" }) oldNodes.Remove("PCRAlphaSelect" + node);
            oldNodes["PCRAlphaSelectRewards"]!["attach"]!["selected"] = true;
            Check(PcrAlphaOwner.Selection("PCRAlphaEntry", oldNodes.ToString())["selected_modules"]!.Values<string>().SequenceEqual(["rewards"]), "old selection");

            var oldConfig = Export("rewards");
            Check(oldConfig["investigations"] == null, "nothing added to an unrelated run");
            settings.ImportConfig(oldConfig);
            Check(settings.SanctumStage == "1" && settings.TempleStage == "1"
                && settings.SanctumDailyTarget == "0" && settings.TempleDailyTarget == "0", "old import defaults");
            var imported = (JObject)oldConfig.DeepClone();
            imported["investigations"] = new JObject { [Sanctum] = new JObject { ["stage"] = 3, ["daily_target"] = 5 },
                [Temple] = new JObject { ["stage"] = 2, ["daily_target"] = 8 } };
            settings.ImportConfig(imported);
            vm = Open();
            settings = vm.PcrSettings;
            Check(settings.SanctumStage == "3" && settings.SanctumDailyTarget == "5" && settings.TempleStage == "2"
                && settings.TempleDailyTarget == "8", "persisted import reopened");
            foreach (var (controlName, value) in new[] { ("PcrSanctumStage", "3"), ("PcrSanctumDailyTarget", "5"),
                ("PcrTempleStage", "2"), ("PcrTempleDailyTarget", "8") })
            {
                var input = view.FindControl<PcrCountInput>(controlName) ?? throw new InvalidOperationException("Missing " + controlName);
                input.DataContext = vm;
                Dispatcher.UIThread.RunJobs();
                Check(input.DraftText == value, "bound control " + controlName);
            }
            var sanctumInput = view.FindControl<PcrCountInput>("PcrSanctumDailyTarget")!;
            sanctumInput.SetCurrentValue(PcrCountInput.DraftTextProperty, "6");
            Dispatcher.UIThread.RunJobs();
            Check(Open().PcrSettings.SanctumDailyTarget == "6", "real control edits saved draft");
            settings.TempleStage = "";
            var selected = Export(Sanctum);
            Check(selected["investigations"] is JObject data && data.Count == 1 && (int?)data[Sanctum]?["stage"] == 3
                && (int?)data[Sanctum]?["daily_target"] == 6, "only checked valid investigation exported");
            Check(Export(Tower)["investigations"] == null, "tower requires no investigation parameters");
            try { Export(Temple); throw new Exception("Invalid checked stage was exported"); }
            catch (InvalidOperationException) { }
            var validate = typeof(PcrStartConsent).GetMethod("Validate", BindingFlags.NonPublic | BindingFlags.Static)!;
            foreach (var id in new[] { Sanctum, Tower })
            {
                var selection = Select(id);
                var consent = settings.ConsentForStart(selection);
                var actual = (PcrStartConsent)validate.Invoke(null, [selection, Export(id), consent])!;
                Check(actual.CountContext == "gui-current-state" && actual.GameDay == "" && actual.ScheduleAuthorizations.Length == 0,
                    "fresh count state for " + id);
            }
            Check(PcrAlphaOwner.ControllerCreations == 0 && PcrAlphaOwner.AgentCreations == 0 && PcrAlphaOwner.Grants == 0,
                "no device, agent or game action");
            Console.WriteLine("PCR investigation settings, scope and selection checks passed.");
        }
        finally { configPath.SetValue(null, previousPath); }
    }

    private static void Check(bool valid, string name)
    {
        if (!valid) throw new InvalidOperationException("PCR investigation GUI check failed: " + name);
    }
}
