using System;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

/// <summary>黎明界 Boss 筛选的离线检查。目录来自 assets/labyrinth/labyrinth-bosses.json，
/// 名称/ID/轮转顺序在这里保留一份游戏 master 期望值，作为数据文件被误改时的独立守卫。
/// 不建窗口、不联网；Bitmap 由已初始化的 Avalonia 解码，图标缺失或损坏立即失败。</summary>
internal static class LabyrinthBossFilterChecks
{
    internal static void Run(string repositoryRoot)
    {
        const BindingFlags hidden = BindingFlags.Instance | BindingFlags.NonPublic;
        var count = 0;
        void Check(bool value, string name)
        {
            if (!value) throw new InvalidOperationException(name);
            count++;
        }

        var catalog = PcrLabyrinthBossCatalog.LoadFrom(repositoryRoot);
        // 顺序即协议轮转：quest_id 序号按 (ordinal-1)%5 映射；改动这里会让路线筛选静默换人。
        Check(catalog.Rows(3).Select(row => row.Id)
                .SequenceEqual(new[] { 301206, 303306, 319604, 312505, 306604 }),
            "boss_catalog_area3_matches_master_rotation");
        Check(catalog.Rows(5).Select(row => row.Id)
                .SequenceEqual(new[] { 302501, 301701, 310103, 315004, 319401 }),
            "boss_catalog_area5_matches_master_rotation");
        Check(catalog.Rows(3).Select(row => row.Ordinal).SequenceEqual(new[] { 1, 2, 3, 4, 5 })
                && catalog.Rows(5).Select(row => row.Ordinal).SequenceEqual(new[] { 1, 2, 3, 4, 5 }),
            "boss_catalog_ordinals_are_one_to_five");
        Check(catalog.Rows(3).Concat(catalog.Rows(5)).All(row => !string.IsNullOrWhiteSpace(row.Name)),
            "boss_catalog_names_are_filled");
        Check(catalog.Rows(3).First(row => row.Id == 319604).Name == "冰霜魔狼",
            "boss_area3_rotation_keeps_frost_wolf_third");
        Check(catalog.DefaultIds(3).SequenceEqual(new[] { 312505, 319604 }),
            "boss_default_area3_unchanged");
        Check(catalog.DefaultIds(5).SequenceEqual(new[] { 310103 }),
            "boss_default_area5_unchanged");

        var settings = (PcrLabyrinthSettings)RuntimeHelpers.GetUninitializedObject(typeof(PcrLabyrinthSettings));
        var area3 = catalog.Choices(3, settings);
        var area5 = catalog.Choices(5, settings);
        typeof(PcrLabyrinthSettings).GetField("<Area3Bosses>k__BackingField", hidden)!.SetValue(settings, area3);
        typeof(PcrLabyrinthSettings).GetField("<Area5Bosses>k__BackingField", hidden)!.SetValue(settings, area5);
        typeof(PcrLabyrinthSettings).GetField("bossCatalog", hidden)!.SetValue(settings, catalog);
        var apply = typeof(PcrLabyrinthSettings).GetMethod("ApplyBossFilters", hidden)!;
        void Apply(string routeJson) => apply.Invoke(settings, [JObject.Parse(routeJson)]);

        Check(area3.Count == 5 && area5.Count == 5, "boss_choices_are_built_for_both_areas");
        Check(area3.Concat(area5).All(choice => choice.Label.Contains(choice.Name, StringComparison.Ordinal)
                && choice.Label.Contains(choice.Id.ToString(), StringComparison.Ordinal)),
            "boss_checkbox_labels_show_name_and_id");
        Check(area3.Concat(area5).All(choice => choice.Icon is { PixelSize: { Width: 128, Height: 128 } }),
            "boss_icons_decode_to_128_square");

        // 键缺失 → 沿用目录里的默认勾选。
        Apply("{\"mode\":\"value\"}");
        Check(catalog.SelectedIds(3, area3).OrderBy(id => id).SequenceEqual(new[] { 312505, 319604 }),
            "boss_missing_key_restores_area3_default");

        // 陌生 ID 逐个丢弃，能识别的保留；空数组即不限制。
        Apply("{\"area3_bosses\":[111111,306604],\"area5_bosses\":[]}");
        Check(catalog.SelectedIds(3, area3).SequenceEqual(new[] { 306604 }),
            "boss_unknown_ids_are_dropped_and_known_ids_kept");
        Check(area5.All(choice => !choice.Selected), "boss_empty_array_means_no_limit");
        Check(catalog.SelectedIds(5, area5).Length == 0,
            "boss_empty_selection_snapshots_empty_array_for_python");
        Check(settings.HasBossFilterStatus && settings.BossFilterStatus.Contains("111111", StringComparison.Ordinal),
            "boss_dropped_ids_are_reported_in_status");

        // 往返：勾选 → 快照 → 读回，勾选状态与 ID 集合稳定。
        foreach (var choice in area3) choice.Selected = choice.Id == 303306;
        foreach (var choice in area5) choice.Selected = choice.Id == 315004;
        var snapshot = new JObject {
            ["area3_bosses"] = new JArray(catalog.SelectedIds(3, area3)),
            ["area5_bosses"] = new JArray(catalog.SelectedIds(5, area5)) };
        Check(snapshot["area3_bosses"]!.Type == JTokenType.Array && snapshot["area5_bosses"]!.Type == JTokenType.Array,
            "boss_snapshot_keeps_the_id_array_contract");
        Apply(snapshot.ToString(Newtonsoft.Json.Formatting.None));
        Check(catalog.SelectedIds(3, area3).SequenceEqual(new[] { 303306 }),
            "boss_area3_round_trip_is_stable");
        Check(catalog.SelectedIds(5, area5).SequenceEqual(new[] { 315004 }),
            "boss_area5_round_trip_is_stable");
        Check(!settings.HasBossFilterStatus, "boss_known_ids_clear_the_ignored_status");

        // 全不勾 → 写空数组，Python 侧按"不限制"处理。
        foreach (var choice in area3) choice.Selected = false;
        Check(new JArray(catalog.SelectedIds(3, area3)).Count == 0,
            "boss_all_unchecked_writes_empty_array");

        Console.WriteLine($"PCR labyrinth boss filter checks passed: {count}");
    }
}
