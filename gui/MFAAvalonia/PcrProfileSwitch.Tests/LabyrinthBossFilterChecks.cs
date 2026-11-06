using System;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

/// <summary>黎明界 Boss 筛选改为按名称勾选后的离线检查。
/// 不建窗口、不读写磁盘、不联网；设置对象用未初始化实例并把 ready 留在 false，
/// 这样勾选回调只走 ready 守卫的提前返回，不触发保存副作用。</summary>
internal static class LabyrinthBossFilterChecks
{
    internal static void Run()
    {
        const BindingFlags hidden = BindingFlags.Instance | BindingFlags.NonPublic;
        var count = 0;
        void Check(bool value, string name)
        {
            if (!value) throw new InvalidOperationException(name);
            count++;
        }

        // 行顺序与 src/pcr_maa/labyrinth/routes.py 的 BOSSES 对齐便于两张表互核；
        // Python 只按 ID 集合匹配，真正的风险是名称与 ID 配错（名称由 tests/test_labyrinth_boss_names_gui.py 对照游戏数据锁定）。
        Check(PcrLabyrinthBossTable.Rows(3).Select(row => row.Id)
                .SequenceEqual(new[] { 301206, 303306, 319604, 312505, 306604 }),
            "boss_table_area3_matches_python_bosses_order");
        Check(PcrLabyrinthBossTable.Rows(5).Select(row => row.Id)
                .SequenceEqual(new[] { 302501, 301701, 310103, 315004, 319401 }),
            "boss_table_area5_matches_python_bosses_order");
        Check(PcrLabyrinthBossTable.Rows(3).Length == 5 && PcrLabyrinthBossTable.Rows(5).Length == 5,
            "boss_table_supports_five_bosses_per_area");
        Check(PcrLabyrinthBossTable.Rows(3).Concat(PcrLabyrinthBossTable.Rows(5))
                .All(row => !string.IsNullOrWhiteSpace(row.Name)),
            "boss_table_names_are_filled");
        Check(PcrLabyrinthBossTable.DefaultIds(3).SequenceEqual(new[] { 312505, 319604 }),
            "boss_default_area3_unchanged");
        Check(PcrLabyrinthBossTable.DefaultIds(5).SequenceEqual(new[] { 310103 }),
            "boss_default_area5_unchanged");

        var settings = (PcrLabyrinthSettings)RuntimeHelpers.GetUninitializedObject(typeof(PcrLabyrinthSettings));
        var area3 = PcrLabyrinthBossTable.Choices(3, settings);
        var area5 = PcrLabyrinthBossTable.Choices(5, settings);
        typeof(PcrLabyrinthSettings).GetField("<Area3Bosses>k__BackingField", hidden)!.SetValue(settings, area3);
        typeof(PcrLabyrinthSettings).GetField("<Area5Bosses>k__BackingField", hidden)!.SetValue(settings, area5);
        var apply = typeof(PcrLabyrinthSettings).GetMethod("ApplyBossFilters", hidden)!;
        void Apply(string routeJson) => apply.Invoke(settings, [JObject.Parse(routeJson)]);

        Check(area3.Count == 5 && area5.Count == 5, "boss_choices_are_built_for_both_areas");
        Check(area3.Concat(area5).All(choice => choice.Label.Contains(choice.Name, StringComparison.Ordinal)
                && choice.Label.Contains(choice.Id.ToString(), StringComparison.Ordinal)),
            "boss_checkbox_labels_show_name_and_id");

        // 键缺失 → 沿用原默认勾选。
        Apply("{\"mode\":\"value\"}");
        Check(PcrLabyrinthBossTable.SelectedIds(3, area3).OrderBy(id => id).SequenceEqual(new[] { 312505, 319604 }),
            "boss_missing_key_restores_area3_default");

        // 陌生 ID 逐个丢弃，能识别的保留；空数组即不限制。
        Apply("{\"area3_bosses\":[111111,306604],\"area5_bosses\":[]}");
        Check(PcrLabyrinthBossTable.SelectedIds(3, area3).SequenceEqual(new[] { 306604 }),
            "boss_unknown_ids_are_dropped_and_known_ids_kept");
        Check(area5.All(choice => !choice.Selected), "boss_empty_array_means_no_limit");
        Check(PcrLabyrinthBossTable.SelectedIds(5, area5).Length == 0,
            "boss_empty_selection_snapshots_empty_array_for_python");
        Check(settings.HasBossFilterStatus && settings.BossFilterStatus.Contains("111111", StringComparison.Ordinal),
            "boss_dropped_ids_are_reported_in_status");

        // 往返：勾选 → 快照 → 读回，勾选状态与 ID 集合稳定。
        foreach (var choice in area3) choice.Selected = choice.Id == 303306;
        foreach (var choice in area5) choice.Selected = choice.Id == 315004;
        var snapshot = new JObject {
            ["area3_bosses"] = new JArray(PcrLabyrinthBossTable.SelectedIds(3, area3)),
            ["area5_bosses"] = new JArray(PcrLabyrinthBossTable.SelectedIds(5, area5)) };
        Check(snapshot["area3_bosses"]!.Type == JTokenType.Array && snapshot["area5_bosses"]!.Type == JTokenType.Array,
            "boss_snapshot_keeps_the_id_array_contract");
        Apply(snapshot.ToString(Newtonsoft.Json.Formatting.None));
        Check(PcrLabyrinthBossTable.SelectedIds(3, area3).SequenceEqual(new[] { 303306 }),
            "boss_area3_round_trip_is_stable");
        Check(PcrLabyrinthBossTable.SelectedIds(5, area5).SequenceEqual(new[] { 315004 }),
            "boss_area5_round_trip_is_stable");
        Check(!settings.HasBossFilterStatus, "boss_known_ids_clear_the_ignored_status");

        // 全不勾 → 写空数组，Python 侧按"不限制"处理。
        foreach (var choice in area3) choice.Selected = false;
        Check(new JArray(PcrLabyrinthBossTable.SelectedIds(3, area3)).Count == 0,
            "boss_all_unchecked_writes_empty_array");

        Console.WriteLine($"PCR labyrinth boss filter checks passed: {count}");
    }
}
