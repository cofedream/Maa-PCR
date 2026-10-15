using System;
using MaaFramework.Binding;
using Avalonia.Platform.Storage;
using System.IO;
using System.Linq;
using System.Collections.ObjectModel;
using System.ComponentModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MFAAvalonia.Configuration;
using MFAAvalonia.Helper.ValueType;
using MFAAvalonia.ViewModels.Pages;
using MFAAvalonia.ViewModels.UsersControls;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public partial class PcrScheduleChoice(string id, string label) : ObservableObject
{
    public string Id { get; } = id;
    public string Label { get; } = label;
    [ObservableProperty] private bool enabled;
}

// Uses MFA's existing per-instance persistence. A saved plan is data, never a grant.
public partial class PcrPlayerSettings : ObservableObject
{
    private const string Key = "PcrPlayerSettingsV1";
    private readonly TaskQueueViewModel vm;
    private string saved = "";
    private string? lastSaveError;
    private bool ready;
    private JObject? ledgerIdentity;
    private JObject? identityConnection;
    private JObject? managerIdentity;
    private JObject? managerConnection;
    public string[] Slots { get; } = { "", "1", "2", "3", "4", "5", "6", "7" };
    [ObservableProperty] private string mumuPath = "";
    [ObservableProperty] private string sdkPath = "";
    [ObservableProperty] private string instanceIndex = "";
    [ObservableProperty] private string cloneIndex = "";
    [ObservableProperty] private string deepSlot = "";
    [ObservableProperty] private string? deepCount = "0";
    [ObservableProperty] private string veryHardSlot = "";
    [ObservableProperty] private string? veryHardCount = "0";
    [ObservableProperty] private string hardSlot = "";
    [ObservableProperty] private string? hardCount = "0";
    [ObservableProperty] private string normalSlot = "";
    [ObservableProperty] private string? normalCount = "0";
    [ObservableProperty] private bool validateDisplayMapping;
    [ObservableProperty] private string gamePackage = PcrMuMuDisplay.Package;
    [ObservableProperty] private string gameActivity = ".MainActivity";
    public System.Collections.Generic.IReadOnlyList<PcrChannelPreset> Channels => PcrChannelCatalog.All;
    public PcrChannelPreset? SelectedChannel
    {
        get => PcrChannelCatalog.Find(GamePackage, GameActivity);
        set {
            if (value == null) return;
            if (!Channels.Contains(value)) throw new InvalidOperationException("请选择已收录的渠道。");
            GamePackage = value.Package;
            GameActivity = value.Activity;
        }
    }
    public string ChannelStatus => SelectedChannel is { } channel ? "已选择：" + channel.Label
        : "原渠道配置尚未收录，请选择已收录渠道；原设置和历史记录保留。";
    [ObservableProperty] private bool collectGiftStamina;
    [ObservableProperty] private bool unreset;
    [ObservableProperty] private bool attestPlan;
    // Legacy in-memory fields only; never persisted or used as execution facts.
    [ObservableProperty] private string gameDay = "";
    [ObservableProperty] private string status = "设置自动保存；可先填写任务，再选择设备。";
    public ObservableCollection<PcrScheduleChoice> Schedule { get; } = new(new[] {
        new PcrScheduleChoice("arena_coins", "竞技场金币"), new PcrScheduleChoice("princess_arena_coins", "公主竞技场金币"),
        new PcrScheduleChoice("dungeon_skip", "地下城跳过"), new PcrScheduleChoice("mana_skip", "玛那探索（2券）"),
        new PcrScheduleChoice("exp_skip", "经验探索（2券）"), new PcrScheduleChoice("normal_gacha_free", "普通免费扭蛋"),
        new PcrScheduleChoice("guild_like", "公会点赞"), new PcrScheduleChoice("house_items", "公会之家道具"),
        new PcrScheduleChoice("memorial_rewards", "追忆战场奖励") });

    public PcrPlayerSettings(TaskQueueViewModel owner)
    {
        vm = owner;
        var stored = vm.Processor.InstanceConfiguration.GetValue(Key, "");
        if (!string.IsNullOrEmpty(stored))
        {
            try {
                Load(JObject.Parse(stored));
                // GetValue may be an in-memory value left by a failed legacy save.
                if (InstanceConfiguration.ReadValueFromFile(vm.Processor.InstanceId, Key) == stored) {
                    saved = Payload().ToString(Formatting.None); Status = "已载入保存的设置。";
                } else Status = "设置尚未写入磁盘，请重新保存。";
            }
            catch { Status = "保存的设置无法读取，请重新填写并保存。"; }
        }
        ready = true;
        vm.Processor.InstanceConfiguration.PersistenceFailed += message => {
            lastSaveError = message;
            Status = "保存失败：" + message;
        };
        foreach (var choice in Schedule) choice.PropertyChanged += (_, _) => { PcrAlphaOwner.Revoke(vm.Processor); MarkDirty(); };
    }
    protected override void OnPropertyChanged(PropertyChangedEventArgs e)
    {
        base.OnPropertyChanged(e);
        if (e.PropertyName is nameof(GamePackage) or nameof(GameActivity)) {
            base.OnPropertyChanged(new PropertyChangedEventArgs(nameof(SelectedChannel)));
            base.OnPropertyChanged(new PropertyChangedEventArgs(nameof(ChannelStatus)));
        }
        if (ready && e.PropertyName != nameof(Status) && e.PropertyName != nameof(Summary) && vm != null)
        {
            PcrAlphaOwner.Revoke(vm.Processor);
            if (e.PropertyName != nameof(GameDay)) MarkDirty();
        }
    }
    private void MarkDirty()
    {
        if (!ready) return;
        if (saved != Payload().ToString(Formatting.None)) {
            Status = "有未保存的修改";
            Save();
        }
        base.OnPropertyChanged(new PropertyChangedEventArgs(nameof(Summary)));
    }
    private JObject Payload() => new() {
        ["mumuPath"] = MumuPath, ["sdkPath"] = SdkPath, ["index"] = InstanceIndex, ["clone"] = CloneIndex,
        ["deepSlot"] = DeepSlot, ["deepCount"] = DeepCount,
        ["veryHardSlot"] = VeryHardSlot, ["veryHardCount"] = VeryHardCount,
        ["hardSlot"] = HardSlot, ["hardCount"] = HardCount,
        ["normalSlot"] = NormalSlot, ["normalCount"] = NormalCount,
        ["validateDisplayMapping"] = ValidateDisplayMapping,
        ["gamePackage"] = GamePackage, ["gameActivity"] = GameActivity,
        ["collectGiftStamina"] = CollectGiftStamina,
        ["ledgerIdentity"] = ledgerIdentity?.DeepClone(), ["identityConnection"] = identityConnection?.DeepClone(),
        ["managerIdentity"] = managerIdentity?.DeepClone(), ["managerConnection"] = managerConnection?.DeepClone(),
        ["schedule"] = new JArray(Schedule.Where(x => x.Enabled).Select(x => x.Id)),
        ["connection"] = connection.DeepClone() };
    private JObject connection = new();
    private void Load(JObject p)
    {
        MumuPath = (string?)p["mumuPath"] ?? ""; SdkPath = (string?)p["sdkPath"] ?? "";
        InstanceIndex = (string?)p["index"] ?? ""; CloneIndex = (string?)p["clone"] ?? "";
        DeepSlot = (string?)p["deepSlot"] ?? ""; DeepCount = (string?)p["deepCount"];
        VeryHardSlot = (string?)p["veryHardSlot"] ?? ""; VeryHardCount = (string?)p["veryHardCount"];
        HardSlot = (string?)p["hardSlot"] ?? ""; HardCount = (string?)p["hardCount"];
        NormalSlot = (string?)p["normalSlot"] ?? ""; NormalCount = (string?)p["normalCount"];
        ValidateDisplayMapping = p["validateDisplayMapping"] == null || p["validateDisplayMapping"]!.Type == JTokenType.Null
            ? false : p["validateDisplayMapping"]!.Type == JTokenType.Boolean
            ? p["validateDisplayMapping"]!.Value<bool>()
            : throw new InvalidOperationException("多显示屏映射校验设置必须为布尔值");
        CollectGiftStamina = ReadGiftStamina(p, "collectGiftStamina");
        GamePackage = (string?)p["gamePackage"] ?? PcrMuMuDisplay.Package;
        GameActivity = (string?)p["gameActivity"] ?? ".MainActivity";
        Unreset = false; AttestPlan = false;
        ledgerIdentity = p["ledgerIdentity"] as JObject;
        identityConnection = p["identityConnection"] as JObject;
        managerIdentity = p["managerIdentity"] as JObject;
        managerConnection = p["managerConnection"] as JObject;
        connection = (JObject?)p["connection"]?.DeepClone() ?? new();
        foreach (var c in Schedule) c.Enabled = p["schedule"]?.Values<string>().Contains(c.Id) == true;
    }
    private static bool ReadGiftStamina(JObject value, string key) => value[key] == null ? false
        : value[key]!.Type == JTokenType.Boolean ? value[key]!.Value<bool>()
        : throw new InvalidOperationException("领取礼物箱体力设置必须为布尔值");
    private static int Number(string? text, int min, int max, string field) =>
        int.TryParse(text, out var n) && n >= min && n <= max ? n : throw new InvalidOperationException(field + "未设置或超出范围");
    public string Summary => string.Join("；", new[] {
        $"深域每日目标 {DeepCount}", $"高难每日目标 {VeryHardCount}", $"困难每日目标 {HardCount}",
        $"普通本次 {NormalCount}" }.Select(x => x.EndsWith(" 0") ? x + "（本次跳过）" : x));
    private JObject Config(string[]? selected = null)
    {
        if (SelectedChannel == null)
            throw new InvalidOperationException("该渠道配置尚未收录，请在高级设置选择已收录渠道；原设置和历史记录保留。");
        JObject Preset(string kind, string label, string slot, string? count)
        {
            if (selected != null && !selected.Contains(kind))
                return new JObject { ["kind"] = kind, ["slot"] = null, ["label"] = "", ["target_per_stage"] = 0 };
            var target = Number(count, 0, 999, label + (kind == "normal" ? "每关本次次数（0..999）" : "每关每日目标（0..999）"));
            return new JObject {
                ["kind"] = kind, ["slot"] = target == 0 ? null : Number(slot, 1, 7, label + "预设槽位 1..7"),
                ["label"] = string.IsNullOrWhiteSpace(slot) ? "" : "预设" + slot,
                ["target_per_stage"] = target };
        }
        var presets = new JArray(Preset("deep", "深域", DeepSlot, DeepCount), Preset("very_hard", "高难", VeryHardSlot, VeryHardCount), Preset("hard", "困难", HardSlot, HardCount), Preset("normal", "普通", NormalSlot, NormalCount));
        var slots = presets.Select(x => (int?)x["slot"]).Where(x => x.HasValue).ToArray();
        if (slots.Distinct().Count() != slots.Length) throw new InvalidOperationException("已配置的预设槽位不可重复。");
        _ = PcrMuMuDisplay.ComponentPattern(GamePackage, GameActivity);
        var controller = new JObject { ["kind"] = "adb", ["app_package"] = GamePackage };
        if (GameActivity != ".MainActivity") controller["app_activity"] = GameActivity;
        if (ledgerIdentity != null && (string?)ledgerIdentity["app_package"] == GamePackage
            && JToken.DeepEquals(connection, identityConnection))
            controller["ledger_identity"] = ledgerIdentity.DeepClone();
        if (managerIdentity != null && (string?)managerIdentity["app_package"] == GamePackage
            && JToken.DeepEquals(connection, managerConnection))
            controller["manager_identity"] = managerIdentity.DeepClone();
        // GUI one-key is scoped to this explicit Start, not the legacy CLI ledger.
        // The Owner verifies transport, current package/display and native objects.
        // Missing continuity metadata is valid for a generic ADB session; stored
        // identities stay preserved and are never transferred to another target.
        return new JObject { ["version"] = 4, ["start"] = "home", ["count_context"] = null,
            ["connection"] = connection.DeepClone(), ["presets"] = presets,
            ["controller"] = controller, ["collect_gift_stamina"] = CollectGiftStamina };
    }
    // Called only after Owner's fresh fixed-command read and pending/target check.
    // Persistence restores the comparison baseline, never permission to skip a read.
    internal void AcceptManagerIdentity(JObject observed, JObject selectedConnection)
    {
        if (managerIdentity != null && (!JToken.DeepEquals(managerConnection, selectedConnection)
            || !JToken.DeepEquals(managerIdentity, observed)))
            throw new InvalidOperationException("日程设备身份已变化，保留原绑定和账本，请核验是否重建或导入实例。[schedule_identity_drift]");
        if (ledgerIdentity != null && (!JToken.DeepEquals(identityConnection, selectedConnection)
            || !PcrMuMuIdentity.SameLegacy(ledgerIdentity, observed)))
            throw new InvalidOperationException("旧日程身份无法对应当前本机实例；克隆或导入目标尚不支持自动接续。[schedule_identity_legacy_unmapped]");
        managerIdentity = (JObject)observed.DeepClone();
        managerConnection = (JObject)selectedConnection.DeepClone();
    }
    [RelayCommand] private void Save() => PersistDraft(revoke: true);
    private void PersistDraft(bool revoke)
    {
        try {
            if (!vm.CanEditPcrTarget) throw new InvalidOperationException("请先停止当前任务");
            // Draft persistence is independent of transport and execution validation.
            if (vm.CurrentDevice is AdbDeviceInfo device &&
                AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(device.AdbPath, device.AdbSerial, out var path, out var serial))
                connection = new JObject { ["adb_path"] = path, ["serial"] = serial };
            var payload = Payload().ToString(Formatting.None);
            vm.Processor.InstanceConfiguration.SetValueAndPersist(Key, payload);
            saved = payload; lastSaveError = null;
            if (revoke) PcrAlphaOwner.Revoke(vm.Processor);
            Status = "已保存（自动保存草稿；开始时校验所选任务）";
        } catch (Exception ex) {
            lastSaveError = ex.Message;
            Status = "保存失败：" + ex.Message;
            vm.Processor.AddLog("PCR 设置保存失败：" + ex.Message, (Avalonia.Media.IBrush?)null);
        }
    }
    public string ExportForStart(string repository) => ExportForStart(repository, null);
    public string ExportForStart(string repository, JObject? selection)
    {
        if (!vm.CanEditPcrTarget) throw new InvalidOperationException("请先停止当前任务");
        if (vm.CurrentDevice is not AdbDeviceInfo target ||
            !AdbEditorDialogViewModel.TryNormalizePcrAdbTarget(target.AdbPath, target.AdbSerial, out var path, out var serial))
            throw new InvalidOperationException("设备栏：请选择有效的本机 ADB 目标后开始；任务草稿已保留。");
        connection = new JObject { ["adb_path"] = path, ["serial"] = serial };
        var modules = selection?["selected_modules"]?.Values<string>().ToArray();
        if (selection != null) _ = ConsentForStart(selection);
        var config = Config(modules);
        // This flush belongs to the already pending Start. Edits still revoke,
        // but writing the unchanged reviewed draft must not cancel its owner.
        PersistDraft(revoke: false);
        if (lastSaveError != null || saved != Payload().ToString(Formatting.None))
            throw new InvalidOperationException("设置保存失败：" + lastSaveError);
        var dir = Path.Combine(repository, ".runtime", "alpha-agent", "settings"); Directory.CreateDirectory(dir);
        var output = Path.Combine(dir, Guid.NewGuid().ToString("N") + ".json");
        File.WriteAllText(output, config.ToString(Formatting.Indented)); return output;
    }
    public PcrStartConsent ConsentForStart(JObject selection)
    {
        var modules = selection["selected_modules"]!.Values<string>().ToArray();
        bool schedule = modules.Contains("schedule_onekey");
        bool sweep = modules.Contains("event") || modules.Contains("abyss") ||
            modules.Contains("deep") && Number(DeepCount, 0, 999, "深域次数") > 0 ||
            modules.Contains("very_hard") && Number(VeryHardCount, 0, 999, "高难次数") > 0 ||
            modules.Contains("hard") && Number(HardCount, 0, 999, "困难次数") > 0;
        // Selecting Schedule and pressing Start expresses this run's intent.
        // Legacy saved per-item choices remain data and do not limit that intent.
        return new("", schedule || sweep ? "gui-current-state" : "",
            schedule ? Schedule.Select(x => x.Id).ToArray() : Array.Empty<string>(), false);
    }
    // Explicit import only; existing file stays untouched. Legacy budgets are
    // ignored by this player flow. No old run permission or game day is read.
    public void ImportConfig(JObject config)
    {
        if (!vm.CanEditPcrTarget) throw new InvalidOperationException("请先停止当前任务");
        int? version = (int?)config["version"];
        if (version is not (3 or 4)) throw new InvalidOperationException("导入仅兼容 v3 MuMu / v4 标准 ADB 配置。");
        if (config["presets"] is not JArray presets || config["connection"] is not JObject importedConnection)
            throw new InvalidOperationException("导入缺少 presets 数组或 connection 对象。");
        if (version == 4 && (string?)config["controller"]?["kind"] != "adb")
            throw new InvalidOperationException("v4 导入要求标准 ADB controller.kind=adb。");
        foreach (var row in presets) {
            if (row is not JObject || !new[] { "deep", "very_hard", "hard", "normal" }.Contains((string?)row["kind"]))
                throw new InvalidOperationException("导入 presets.kind 无效。");
            var count = Number(row["target_per_stage"]?.ToString() ?? "", 0, 999, row["kind"] + "次数");
            if (count > 0 || row["slot"]?.Type != JTokenType.Null) _ = Number(row["slot"]?.ToString() ?? "", 1, 7, row["kind"] + "槽位");
        }
        if (presets.Select(x => (string?)x["kind"]).Distinct().Count() != presets.Count ||
            !new[] { "deep", "very_hard", "hard" }.All(k => presets.Any(x => (string?)x["kind"] == k)))
            throw new InvalidOperationException("导入 presets 必须包含且不重复 deep/very_hard/hard。");
        var positiveSlots = presets.Where(x => (int?)x["target_per_stage"] > 0).Select(x => (int?)x["slot"]).ToArray();
        if (positiveSlots.Distinct().Count() != positiveSlots.Length) throw new InvalidOperationException("导入预设槽位重复。");
        var identity = version == 3 ? config["mumu"] as JObject : config["controller"]?["ledger_identity"] as JObject;
        if (version == 3 && identity == null) throw new InvalidOperationException("v3 导入缺少 mumu 身份。");
        var importedGiftStamina = ReadGiftStamina(config, "collect_gift_stamina");
        var importedPackage = version == 3 ? (string?)identity?["app_package"]
            : (string?)config["controller"]?["app_package"];
        var importedActivity = (string?)config["controller"]?["app_activity"] ?? ".MainActivity";
        _ = PcrMuMuDisplay.ComponentPattern(importedPackage ?? "", importedActivity);
        if (PcrChannelCatalog.Find(importedPackage!, importedActivity) == null)
            throw new InvalidOperationException("导入的渠道尚未收录，请反馈维护者补充；现有设置和历史记录保留。");
        ready = false;
        CollectGiftStamina = importedGiftStamina;
        GamePackage = importedPackage!; GameActivity = importedActivity;
        connection = (JObject)importedConnection.DeepClone();
        ledgerIdentity = (JObject?)identity?.DeepClone();
        identityConnection = ledgerIdentity == null ? null : (JObject)connection.DeepClone();
        DeepSlot = VeryHardSlot = HardSlot = ""; DeepCount = VeryHardCount = HardCount = "0";
        NormalSlot = ""; NormalCount = "0";
        foreach (var p in config["presets"]!)
        {
            var slot = p["slot"]?.Type == JTokenType.Null ? "" : p["slot"]?.ToString() ?? "";
            var count = p["target_per_stage"]!.ToString();
            switch ((string?)p["kind"]) {
                case "deep": DeepSlot = slot; DeepCount = count; break;
                case "very_hard": VeryHardSlot = slot; VeryHardCount = count; break;
                case "hard": HardSlot = slot; HardCount = count; break;
                case "normal": NormalSlot = slot; NormalCount = count; break;
            }
        }
        ready = true; saved = ""; GameDay = ""; Unreset = false; AttestPlan = false;
        foreach (var c in Schedule) c.Enabled = false;
        Save();
        if (lastSaveError == null) Status = "已保存：v" + version + " 配置已导入；请核对设备和任务，旧许可未导入。";
    }
    [RelayCommand] private async System.Threading.Tasks.Task ImportAsync()
    {
        if (!vm.CanEditPcrTarget) return;
        try {
            var storage = MFAAvalonia.Helper.Instances.StorageProvider ?? throw new InvalidOperationException("本机文件选择器不可用");
            var files = await storage.OpenFilePickerAsync(new Avalonia.Platform.Storage.FilePickerOpenOptions {
                Title = "导入旧配置（可选）", AllowMultiple = false,
                FileTypeFilter = [new Avalonia.Platform.Storage.FilePickerFileType("JSON") { Patterns = ["*.json"] }] });
            if (files.Count == 1) {
                var path = files[0].TryGetLocalPath();
                if (path == null || !Path.IsPathFullyQualified(path) || path.StartsWith(@"\\") || path.StartsWith("//")) throw new InvalidOperationException("仅支持本机配置文件");
                ImportConfig(JObject.Parse(File.ReadAllText(path)));
            }
        } catch (Exception ex) { Status = ex.Message; }
    }
}
