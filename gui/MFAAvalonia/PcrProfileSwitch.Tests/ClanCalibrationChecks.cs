using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using Avalonia.Controls;
using MFAAvalonia.Pcr;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

internal static class ClanCalibrationChecks
{
    public static void Run(string sourceRoot, string directory)
    {
        var root = Path.GetFullPath(Path.Combine(directory, "clan-calibration", Guid.NewGuid().ToString("N")));
        var indexPath = Path.Combine(root, PcrClanCalibration.IndexPath);
        var profilePath = Path.Combine(root, "assets/resource/clan-battle-calibrations/synthetic.json");
        Directory.CreateDirectory(Path.GetDirectoryName(indexPath)!);
        const string contentVersion = "202610250001";
        var count = 0;
        void Check(bool value, string name) { if (!value) throw new InvalidOperationException("clan_calibration_" + name); count++; }
        void Reject(Action action, string name, string code)
        {
            try { action(); }
            catch (InvalidOperationException ex) when (ex.Message.Contains(code)) { count++; return; }
            throw new InvalidOperationException("clan_calibration_expected_rejection_" + name);
        }
        var reader = typeof(PcrClanCalibration).GetMethod("Read", BindingFlags.Static | BindingFlags.NonPublic,
            null, new[] { typeof(string), typeof(string), typeof(string) }, null)!;
        PcrClanCalibration Read(string? version = contentVersion, string? digest = null) =>
            (PcrClanCalibration)reader.Invoke(null, new object?[] { root, version, digest ?? PcrClanGuide.Hash(File.ReadAllBytes(indexPath)) })!;
        JObject Dependency(string relative, string content)
        {
            var path = Path.Combine(root, relative); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, content, new UTF8Encoding(false));
            return new JObject { ["path"] = relative, ["sha256"] = PcrClanGuide.Hash(File.ReadAllBytes(path)) };
        }
        JObject Sample(string name)
        {
            var value = Dependency("assets/resource/clan-battle-calibrations/" + name + ".sample", "synthetic " + name);
            value["page"] = name; return value;
        }
        JObject Profile(int version = 2)
        {
            var template = Dependency("assets/resource/image/clan-synthetic/first.png", "synthetic template one");
            _ = Dependency("assets/resource/image/clan-synthetic/second.png", "synthetic template two");
            JObject Node() => new() { ["recognition"] = "TemplateMatch", ["roi"] = new JArray(1, 1, 8, 8),
                ["template"] = "clan-synthetic/first.png", ["sha256"] = template["sha256"]!.DeepClone() };
            JArray Roi() => new(1, 1, 8, 8);
            JObject Page(params string[] fields) => new()
            {
                ["labels"] = new JArray(Node(), Node()), ["rejects"] = new JArray(Node()),
                ["fields"] = new JObject(fields.Select(field => new JProperty(field, Roi())))
            };
            JObject Members(int size) => new()
            {
                ["slots"] = new JArray(Enumerable.Range(0, size).Select(_ => Roi())),
                ["empty"] = new JArray(Enumerable.Range(0, size).Select(_ => Node())),
                ["borrowed"] = new JArray(Enumerable.Range(0, size).Select(_ => Node()))
            };
            var dailyPages = new JObject { ["lobby"] = Page("remaining_count", "carry_count", "cp", "period", "phase"),
                ["target"] = Page("remaining_count", "cp", "health", "boss", "phase"), ["carry_empty"] = Page() };
            var pages = new JObject { ["adventure"] = Page(), ["boss_detail"] = Page("boss", "phase"),
                ["formation"] = Page(), ["support"] = Page(), ["battle"] = Page("remaining_seconds"),
                ["result"] = Page("damage"), ["used_units"] = Page("game_day"), ["carry"] = Page() };
            var actionPages = new (string Name, string Page)[] {
                ("open_clan", "adventure"), ("open_boss", "lobby"), ("open_formal", "boss_detail"),
                ("open_formation", "target"), ("cancel_formation", "formation"), ("back_to_lobby", "boss_detail"),
                ("back_to_adventure", "lobby"), ("open_support", "formation"), ("support_back", "support"),
                ("characters_top", "formation"), ("characters_next", "formation"), ("support_top", "support"),
                ("support_next", "support"), ("start", "formation"), ("open_used", "lobby"), ("close_used", "used_units"),
                ("open_carry", "lobby"), ("close_carry", "carry"), ("close_empty_carry", "carry_empty"),
                ("open_carry_for_target", "target"), ("select_carry", "carry"), ("return_to_lobby", "result")
            };
            var actions = new JObject();
            foreach (var (name, page) in actionPages)
            {
                var action = new JObject { ["page"] = page, ["label"] = Node(), ["target"] = Roi() };
                if (name is "characters_top" or "characters_next" or "support_top" or "support_next")
                { action["begin"] = Roi(); action["end"] = Roi(); }
                actions[name] = action;
            }
            var bossSlots = new JObject();
            foreach (var boss in new[] { "1", "2", "3", "4", "5" })
                bossSlots[boss] = new JObject { ["round"] = Roi(), ["defeated"] = Node() };
            var profile = new JObject
            {
                ["version"] = version, ["calibration_id"] = "synthetic-capabilities", ["server"] = "cn-bilibili",
                ["frame_size"] = new JArray(1280, 720), ["evidence_kind"] = "device_saved_frame",
                ["battle_hud"] = "guide-trial-1280x720", ["actions"] = new JObject(), ["formation"] = new JObject(), ["support"] = new JObject(),
                ["pages"] = new JObject { ["main"] = new JObject { ["recognition"] = "TemplateMatch", ["template"] = "clan-synthetic/first.png", ["sha256"] = template["sha256"]!.DeepClone() } },
                ["samples"] = new JArray(Sample("main")),
                ["daily"] = new JObject
                {
                    ["version"] = 2, ["cp_semantics"] = "daily_earned", ["pages"] = dailyPages,
                    ["samples"] = new JArray(dailyPages.Properties().Select(page => Sample(page.Name))),
                    ["execution"] = new JObject
                    {
                        ["pages"] = pages, ["actions"] = actions, ["used_units"] = Members(15),
                        ["carry_slots"] = new JArray(new JObject { ["empty"] = Node(), ["occupied"] = Node(),
                            ["stock_index"] = Roi(), ["time"] = Roi(), ["members"] = Members(5),
                            ["support_owner"] = Roi(), ["target"] = Roi() }),
                        ["boss_slots"] = bossSlots, ["formation_owner"] = Roi(),
                        ["formation_mode"] = new JObject { ["regular"] = Node(), ["carryover"] = Node(), ["stock_index"] = Roi(), ["time"] = Roi() },
                        ["speed_one"] = Node(), ["samples"] = new JArray(pages.Properties().Select(page => Sample(page.Name)))
                    }
                }
            };
            if (version == 2)
            {
                var frame = Sample("simulation-result");
                frame["page"] = "result";
                ((JArray)profile["samples"]!).Add(frame);
                profile["pages"]!["result"] = new JObject
                {
                    ["fields"] = new JObject { ["damage"] = new JArray(80, 45, 332, 42) },
                    ["damage_line"] = new JObject { ["sample_sha256"] = frame["sha256"]!.DeepClone(),
                        ["verify_roi"] = new JArray(88, 47, 324, 38) }
                };
            }
            if (version == 2) profile["daily"]!["execution"]!["opening"] = new JObject
            {
                ["battle_speed"] = 2, ["opening_guards"] = new JArray(Enumerable.Range(0, 3).Select(_ => Node())),
                ["paused_guards"] = new JArray(Enumerable.Range(0, 3).Select(_ => Node())), ["clock"] = new JArray(1, 1, 8, 8),
                ["auto_on"] = Node(), ["auto_off"] = Node(), ["speed"] = new JObject { ["1"] = Node(), ["2"] = Node() },
                ["speed_two"] = Node(), ["set_badge"] = Node(), ["empty_tp"] = Node(),
                ["targets"] = new JObject { ["pause"] = new JArray(1, 1, 8, 8), ["auto"] = new JArray(1, 1, 8, 8),
                    ["speed"] = new JArray(1, 1, 8, 8), ["resume"] = new JArray(1, 1, 8, 8),
                    ["sets"] = new JArray(Enumerable.Range(0, 5).Select(_ => new JArray(1, 1, 8, 8))) },
                ["inheritance"] = new JObject { ["battle_selected"] = Node(), ["radio_on"] = Node(), ["radio_off"] = Node(),
                    ["samples"] = new JArray(Sample("inheritance")) },
                ["samples"] = new JArray(Sample("battle"), Sample("paused"), Sample("battle_two"))
            };
            var measured = new JObject
            {
                ["kind"] = "owner_native_clan_timing", ["calibration_id"] = profile["calibration_id"]!.DeepClone(),
                ["battle_hud"] = profile["battle_hud"]!.DeepClone(), ["observation_scope"] = "full_read_tick",
                ["action_scope"] = "toggle_dispatch_and_full_team_readback", ["battle_speed"] = 1,
                ["observation_intervals_ms"] = new JArray(Enumerable.Repeat(1, 20)), ["action_latencies_ms"] = new JArray(Enumerable.Repeat(1, 20)),
                ["game_second_ms"] = new JArray(Enumerable.Repeat(1000, 20))
            };
            profile["timing_measurements"] = Dependency("assets/resource/clan-battle-calibrations/timing.json", measured.ToString(Formatting.None));
            return profile;
        }
        JObject SetResetSpec() => new()
        {
            ["description"] = new JObject { ["recognition"] = "OCR", ["roi"] = new JArray(1, 1, 8, 8), ["expected"] = "^发动连结爆发时解除连结爆发立即发动模式$" },
            ["on_label"] = new JObject { ["recognition"] = "OCR", ["roi"] = new JArray(1, 1, 8, 8), ["expected"] = "^开启$" },
            ["off_label"] = new JObject { ["recognition"] = "OCR", ["roi"] = new JArray(1, 1, 8, 8), ["expected"] = "^关闭$" }
        };
        JObject AutoProfile(bool legacy = false)
        {
            var profile = Profile();
            var opening = (JObject)profile["daily"]!["execution"]!["opening"]!;
            var paused = (JArray)opening["paused_guards"]!.DeepClone();
            paused[2] = new JObject { ["recognition"] = "OCR", ["roi"] = new JArray(1, 1, 8, 8),
                ["expected"] = "^(?:1[:：](?:[0-2][0-9]|30)|0[:：][0-5][0-9])$" };
            var auto = new JObject
            {
                ["speeds"] = new JArray(1, 2), ["opening_guards"] = opening["opening_guards"]!.DeepClone(),
                ["paused_guards"] = paused, ["auto_on"] = opening["auto_on"]!.DeepClone(), ["auto_off"] = opening["auto_off"]!.DeepClone(),
                ["speed"] = new JObject { ["1"] = opening["speed"]!["1"]!.DeepClone(), ["2"] = opening["speed"]!["2"]!.DeepClone(), ["4"] = opening["speed"]!["2"]!.DeepClone() },
                ["set_badge"] = opening["set_badge"]!.DeepClone(), ["samples"] = new JArray(Sample("paused")),
                ["inheritance"] = opening["inheritance"]!.DeepClone()
            };
            if (legacy)
            {
                var clock = (JObject)opening["set_badge"]!.DeepClone();
                clock["template"] = new JArray(clock["template"]!.DeepClone(), clock["template"]!.DeepClone());
                clock["sha256"] = new JArray(clock["sha256"]!.DeepClone(), clock["sha256"]!.DeepClone());
                paused[2] = clock; paused.Add(opening["set_badge"]!.DeepClone());
                auto["empty_tp"] = opening["empty_tp"]!.DeepClone();
            }
            if (!legacy) auto["inheritance"]!["ub_auto_clear"] = SetResetSpec();
            profile["pages"]!["battle"] = new JObject { ["speed_two"] = opening["speed_two"]!.DeepClone() };
            profile["auto_battle"] = auto;
            return profile;
        }
        JObject Index(int version, JObject profile, params string[] capabilities)
        {
            File.WriteAllText(profilePath, profile.ToString(Formatting.None), new UTF8Encoding(false));
            var entry = new JObject
            {
                ["calibration_id"] = "synthetic-capabilities", ["server"] = "cn-bilibili", ["content_version"] = contentVersion,
                ["frame_size"] = new JArray(1280, 720), ["profile_path"] = "assets/resource/clan-battle-calibrations/synthetic.json",
                ["profile_sha256"] = PcrClanGuide.Hash(File.ReadAllBytes(profilePath))
            };
            if (version == 2) entry["capabilities"] = new JArray(capabilities);
            return new JObject { ["version"] = version, ["entries"] = new JArray(entry) };
        }
        PcrClanCalibration Load(JObject index, string version = contentVersion)
        {
            File.WriteAllText(indexPath, index.ToString(Formatting.None), new UTF8Encoding(false));
            return Read(version);
        }
        JObject Entry(JObject index) => (JObject)index["entries"]![0]!;
        JObject Plan(bool auto = true, bool timed = false) => new()
        {
            ["content_version"] = contentVersion, ["auto"] = auto, ["support_mode"] = "none",
            ["switches"] = timed ? new JArray(new JObject { ["remaining_seconds"] = 60, ["auto"] = false, ["set_by_unit"] = new JObject() }) : new JArray()
        };
        void Unavailable(PcrClanCalibration value, string name) => Check(value.Error != null && !value.Capabilities.Any()
            && !value.Supports("simulation") && !value.Supports("formal"), name);

        var bundledIndex = Path.Combine(sourceRoot, PcrClanCalibration.IndexPath);
        Check(PcrClanGuide.Hash(File.ReadAllBytes(bundledIndex)) == PcrClanCalibration.IndexSha256, "gui_pin_matches_bundled_index");
        var python = File.ReadAllText(Path.Combine(sourceRoot, "src/pcr_maa/clan_battle_calibration.py"), Encoding.UTF8);
        Check(Regex.Match(python, "INDEX_SHA256 = '([0-9a-f]{64})'").Groups[1].Value == PcrClanCalibration.IndexSha256,
            "python_and_gui_share_index_pin");
        Unavailable(PcrClanCalibration.Read(root, contentVersion), "missing_index_is_unavailable");
        var index = Index(2, Profile(), "simulation");
        var limited = Load(index);
        Check(limited.Supports("simulation") && !limited.Supports("timed") && !limited.Supports("formal") && !limited.Supports("carryover"),
            "v2_only_grants_declared_capabilities");
        var trailingRoot = (PcrClanCalibration)reader.Invoke(null, new object?[]
            { root + Path.DirectorySeparatorChar, contentVersion, PcrClanGuide.Hash(File.ReadAllBytes(indexPath)) })!;
        Check(trailingRoot.Supports("simulation"), "root_trailing_separator_is_supported");
        limited.Require(Plan(false), "simulation"); count++;
        Reject(() => limited.Require(Plan(), "formal"), "formal_missing", "capability_missing:formal");
        Reject(() => limited.Require(Plan(timed: true), "simulation"), "timed_missing", "capability_missing:timed");
        Unavailable(PcrClanCalibration.Read(root, contentVersion), "public_reader_does_not_trust_synthetic_index");
        Unavailable(Read(digest: new string('0', 64)), "wrong_index_hash");
        Unavailable(Read("202610250002"), "missing_content_version");
        Unavailable(Read(null), "missing_account_content_version");

        JObject SimulationTarget(string phase = "C", int boss = 1, string period = "2026-11") =>
            new() { ["period"] = period, ["phase"] = phase, ["boss"] = boss };
        JObject TargetPlan(string phase, int boss, string period = "2026-11")
        {
            var plan = Plan();
            plan.Merge(SimulationTarget(phase, boss, period));
            return plan;
        }
        JObject WithTargets(JToken targets, params string[] choices)
        {
            var profile = Profile();
            var marker = profile["daily"]!["execution"]!["opening"]!["paused_guards"]![0]!;
            var choice = new JObject { ["label"] = marker.DeepClone(), ["selected"] = marker.DeepClone() };
            profile["actions"]!["select_difficulty"] = new JObject { ["page"] = "difficulty",
                ["choices"] = new JObject(choices.Select(phase => new JProperty(phase, choice.DeepClone()))) };
            profile["simulation_targets"] = targets;
            return profile;
        }
        PcrClanCalibration Scoped(JToken targets, params string[] choices) =>
            Load(Index(2, WithTargets(targets, choices), "simulation", "formal", "carryover"));
        var c1Only = Scoped(new JArray(SimulationTarget()), "C");
        Check(c1Only.Summary.Contains("2026-11") && c1Only.Summary.Contains("C1"), "summary_names_reviewed_target");
        c1Only.Require(TargetPlan("C", 1), "simulation"); count++;
        c1Only.Require(TargetPlan("BC", 1), "simulation"); count++;
        const string deniedTarget = "clan_battle_simulation_target_uncalibrated";
        foreach (int boss in Enumerable.Range(2, 4))
            Reject(() => c1Only.Require(TargetPlan("C", boss), "simulation"), $"unreviewed_c{boss}", deniedTarget);
        foreach (var phase in new[] { "B", "D", "E" })
            Reject(() => c1Only.Require(TargetPlan(phase, 1), "simulation"), $"unreviewed_{phase}1", deniedTarget);
        Reject(() => c1Only.Require(TargetPlan("C", 1, "2026-12"), "simulation"), "different_month_same_version", deniedTarget);
        Reject(() => c1Only.Require(Plan(), "simulation"), "missing_target_in_scoped_plan", deniedTarget);
        foreach (string capability in new[] { "formal", "carryover" })
        { c1Only.Require(TargetPlan("D", 5), capability, 2); count++; }
        limited.Require(TargetPlan("D", 5), "simulation"); count++;
        var availableBC = Scoped(new JArray(SimulationTarget()), "B", "C");
        availableBC.Require(TargetPlan("BC", 1), "simulation"); count++;
        Reject(() => availableBC.Require(TargetPlan("B", 1), "simulation"), "b_present_only_for_navigation", deniedTarget);
        var splitTargets = Scoped(new JArray(SimulationTarget("B", 2), SimulationTarget("C", 1)), "B", "C");
        splitTargets.Require(TargetPlan("BC", 2), "simulation"); count++;
        Reject(() => splitTargets.Require(TargetPlan("C", 2), "simulation"), "do_not_cross_matching_target_pairs", deniedTarget);

        // Supply a verified synthetic calibration to the editor, then exercise
        // its text-change events without replacing the installation's pinned index.
        var targetView = new PcrClanGuideView(sourceRoot, Path.Combine(root, "editor", "guide.json"), Path.Combine(root, "editor"));
        var viewType = typeof(PcrClanGuideView);
        FieldInfo Field(string name) => viewType.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!;
        Field("displayCalibration").SetValue(targetView, availableBC);
        var editor = (Dictionary<string, TextBox>)Field("fields").GetValue(targetView)!;
        var currentTarget = (TextBlock)Field("calibrationSummary").GetValue(targetView)!;
        editor["period"].Text = "2026-11"; editor["phase"].Text = "BC"; editor["boss"].Text = "1";
        Check(currentTarget.Text!.Contains("本队目标为 2026-11 C1"), "editor_shared_guide_uses_permitted_c");
        editor["boss"].Text = "2";
        Check(currentTarget.Text!.Contains("不可用") && currentTarget.Text.Contains("2026-11 C1"), "editor_boss_edit_shows_permitted_target");
        editor["boss"].Text = "1"; editor["period"].Text = "2026-12";
        Check(currentTarget.Text!.Contains("不可用"), "editor_period_change_disables_target");
        editor["period"].Text = "2026-11"; editor["phase"].Text = "B";
        Check(currentTarget.Text!.Contains("不可用"), "editor_phase_change_disables_target");
        var both = Scoped(new JArray(SimulationTarget("C"), SimulationTarget("B")), "C", "B");
        Field("displayCalibration").SetValue(targetView, both);
        editor["phase"].Text = "BC";
        Check(currentTarget.Text!.Contains("本队目标为 2026-11 B1"), "editor_shared_guide_prefers_b_when_both_allowed");

        var extraTargetField = SimulationTarget(); extraTargetField["extra"] = true;
        var targetWithTemplate = SimulationTarget();
        targetWithTemplate["extra"] = new JObject { ["recognition"] = "TemplateMatch", ["template"] = "clan-synthetic/missing.png", ["sha256"] = new string('0', 64) };
        var missingBoss = SimulationTarget(); missingBoss.Remove("boss");
        var oneHundred = new JArray(Enumerable.Range(2000, 100).Select(year => SimulationTarget(period: $"{year}-11")));
        Check(Scoped(oneHundred, "C").Supports("simulation"), "scope_accepts_one_hundred_targets");
        oneHundred.Add(SimulationTarget(period: "2100-11"));
        foreach (var (name, targets) in new (string, JToken)[] {
            ("null", JValue.CreateNull()), ("not_array", SimulationTarget()), ("empty", new JArray()),
            ("over_limit", oneHundred), ("duplicate", new JArray(SimulationTarget(), SimulationTarget())),
            ("string_row", new JArray("C1")), ("extra_field", new JArray(extraTargetField)),
            ("embedded_template", new JArray(targetWithTemplate)),
            ("missing_boss", new JArray(missingBoss)), ("month_00", new JArray(SimulationTarget(period: "2026-00"))),
            ("month_13", new JArray(SimulationTarget(period: "2026-13"))),
            ("non_ascii_period", new JArray(SimulationTarget(period: "２０２６-11"))),
            ("combined_phase", new JArray(SimulationTarget("BC"))), ("missing_difficulty", new JArray(SimulationTarget("D"))),
            ("boss_0", new JArray(SimulationTarget(boss: 0))), ("boss_6", new JArray(SimulationTarget(boss: 6))),
            ("float_boss", JArray.Parse("[{\"period\":\"2026-11\",\"phase\":\"C\",\"boss\":1.0}]")),
            ("bool_boss", JArray.Parse("[{\"period\":\"2026-11\",\"phase\":\"C\",\"boss\":true}]"))
        })
        {
            var malformed = Scoped(targets, "C");
            Check(!malformed.Supports("simulation") && malformed.Supports("formal") && malformed.Supports("carryover"),
                "invalid_target_blocks_simulation_only_" + name);
            Reject(() => malformed.Require(TargetPlan("C", 1), "simulation"), "invalid_scope_at_start_" + name,
                "clan_battle_simulation_targets_invalid");
        }
        var scopedLegacy = WithTargets(new JArray(SimulationTarget()), "C");
        scopedLegacy["version"] = 1;
        Check(!Load(Index(2, scopedLegacy, "simulation")).Supports("simulation"), "scoped_targets_require_v2_profile");

        foreach (var (name, change) in new (string, Action<JObject>)[] {
            ("missing_binding", p => ((JObject)p["pages"]!["result"]!).Remove("damage_line")),
            ("extra_field", p => p["pages"]!["result"]!["damage_line"]!["extra"] = 1),
            ("unbound_sample", p => p["pages"]!["result"]!["damage_line"]!["sample_sha256"] = new string('a', 64)),
            ("other_page", p => p["pages"]!["result"]!["damage_line"]!["sample_sha256"] = p["samples"]![0]!["sha256"]!.DeepClone()),
            ("formal_result_only", p => p["pages"]!["result"]!["damage_line"]!["sample_sha256"] =
                ((JArray)p["daily"]!["execution"]!["samples"]!).Single(row => (string?)row["page"] == "result")["sha256"]!.DeepClone()),
            ("same_region", p => p["pages"]!["result"]!["damage_line"]!["verify_roi"] = p["pages"]!["result"]!["fields"]!["damage"]!.DeepClone()),
            ("outside_primary", p => p["pages"]!["result"]!["damage_line"]!["verify_roi"] = new JArray(88, 47, 325, 38)),
            ("fractional_coordinate", p => p["pages"]!["result"]!["damage_line"]!["verify_roi"]![0] = 88.5),
            ("primary_out_of_frame", p => p["pages"]!["result"]!["fields"]!["damage"] = new JArray(80, 45, 1280, 42)),
        })
        {
            var invalid = Profile();
            change(invalid);
            var rejected = Load(Index(2, invalid, "simulation"));
            Check(!rejected.Supports("simulation"), "damaged_simulation_result_hidden_" + name);
            Reject(() => rejected.Require(Plan(), "simulation"), "damaged_simulation_result_" + name,
                "clan_battle_result_damage_uncalibrated");
        }

        var legacyDamage = Profile();
        var legacyResult = (JObject)legacyDamage["pages"]!["result"]!;
        legacyResult.Remove("damage_line");
        legacyResult["fields"]!["damage"] = new JArray(88, 47, 162, 38);
        var onlyFormal = Load(Index(2, legacyDamage, "simulation", "formal", "carryover"));
        Check(!onlyFormal.Supports("simulation") && onlyFormal.Supports("formal") && onlyFormal.Supports("carryover"),
            "unbound_result_only_disables_simulation");
        onlyFormal.Require(Plan(), "formal", 2);
        onlyFormal.Require(Plan(), "carryover", 2);
        legacyDamage["calibration_id"] = "cn-bilibili-202610141448-clan-simulation";
        PcrClanCalibration OctoberLayout(string version = "202610141448")
        {
            var octoberIndex = Index(2, legacyDamage, "simulation");
            Entry(octoberIndex)["calibration_id"] = legacyDamage["calibration_id"]!.DeepClone();
            Entry(octoberIndex)["content_version"] = version;
            return Load(octoberIndex, version);
        }
        Check(OctoberLayout().Supports("simulation"), "reviewed_october_result_layout_compatible");
        var novemberWithOctoberDamage = OctoberLayout("202611131613");
        Check(!novemberWithOctoberDamage.Supports("simulation"), "new_month_cannot_borrow_october_damage_layout");
        Reject(() => novemberWithOctoberDamage.Require(Plan(), "simulation"), "rebound_october_result_refused_at_start",
            "clan_battle_result_damage_uncalibrated");
        legacyResult["fields"]!["damage"]![2] = 163;
        Check(!OctoberLayout().Supports("simulation"), "october_id_with_changed_result_region_is_unreviewed");

        var full = Load(Index(2, Profile(), "simulation", "timed", "formal", "carryover"));
        full.Require(Plan(timed: true), "simulation"); count++;
        foreach (var capability in new[] { "formal", "carryover" })
        {
            full.Require(Plan(), capability, 2); count++;
            Reject(() => full.Require(Plan(timed: true), capability, 2), "formal_axis_rejected_" + capability, "daily_auto_only");
            foreach (var speed in new[] { 1, 4 })
                Reject(() => full.Require(Plan(), capability, speed), "formal_fixed_two_" + capability + speed, "daily_speed_invalid");
        }
        Check(full.FormalOpening, "formal_opening_is_separately_validated");
        var noRecords = Profile(); var noRecordExecution = (JObject)noRecords["daily"]!["execution"]!;
        noRecordExecution.Remove("used_units"); ((JObject)noRecordExecution["pages"]!).Remove("used_units");
        foreach (var action in new[] { "open_used", "close_used" }) ((JObject)noRecordExecution["actions"]!).Remove(action);
        noRecordExecution["samples"] = new JArray(((JArray)noRecordExecution["samples"]!).Where(row => (string?)row["page"] != "used_units").Select(row => row.DeepClone()));
        var noRecordCalibration = Load(Index(2, noRecords, "formal", "carryover"));
        Check(noRecordCalibration.FormalOpening && noRecordCalibration.Supports("formal") && noRecordCalibration.Supports("carryover"),
            "formal_calibration_can_omit_whole_personal_record_group");
        JObject RegularOnly(bool removeSupport, bool removeCarry)
        {
            var profile = Profile(); profile["support_modes"] = new JArray("none");
            var execution = (JObject)profile["daily"]!["execution"]!;
            var pages = (JObject)execution["pages"]!; var actions = (JObject)execution["actions"]!;
            var removed = new HashSet<string>();
            if (removeSupport)
            {
                pages.Remove("support"); removed.Add("support"); execution["formation_owner"] = JValue.CreateNull();
                foreach (var action in new[] { "open_support", "support_back", "support_top", "support_next" }) actions.Remove(action);
            }
            if (removeCarry)
            {
                pages.Remove("carry"); removed.Add("carry"); execution["carry_slots"] = new JArray();
                execution["formation_mode"]!["stock_index"] = JValue.CreateNull(); execution["formation_mode"]!["time"] = JValue.CreateNull();
                foreach (var action in new[] { "close_carry", "open_carry_for_target", "select_carry" }) actions.Remove(action);
            }
            execution["samples"] = new JArray(((JArray)execution["samples"]!).Where(row => !removed.Contains((string)row["page"]!)).Select(row => row.DeepClone()));
            return profile;
        }
        foreach (var (support, carry) in new[] { (true, false), (false, true), (true, true) })
        {
            var regularProfile = RegularOnly(support, carry);
            var regular = Load(Index(2, regularProfile, "simulation", "formal"));
            Check(regular.Supports("formal") && !regular.Supports("carryover"), "regular_only_groups_" + support + carry);
            regular.Require(Plan(), "formal", 2); count++;
            Reject(() => regular.Require(Plan(), "carryover", 2), "regular_does_not_grant_carryover_" + support + carry, "capability_missing:carryover");
            if (carry)
                Unavailable(Load(Index(2, regularProfile, "formal", "carryover")), "carryover_requires_carry_group_" + support + carry);
            else
            {
                var ownCarry = Load(Index(2, regularProfile, "formal", "carryover"));
                Check(ownCarry.OwnUnitsOnly && ownCarry.FormalOpening && ownCarry.Supports("carryover"),
                    "own_carryover_can_omit_whole_support_group");
                ownCarry.Require(Plan(), "carryover", 2); count++;
                var borrowed = Plan(); borrowed["support_mode"] = "specified"; borrowed["support_unit_id"] = 1;
                Reject(() => ownCarry.Require(borrowed, "carryover", 2), "own_carryover_rejects_borrowed_team", "support_mode_uncalibrated");
            }
            var borrowedScope = (JObject)regularProfile.DeepClone(); borrowedScope.Remove("support_modes");
            Unavailable(Load(Index(2, borrowedScope, "formal")), "partial_requires_own_only_" + support + carry);
            var simulation = Load(Index(2, regularProfile, "simulation"));
            Check(simulation.Supports("simulation") && !simulation.Supports("formal"), "simulation_cannot_grant_formal_" + support + carry);
            Reject(() => simulation.Require(Plan(), "formal", 2), "simulation_cannot_run_formal_" + support + carry, "capability_missing:formal");
        }
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("support_action", value => value["daily"]!["execution"]!["actions"]!["support_next"] = new JObject()),
            ("support_owner", value => value["daily"]!["execution"]!["formation_owner"] = new JArray(1, 1, 8, 8)),
            ("carry_members", value => ((JArray)value["daily"]!["execution"]!["carry_slots"]![0]!["members"]!["slots"]!).RemoveAt(0)),
            ("carry_owner", value => value["daily"]!["execution"]!["carry_slots"]![0]!["support_owner"] = JValue.CreateNull()),
            ("carry_target", value => value["daily"]!["execution"]!["carry_slots"]![0]!["target"] = JValue.CreateNull()),
            ("carry_mode", value => value["daily"]!["execution"]!["formation_mode"]!["carryover"] = JValue.CreateNull()),
        })
        {
            var profile = RegularOnly(true, false); change.Change(profile);
            Unavailable(Load(Index(2, profile, "formal", "carryover")), "own_carryover_scope_rejected_" + change.Name);
        }
        var borrowedCarryScope = RegularOnly(true, false); borrowedCarryScope.Remove("support_modes");
        Unavailable(Load(Index(2, borrowedCarryScope, "formal", "carryover")), "carryover_without_support_requires_own_only");
        JObject CarrySelection(bool ownScope = true, bool compact = true)
        {
            var profile = ownScope ? RegularOnly(true, false) : Profile();
            var execution = (JObject)profile["daily"]!["execution"]!;
            var stock = (JObject)execution["carry_slots"]![0]!;
            var reuseOn = stock["occupied"]!.DeepClone(); var reuseOff = stock["empty"]!.DeepClone();
            if (compact)
            {
                foreach (var field in new[] { "empty", "occupied", "support_owner" }) stock.Remove(field);
                stock["members"] = new JObject { ["slots"] = stock["members"]!["slots"]!.DeepClone() };
            }
            execution["pages"]!["carry_selection"] = execution["pages"]!["carry"]!.DeepClone();
            execution["carry_selection"] = new JObject { ["slots"] = execution["carry_slots"]!.DeepClone(),
                ["reuse_on"] = reuseOn, ["reuse_off"] = reuseOff };
            execution["actions"]!["select_carry"]!["page"] = "carry_selection";
            execution["actions"]!["enable_carry_team"] = execution["actions"]!["select_carry"]!.DeepClone();
            execution["actions"]!["close_carry_selection"] = execution["actions"]!["select_carry"]!.DeepClone();
            ((JArray)execution["samples"]!).Add(Sample("carry_selection"));
            return profile;
        }
        foreach (var (selectionOwnOnly, compact) in new[] { (true, true), (true, false), (false, false) })
        {
            var selectedCarry = Load(Index(2, CarrySelection(selectionOwnOnly, compact), "formal", "carryover"));
            Check(selectedCarry.FormalOpening && selectedCarry.Supports("carryover"), "separate_carry_selection_" + selectionOwnOnly + compact);
            selectedCarry.Require(Plan(), "carryover", 2); count++;
        }
        Unavailable(Load(Index(2, CarrySelection(false, true), "formal", "carryover")), "compact_carry_slots_require_own_scope");
        var selectedFormationProfile = CarrySelection();
        foreach (var key in new[] { "carryover", "stock_index", "time" }) selectedFormationProfile["daily"]!["execution"]!["formation_mode"]![key] = JValue.CreateNull();
        var selectedFormation = Load(Index(2, selectedFormationProfile, "formal", "carryover"));
        Check(selectedFormation.OwnUnitsOnly && selectedFormation.Supports("carryover"), "carry_selection_can_bind_unmarked_formation");
        selectedFormation.Require(Plan(), "carryover", 2); count++;
        foreach (var key in new[] { "carryover", "stock_index", "time" })
        {
            var mixed = (JObject)selectedFormationProfile.DeepClone();
            mixed["daily"]!["execution"]!["formation_mode"]![key] = CarrySelection()["daily"]!["execution"]!["formation_mode"]![key]!.DeepClone();
            Unavailable(Load(Index(2, mixed, "formal", "carryover")), "carry_selection_rejects_mixed_formation_null_" + key);
        }
        var borrowedUnmarked = CarrySelection(false, false);
        foreach (var key in new[] { "carryover", "stock_index", "time" }) borrowedUnmarked["daily"]!["execution"]!["formation_mode"]![key] = JValue.CreateNull();
        Unavailable(Load(Index(2, borrowedUnmarked, "formal", "carryover")), "unmarked_carry_formation_requires_own_scope");
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("group_missing", value => ((JObject)value["daily"]!["execution"]!).Remove("carry_selection")),
            ("group_null", value => value["daily"]!["execution"]!["carry_selection"] = JValue.CreateNull()),
            ("page_missing", value => ((JObject)value["daily"]!["execution"]!["pages"]!).Remove("carry_selection")),
            ("sample_missing", value => value["daily"]!["execution"]!["samples"] = new JArray(((JArray)value["daily"]!["execution"]!["samples"]!).Where(row => (string?)row["page"] != "carry_selection").Select(row => row.DeepClone()))),
            ("enable_missing", value => ((JObject)value["daily"]!["execution"]!["actions"]!).Remove("enable_carry_team")),
            ("close_missing", value => ((JObject)value["daily"]!["execution"]!["actions"]!).Remove("close_carry_selection")),
            ("close_wrong_page", value => value["daily"]!["execution"]!["actions"]!["close_carry_selection"]!["page"] = "carry"),
            ("select_wrong_page", value => value["daily"]!["execution"]!["actions"]!["select_carry"]!["page"] = "carry"),
            ("enable_wrong_page", value => value["daily"]!["execution"]!["actions"]!["enable_carry_team"]!["page"] = "carry"),
            ("inventory_missing", value => ((JObject)value["daily"]!["execution"]!["pages"]!).Remove("carry")),
            ("slots_empty", value => value["daily"]!["execution"]!["carry_selection"]!["slots"] = new JArray()),
            ("members_short", value => ((JArray)value["daily"]!["execution"]!["carry_selection"]!["slots"]![0]!["members"]!["slots"]!).RemoveAt(0)),
            ("target_missing", value => ((JObject)value["daily"]!["execution"]!["carry_selection"]!["slots"]![0]!).Remove("target")),
            ("time_invalid", value => value["daily"]!["execution"]!["carry_selection"]!["slots"]![0]!["time"] = JValue.CreateNull()),
            ("reuse_invalid", value => value["daily"]!["execution"]!["carry_selection"]!["reuse_on"] = new JObject()),
            ("group_extra", value => value["daily"]!["execution"]!["carry_selection"]!["extra"] = true),
            ("inventory_members_short", value => ((JArray)value["daily"]!["execution"]!["carry_slots"]![0]!["members"]!["slots"]!).RemoveAt(0)),
        })
        {
            var profile = CarrySelection(); change.Change(profile);
            Unavailable(Load(Index(2, profile, "formal", "carryover")), "carry_selection_rejected_" + change.Name);
        }
        var regularNullProfile = RegularOnly(true, true);
        regularNullProfile["daily"]!["execution"]!["formation_mode"]!["carryover"] = JValue.CreateNull();
        var regularNull = Load(Index(2, regularNullProfile, "simulation", "formal"));
        Check(regularNull.OwnUnitsOnly && regularNull.FormalOpening && regularNull.Supports("formal")
            && !regularNull.Supports("carryover"), "explicit_null_carry_marker_is_regular_own_only");
        regularNull.Require(Plan(), "formal", 2); count++;
        Reject(() => regularNull.Require(Plan(), "carryover", 2), "null_carry_marker_does_not_grant_carryover", "capability_missing:carryover");
        Unavailable(Load(Index(2, regularNullProfile, "formal", "carryover")), "null_carry_marker_cannot_claim_carryover");
        foreach (var (support, carry) in new[] { (false, false), (true, false), (false, true) })
        {
            var profile = RegularOnly(support, carry);
            profile["daily"]!["execution"]!["formation_mode"]!["carryover"] = JValue.CreateNull();
            Unavailable(Load(Index(2, profile, "formal")), "null_carry_marker_requires_both_groups_omitted_" + support + carry);
        }
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("missing_carry_key", value => ((JObject)value["daily"]!["execution"]!["formation_mode"]!).Remove("carryover")),
            ("missing_regular_key", value => ((JObject)value["daily"]!["execution"]!["formation_mode"]!).Remove("regular")),
            ("null_regular", value => value["daily"]!["execution"]!["formation_mode"]!["regular"] = JValue.CreateNull()),
            ("malformed_regular", value => value["daily"]!["execution"]!["formation_mode"]!["regular"] = new JObject()),
            ("missing_own_scope", value => value.Remove("support_modes")),
            ("non_exact_own_scope", value => value["support_modes"] = new JArray("none", "qsjs")),
            ("null_own_scope", value => value["support_modes"] = JValue.CreateNull()),
        })
        {
            var profile = (JObject)regularNullProfile.DeepClone(); change.Change(profile);
            Unavailable(Load(Index(2, profile, "formal")), "null_carry_marker_rejected_" + change.Name);
        }
        foreach (var malformed in new JToken[] { new JValue(false), new JValue(0), new JValue(""), new JObject(), new JArray() })
        {
            var profile = (JObject)regularNullProfile.DeepClone();
            profile["daily"]!["execution"]!["formation_mode"]!["carryover"] = malformed;
            Unavailable(Load(Index(2, profile, "formal")), "malformed_null_carry_marker_" + malformed.ToString(Formatting.None));
        }
        var nullSimulation = Load(Index(2, regularNullProfile, "simulation"));
        Check(nullSimulation.Supports("simulation") && !nullSimulation.Supports("formal"), "null_carry_marker_simulation_cannot_grant_formal");
        Reject(() => nullSimulation.Require(Plan(), "formal", 2), "null_carry_marker_simulation_cannot_run_formal", "capability_missing:formal");
        var nullLegacy = Load(Index(1, regularNullProfile));
        Check(nullLegacy.Version == 1 && !nullLegacy.Supports("formal"), "null_carry_marker_cannot_upgrade_v1_index");
        Reject(() => nullLegacy.Require(Plan(), "formal", 2), "null_carry_marker_v1_cannot_run_formal", "capability_missing:formal");
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("support_page_only", value => ((JObject)value["daily"]!["execution"]!["pages"]!).Remove("support")),
            ("support_action", value => ((JObject)value["daily"]!["execution"]!["actions"]!).Remove("support_next")),
            ("support_sample", value => value["daily"]!["execution"]!["samples"] = new JArray(((JArray)value["daily"]!["execution"]!["samples"]!).Where(row => (string?)row["page"] != "support").Select(row => row.DeepClone()))),
            ("support_owner", value => value["daily"]!["execution"]!["formation_owner"] = JValue.CreateNull()),
            ("carry_page_only", value => ((JObject)value["daily"]!["execution"]!["pages"]!).Remove("carry")),
            ("carry_action", value => ((JObject)value["daily"]!["execution"]!["actions"]!).Remove("select_carry")),
            ("carry_sample", value => value["daily"]!["execution"]!["samples"] = new JArray(((JArray)value["daily"]!["execution"]!["samples"]!).Where(row => (string?)row["page"] != "carry").Select(row => row.DeepClone()))),
            ("carry_slots", value => value["daily"]!["execution"]!["carry_slots"] = new JArray()),
            ("carry_time", value => value["daily"]!["execution"]!["formation_mode"]!["time"] = JValue.CreateNull()),
        })
        {
            var profile = Profile(); profile["support_modes"] = new JArray("none"); change.Change(profile);
            Unavailable(Load(Index(2, profile, "formal")), "partial_group_rejected_" + change.Name);
        }
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("support_owner", value => value["daily"]!["execution"]!["formation_owner"] = new JArray(1, 1, 8, 8)),
            ("support_action", value => value["daily"]!["execution"]!["actions"]!["support_next"] = new JObject()),
            ("carry_slots", value => value["daily"]!["execution"]!["carry_slots"] = new JArray(new JObject())),
            ("carry_time", value => value["daily"]!["execution"]!["formation_mode"]!["time"] = new JArray(1, 1, 8, 8)),
            ("carry_action", value => value["daily"]!["execution"]!["actions"]!["select_carry"] = new JObject()),
        })
        {
            var profile = RegularOnly(true, true); change.Change(profile);
            Unavailable(Load(Index(2, profile, "formal")), "orphan_group_rejected_" + change.Name);
        }
        var regularLegacyIndex = Load(Index(1, RegularOnly(true, true)));
        Check(regularLegacyIndex.Version == 1 && !regularLegacyIndex.Supports("formal"), "regular_groups_cannot_change_v1_index");
        Reject(() => regularLegacyIndex.Require(Plan(), "formal", 2), "v1_regular_cannot_run_formal", "capability_missing:formal");
        var noTp = Profile(); ((JObject)noTp["daily"]!["execution"]!["opening"]!).Remove("empty_tp");
        Check(Load(Index(2, noTp, "formal", "carryover")).FormalOpening, "formal_opening_does_not_require_empty_tp");
        Check(Load(Index(2, AutoProfile(), "simulation")).AutoBattleSpeeds?.SetEquals(new[] { 1, 2 }) == true,
            "dynamic_pause_clock_without_zero_damage_or_empty_tp");
        Check(Load(Index(2, AutoProfile(true), "simulation")).Supports("simulation"), "legacy_pause_clock_and_tp_key_remain_readable");
        var pausedName = AutoProfile();
        pausedName["auto_battle"]!["paused_boss_roi"] = new JArray(760, 157, 178, 40);
        Check(Load(Index(2, pausedName, "simulation")).Supports("simulation"), "simulation_paused_boss_roi_supported");
        pausedName["auto_battle"]!["paused_clock_alternate_roi"] = new JArray(1077, 18, 44, 32);
        Check(Load(Index(2, pausedName, "simulation")).Supports("simulation"), "simulation_alternate_paused_clock_supported");
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("clock_kind", value => value["paused_guards"]![2]!["recognition"] = "Unknown"),
            ("paused_boss_roi_outside_frame", value => value["paused_boss_roi"] = new JArray(1200, 157, 178, 40)),
            ("paused_alternate_clock_outside_frame", value => value["paused_clock_alternate_roi"] = new JArray(1279, 18, 44, 32)),
            ("clock_roi", value => value["paused_guards"]![2]!["roi"] = new JArray(-1, 1, 8, 8)),
            ("clock_pattern", value => value["paused_guards"]![2]!["expected"] = "unanchored"),
            ("guard_count", value => ((JArray)value["paused_guards"]!).RemoveAt(2)),
            ("optional_tp_type", value => value["empty_tp"] = true)
        })
        {
            var profile = AutoProfile(); change.Change((JObject)profile["auto_battle"]!);
            Unavailable(Load(Index(2, profile, "simulation")), "auto_opening_" + change.Name);
        }
        var formalSetReset = Profile(); formalSetReset["daily"]!["execution"]!["opening"]!["inheritance"]!["ub_auto_clear"] = SetResetSpec();
        Check(Load(Index(2, formalSetReset, "formal", "carryover")).FormalOpening, "formal_optional_set_reset_is_read_only_calibration");
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("missing_label", value => value.Remove("on_label")),
            ("wrong_recognition", value => value["description"]!["recognition"] = "TemplateMatch"),
            ("outside_roi", value => value["off_label"]!["roi"] = new JArray(1280, 1, 8, 8)),
            ("unknown_key", value => value["toggle"] = true)
        })
        {
            var profile = AutoProfile(); change.Change((JObject)profile["auto_battle"]!["inheritance"]!["ub_auto_clear"]!);
            Unavailable(Load(Index(2, profile, "simulation")), "set_reset_" + change.Name);
        }
        Check(full.Summary.Contains("模拟战：校准资料已登记") && full.Summary.Contains("秒数轴：测量资料齐全，开始前核对时限")
            && full.Summary.Contains("正式三刀：校准资料已登记") && full.Summary.Contains("补偿刀：校准资料已登记"), "summary_describes_all_capabilities");
        Check(full.Summary.Contains("不代表当前阶段已通过正常 Start 全程验收") && !full.Summary.Contains("：已校准"),
            "calibration_registration_is_not_normal_owner_acceptance");
        var empty = Load(Index(2, Profile()));
        Check(empty.Error == null && !empty.Capabilities.Any(), "empty_capabilities_mean_not_open");
        Reject(() => empty.Require(Plan(), "simulation"), "empty_capabilities_stop_simulation", "capability_missing:simulation");
        var ownProfile = Profile(); ownProfile["support_modes"] = new JArray("none");
        var ownOnly = Load(Index(2, ownProfile, "simulation", "timed", "formal", "carryover"));
        Check(ownOnly.OwnUnitsOnly && ownOnly.Summary.Contains("仅全自有队伍可用；借人身份尚未校准"), "own_only_scope_is_visible");
        ownOnly.Require(Plan(), "simulation"); count++;
        foreach (var support in new[] { "qsjs", "specified", "unknown" })
        {
            var borrowed = Plan(); borrowed["support_mode"] = support;
            Reject(() => ownOnly.Require(borrowed, "simulation"), "uncalibrated_support_" + support, "clan_battle_support_mode_uncalibrated");
            Reject(() => ownOnly.Require(borrowed, "formal", 2), "uncalibrated_formal_support_" + support, "clan_battle_support_mode_uncalibrated");
        }
        var missingSupport = Plan(); missingSupport.Remove("support_mode");
        Reject(() => ownOnly.Require(missingSupport, "simulation"), "missing_support_scope_stops", "clan_battle_support_mode_uncalibrated");
        foreach (var invalid in new JToken[] { new JArray(), new JArray("none", "none"), new JArray("qsjs"), new JArray("specified"), new JValue("none"), JValue.CreateNull() })
        {
            var profile = Profile(); profile["support_modes"] = invalid;
            Unavailable(Load(Index(2, profile, "simulation")), "invalid_support_scope_" + invalid.ToString(Formatting.None));
        }
        var legacySupport = Profile(1); legacySupport["support_modes"] = new JArray("none");
        Unavailable(Load(Index(1, legacySupport)), "v1_cannot_declare_support_scope");
        Check(!full.OwnUnitsOnly, "absent_support_scope_keeps_legacy_behavior");
        var ownWithoutSupport = Profile(); ownWithoutSupport["support_modes"] = new JArray("none"); ownWithoutSupport.Remove("support");
        Check(Load(Index(2, ownWithoutSupport, "simulation")).Supports("simulation"), "own_only_needs_no_borrowed_support_calibration");

        var accountProfile = Profile(); accountProfile["support_modes"] = new JArray("none");
        accountProfile["formation"]!["star_source"] = "account_snapshot";
        var accountCalibration = Load(Index(2, accountProfile, "simulation"));
        Check(accountCalibration.AccountStars && accountCalibration.OwnUnitsOnly, "account_star_scope");
        var accountData = new JObject
        {
            ["schema_version"] = 1, ["server"] = "cn-bilibili", ["account_id"] = "999000001", ["content_version"] = contentVersion,
            ["observed_at"] = DateTimeOffset.UtcNow.AddSeconds(-1).ToString("O"), ["unknown_fields"] = new JArray(),
            ["units"] = new JArray(Enumerable.Range(1, 5).Select(id => new JObject { ["unit_id"] = id, ["rarity"] = 6, ["battle_rarity"] = 5 }))
        };
        JObject AccountPlan(JObject data)
        {
            var file = Path.Combine(root, "synthetic-account.json");
            File.WriteAllText(file, data.ToString(Formatting.None), new UTF8Encoding(false));
            var value = Plan(); value["server"] = "cn-bilibili"; value["account_id"] = "999000001";
            value["unit_ids"] = new JArray(Enumerable.Range(1, 5)); value["stars"] = new JArray(Enumerable.Repeat(5, 5));
            value["snapshot"] = new JObject { ["path"] = file, ["sha256"] = PcrClanGuide.Hash(File.ReadAllBytes(file)) };
            return value;
        }
        accountCalibration.Require(AccountPlan(accountData), "simulation"); count++;
        var validAccount = (JObject)accountData.DeepClone(); validAccount["observed_at"] = DateTimeOffset.UtcNow.AddHours(-23).ToString("O");
        accountCalibration.Require(AccountPlan(validAccount), "simulation"); count++;
        var staleAccount = (JObject)accountData.DeepClone(); staleAccount["observed_at"] = DateTimeOffset.UtcNow.AddHours(-25).ToString("O");
        Reject(() => accountCalibration.Require(AccountPlan(staleAccount), "simulation"), "account_stars_expired", "clan_battle_snapshot_expired");
        var wrongStar = (JObject)accountData.DeepClone(); wrongStar["units"]![0]!["battle_rarity"] = 3;
        Reject(() => accountCalibration.Require(AccountPlan(wrongStar), "simulation"), "account_stars_mismatch", "clan_battle_account_stars_mismatch");
        var unknownStar = (JObject)accountData.DeepClone(); unknownStar["unknown_fields"] = new JArray("units.1.battle_rarity");
        Reject(() => accountCalibration.Require(AccountPlan(unknownStar), "simulation"), "account_stars_unknown", "clan_battle_account_stars_unknown");
        var ownQsjsPlan = AccountPlan(accountData); ownQsjsPlan["support_mode"] = "qsjs";
        var originalQsjs = ownQsjsPlan.ToString(Formatting.None);
        accountCalibration.Require(ownQsjsPlan, "simulation"); count++;
        var ownFormal = Load(Index(2, accountProfile, "simulation", "formal", "carryover"));
        foreach (var capability in new[] { "formal", "carryover" }) { ownFormal.Require(ownQsjsPlan, capability, 2); count++; }
        Check(ownQsjsPlan.ToString(Formatting.None) == originalQsjs, "owned_qsjs_keeps_plan_and_confirmation_material");
        var fourStar = (JObject)accountData.DeepClone(); fourStar["units"]![4]!["battle_rarity"] = 4;
        var fourStarPlan = AccountPlan(fourStar); fourStarPlan["support_mode"] = "qsjs"; fourStarPlan["stars"]![4] = 4;
        accountCalibration.Require(fourStarPlan, "simulation"); count++;
        Check((int)fourStarPlan["stars"]![4]! == 4, "owned_qsjs_keeps_four_star");
        var specifiedOwn = AccountPlan(accountData); specifiedOwn["support_mode"] = "specified"; specifiedOwn["support_unit_id"] = 1;
        Reject(() => accountCalibration.Require(specifiedOwn, "simulation"), "owned_specified_still_borrowed", "support_mode_uncalibrated");
        var missingOwned = (JObject)accountData.DeepClone(); missingOwned["ownership_complete"] = true; ((JArray)missingOwned["units"]!).RemoveAt(4);
        var missingOwnPlan = AccountPlan(missingOwned); missingOwnPlan["support_mode"] = "qsjs";
        Reject(() => accountCalibration.Require(missingOwnPlan, "simulation"), "qsjs_missing_unit_still_borrowed", "support_mode_uncalibrated");
        void RejectSource(Action action, string name)
        {
            try { action(); } catch (Exception ex) when (ex is InvalidDataException or InvalidOperationException) { count++; return; }
            throw new InvalidOperationException("clan_calibration_expected_source_rejection_" + name);
        }
        var unknownOwned = (JObject)accountData.DeepClone(); unknownOwned["unknown_fields"] = new JArray("units.1.ownership");
        var unknownOwnPlan = AccountPlan(unknownOwned); unknownOwnPlan["support_mode"] = "qsjs";
        RejectSource(() => accountCalibration.Require(unknownOwnPlan, "simulation"), "qsjs_unknown_ownership");
        var changedOwnPlan = AccountPlan(accountData); changedOwnPlan["support_mode"] = "qsjs"; File.AppendAllText((string)changedOwnPlan["snapshot"]!["path"]!, "\n");
        RejectSource(() => accountCalibration.Require(changedOwnPlan, "simulation"), "qsjs_changed_source");
        var wrongOwner = (JObject)accountData.DeepClone(); wrongOwner["account_id"] = "999000002";
        var wrongOwnPlan = AccountPlan(wrongOwner); wrongOwnPlan["support_mode"] = "qsjs";
        RejectSource(() => accountCalibration.Require(wrongOwnPlan, "simulation"), "qsjs_wrong_account");
        var staleOwnPlan = AccountPlan(staleAccount); staleOwnPlan["support_mode"] = "qsjs";
        Reject(() => accountCalibration.Require(staleOwnPlan, "simulation"), "qsjs_expired_source", "snapshot_expired");
        var ambiguousSource = Profile(); ambiguousSource["formation"]!["star_source"] = "account_snapshot";
        Unavailable(Load(Index(2, ambiguousSource, "simulation")), "account_star_source_requires_own_scope");

        foreach (var key in new[] { "battle_hud", "pages", "actions", "formation", "support" })
        {
            var incomplete = Profile(); incomplete.Remove(key);
            Unavailable(Load(Index(2, incomplete, "simulation")), "missing_required_structure_" + key);
        }
        foreach (var section in new[] { "root", "daily", "execution" })
        {
            var profile = Profile();
            var samples = (JArray)(section == "root" ? profile["samples"]! : section == "daily" ? profile["daily"]!["samples"]! : profile["daily"]!["execution"]!["samples"]!);
            var samplePath = Path.Combine(root, (string)samples[0]!["path"]!);
            Load(Index(2, profile, "simulation")); File.AppendAllText(samplePath, "changed", new UTF8Encoding(false));
            Unavailable(Read(), "changed_" + section + "_sample");
            File.Delete(samplePath); Unavailable(Read(), "missing_" + section + "_sample");
        }
        var missingSamples = Profile(); missingSamples["samples"] = new JArray();
        Unavailable(Load(Index(2, missingSamples, "simulation")), "empty_sample_list");
        var badSample = Profile(); ((JObject)badSample["samples"]![0]!).Remove("page");
        Unavailable(Load(Index(2, badSample, "simulation")), "sample_reference_shape");

        var arrayProfile = Profile();
        var spec = (JObject)arrayProfile["pages"]!["main"]!;
        spec["template"] = new JArray("clan-synthetic/first.png", "clan-synthetic/second.png");
        spec["sha256"] = new JArray(new[] { "first", "second" }.Select(name => PcrClanGuide.Hash(File.ReadAllBytes(Path.Combine(root, "assets/resource/image/clan-synthetic/" + name + ".png")))));
        Check(Load(Index(2, arrayProfile, "simulation")).Supports("simulation"), "template_array_hashes_are_supported");
        var secondTemplate = Path.Combine(root, "assets/resource/image/clan-synthetic/second.png");
        File.AppendAllText(secondTemplate, "changed", new UTF8Encoding(false)); Unavailable(Read(), "template_array_second_hash_is_verified");
        File.Delete(secondTemplate); Unavailable(Read(), "template_array_missing_dependency");
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("string_hash_changed", value => value["sha256"] = new string('0', 64)),
            ("array_hash_length", value => { value["template"] = new JArray("clan-synthetic/first.png", "clan-synthetic/second.png"); value["sha256"] = new JArray(value["sha256"]!.DeepClone()); }),
            ("array_with_scalar_hash", value => value["template"] = new JArray("clan-synthetic/first.png")),
            ("template_missing", value => value.Remove("template"))
        })
        {
            var profile = Profile(); change.Change((JObject)profile["pages"]!["main"]!);
            Unavailable(Load(Index(2, profile, "simulation")), change.Name);
        }
        var timingProfile = Profile(); Load(Index(2, timingProfile, "simulation", "timed"));
        var timingPath = Path.Combine(root, (string)timingProfile["timing_measurements"]!["path"]!);
        File.AppendAllText(timingPath, " ", new UTF8Encoding(false)); Unavailable(Read(), "timing_file_changed");
        File.Delete(timingPath); Unavailable(Read(), "timing_file_missing");
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("kind", value => value["kind"] = "unbound"),
            ("calibration_id", value => value["calibration_id"] = "other"),
            ("battle_hud", value => value["battle_hud"] = new JObject { ["other"] = true }),
            ("observation_scope", value => value.Remove("observation_scope")),
            ("action_scope", value => value["action_scope"] = "dispatch_only"),
            ("battle_speed", value => value["battle_speed"] = 2),
            ("short_measurements", value => value["observation_intervals_ms"] = new JArray(1)),
            ("nonpositive_measurement", value => value["action_latencies_ms"]![0] = 0),
            ("missing_measurements", value => value.Remove("game_second_ms"))
        })
        {
            var profile = Profile(); var binding = (JObject)profile["timing_measurements"]!;
            var measured = JObject.Parse(File.ReadAllText(Path.Combine(root, (string)binding["path"]!), Encoding.UTF8)); change.Change(measured);
            profile["timing_measurements"] = Dependency((string)binding["path"]!, measured.ToString(Formatting.None));
            Unavailable(Load(Index(2, profile, "simulation", "timed")), "timing_fields_" + change.Name);
        }
        var noTiming = Profile(); noTiming.Remove("timing_measurements");
        Unavailable(Load(Index(2, noTiming, "simulation", "timed")), "missing_timing_binding");
        JObject FormalPopup(string name) => new()
        {
            ["labels"] = new JArray(Enumerable.Range(0, name == "result_reward" ? 2 : 3)
                .Select(_ => new JObject { ["recognition"] = "OCR", ["roi"] = new JArray(1, 1, 8, 8), ["expected"] = "^合成确认$" })),
            ["target"] = new JArray(1, 1, 8, 8), ["samples"] = new JArray(Sample(name))
        };
        foreach (var group in new[] { "start_confirmation", "carry_start_confirmation", "result_reward" })
        {
            var profile = Profile(); var popup = FormalPopup(group);
            profile["daily"]!["execution"]![group] = popup;
            var popupCalibration = Load(Index(2, profile, "formal", "carryover"));
            Check(popupCalibration.FormalOpening && popupCalibration.Supports("formal") && popupCalibration.Supports("carryover"),
                "optional_formal_popup_" + group);
            var nullGroup = (JObject)profile.DeepClone(); nullGroup["daily"]!["execution"]![group] = JValue.CreateNull();
            Unavailable(Load(Index(2, nullGroup, "formal", "carryover")), "null_formal_popup_" + group);
            foreach (var change in new (string Name, Action<JObject> Change)[]
            {
                ("extra_key", value => value["extra"] = true),
                ("missing_key", value => value.Remove("target")),
                ("labels_count", value => ((JArray)value["labels"]!).RemoveAt(0)),
                ("label_kind", value => value["labels"]![0]!["recognition"] = "Unknown"),
                ("label_roi", value => value["labels"]![0]!["roi"] = new JArray(-1, 1, 8, 8)),
                ("label_anchor", value => value["labels"]![0] = new JObject { ["recognition"] = "OCR", ["roi"] = new JArray(1, 1, 8, 8), ["expected"] = "确认" }),
                ("target_roi", value => value["target"] = new JArray(1, 1, 0, 8)),
                ("sample_page", value => value["samples"]![0]!["page"] = "other"),
                ("samples_count", value => ((JArray)value["samples"]!).Add(value["samples"]![0]!.DeepClone())),
                ("sample_extra_key", value => value["samples"]![0]!["extra"] = true),
                ("sample_missing_key", value => ((JObject)value["samples"]![0]!).Remove("sha256")),
                ("sample_path_escape", value => value["samples"]![0]!["path"] = "../outside.sample"),
                ("sample_hash", value => value["samples"]![0]!["sha256"] = new string('0', 64)),
            })
            {
                var changed = (JObject)profile.DeepClone(); change.Change((JObject)changed["daily"]!["execution"]![group]!);
                Unavailable(Load(Index(2, changed, "formal", "carryover")), "formal_popup_" + group + "_" + change.Name);
            }
            Load(Index(2, profile, "formal", "carryover"));
            File.AppendAllText(Path.Combine(root, (string)popup["samples"]![0]!["path"]!), "changed", new UTF8Encoding(false));
            Unavailable(Read(), "changed_formal_popup_sample_" + group);
        }
        var allPopupProfile = CarrySelection();
        foreach (var group in new[] { "start_confirmation", "carry_start_confirmation", "result_reward" })
            allPopupProfile["daily"]!["execution"]![group] = FormalPopup(group);
        Check(Load(Index(2, allPopupProfile, "formal", "carryover")).Supports("carryover"),
            "regular_and_carry_confirmations_are_independent_groups");
        var wrongCarrySample = (JObject)allPopupProfile.DeepClone();
        wrongCarrySample["daily"]!["execution"]!["carry_start_confirmation"]!["samples"]![0]!["page"] = "start_confirmation";
        Unavailable(Load(Index(2, wrongCarrySample, "formal", "carryover")), "carry_confirmation_rejects_regular_sample_page");
        var bothPopupProfile = (JObject)regularNullProfile.DeepClone();
        foreach (var group in new[] { "start_confirmation", "result_reward" })
            bothPopupProfile["daily"]!["execution"]![group] = FormalPopup(group);
        var bothPopups = Load(Index(2, bothPopupProfile, "formal"));
        Check(bothPopups.Supports("formal") && bothPopups.OwnUnitsOnly && !bothPopups.Supports("carryover"),
            "both_optional_formal_popups_preserve_regular_null_scope");
        var unknownPopup = (JObject)bothPopupProfile.DeepClone(); unknownPopup["daily"]!["execution"]!["unknown_popup"] = true;
        Unavailable(Load(Index(2, unknownPopup, "formal")), "unknown_optional_execution_key_still_rejected");
        var missingPopupRequired = (JObject)bothPopupProfile.DeepClone(); ((JObject)missingPopupRequired["daily"]!["execution"]!).Remove("speed_one");
        Unavailable(Load(Index(2, missingPopupRequired, "formal")), "optional_popups_do_not_relax_required_execution_keys");
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("daily", value => value.Remove("daily")),
            ("daily_version", value => value["daily"]!["version"] = 1),
            ("execution", value => ((JObject)value["daily"]!).Remove("execution")),
            ("execution_key", value => ((JObject)value["daily"]!["execution"]!).Remove("used_units")),
            ("used_page", value => ((JObject)value["daily"]!["execution"]!["pages"]!).Remove("used_units")),
            ("used_page_type", value => value["daily"]!["execution"]!["pages"]!["used_units"] = true),
            ("used_open", value => ((JObject)value["daily"]!["execution"]!["actions"]!).Remove("open_used")),
            ("used_close", value => ((JObject)value["daily"]!["execution"]!["actions"]!).Remove("close_used")),
            ("used_sample", value => value["daily"]!["execution"]!["samples"] =
                new JArray(((JArray)value["daily"]!["execution"]!["samples"]!).Where(row => (string?)row["page"] != "used_units").Select(row => row.DeepClone()))),
            ("used_date", value => value["daily"]!["execution"]!["pages"]!["used_units"]!["fields"] = new JObject()),
            ("execution_extra", value => value["daily"]!["execution"]!["extra"] = true)
        })
        {
            var profile = Profile(); change.Change(profile);
            Unavailable(Load(Index(2, profile, "formal", "carryover")), "formal_structure_" + change.Name);
        }

        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("missing", value => ((JObject)value["daily"]!["execution"]!).Remove("opening")),
            ("speed", value => value["daily"]!["execution"]!["opening"]!["battle_speed"] = 4),
            ("speed_type", value => value["daily"]!["execution"]!["opening"]!["battle_speed"] = 2.0),
            ("simulation_speed_cycle", value => value["daily"]!["execution"]!["opening"]!["speed"]!["4"] = value["daily"]!["execution"]!["opening"]!["speed"]!["2"]!.DeepClone()),
            ("inheritance", value => ((JObject)value["daily"]!["execution"]!["opening"]!).Remove("inheritance")),
            ("clock_bounds", value => value["daily"]!["execution"]!["opening"]!["clock"] = new JArray(long.MaxValue, 1, long.MaxValue, 8)),
            ("set_slots", value => ((JArray)value["daily"]!["execution"]!["opening"]!["targets"]!["sets"]!).RemoveAt(0)),
            ("opening_anchor", value => value["daily"]!["execution"]!["opening"]!["opening_guards"]![0]!["roi"] = new JArray(-1, 1, 8, 8)),
            ("unanchored_ocr", value => value["daily"]!["execution"]!["opening"]!["auto_on"] = new JObject { ["recognition"] = "OCR", ["roi"] = new JArray(1, 1, 8, 8), ["expected"] = "AUTO" }),
            ("sample_page", value => value["daily"]!["execution"]!["opening"]!["samples"]![0]!["page"] = "simulation"),
            ("sample_scalar", value => value["daily"]!["execution"]!["opening"]!["samples"]![0] = true),
            ("duplicate_sample", value => ((JArray)value["daily"]!["execution"]!["opening"]!["samples"]!).Add(value["daily"]!["execution"]!["opening"]!["samples"]![0]!.DeepClone()))
        })
        {
            var profile = Profile(); change.Change(profile);
            Unavailable(Load(Index(2, profile, "formal", "carryover")), "formal_opening_" + change.Name);
        }
        var shortOpening = Profile(); shortOpening["daily"]!["execution"]!["opening"]!["start_seconds"] = new JArray(90, 15);
        Check(Load(Index(2, shortOpening, "formal", "carryover")).FormalOpening, "explicit_short_opening_seconds_are_verified");
        foreach (var invalid in new JToken[] { new JArray(), new JArray(90, 90), new JArray(90, true),
            new JArray(90, 0), new JArray(90, 91), new JArray(15), new JArray(90, 15.0), new JValue(90), JValue.CreateNull() })
        {
            var profile = Profile(); profile["daily"]!["execution"]!["opening"]!["start_seconds"] = invalid;
            Unavailable(Load(Index(2, profile, "formal", "carryover")), "formal_opening_start_seconds_" + invalid.ToString(Formatting.None));
        }
        foreach (var section in new[] { "opening", "inheritance" })
        {
            var profile = Profile(); var opening = profile["daily"]!["execution"]!["opening"]!;
            var samples = (JArray)(section == "opening" ? opening["samples"]! : opening["inheritance"]!["samples"]!);
            var samplePath = Path.Combine(root, (string)samples[0]!["path"]!);
            Load(Index(2, profile, "formal", "carryover")); File.AppendAllText(samplePath, "changed", new UTF8Encoding(false));
            Unavailable(Read(), "changed_formal_" + section + "_sample");
        }
        var simulationOnly = Profile(); ((JObject)simulationOnly["daily"]!["execution"]!).Remove("opening");
        Check(Load(Index(2, simulationOnly, "simulation")).Supports("simulation"), "missing_formal_opening_preserves_simulation");

        var legacy = Load(Index(1, Profile(1)));
        Check(legacy.Version == 1 && legacy.Supports("simulation") && !legacy.Supports("formal"), "v1_is_simulation_only");
        Check(legacy.Summary.Contains("仅全程 AUTO、无切换"), "legacy_summary_explains_simulation_limit");
        legacy.Require(Plan(), "simulation"); count++;
        Reject(() => legacy.Require(Plan(false), "simulation"), "legacy_auto_required", "calibration_auto_only");
        Reject(() => legacy.Require(Plan(timed: true), "simulation"), "legacy_no_timing", "calibration_auto_only");
        var legacyIndex = Load(Index(1, Profile(2)));
        Check(legacyIndex.Version == 1, "v1_index_keeps_legacy_limit_with_v2_profile");
        Reject(() => legacyIndex.Require(Plan(false), "simulation"), "v1_index_still_requires_auto", "calibration_auto_only");
        Check(Load(Index(2, Profile(1), "simulation")).Version == 1, "v2_index_preserves_legacy_profile_limit");
        Unavailable(Load(Index(2, Profile(1), "simulation", "timed")), "legacy_profile_cannot_grant_timing");
        Unavailable(Load(Index(2, Profile(1), "formal")), "legacy_profile_cannot_grant_formal");

        foreach (var capabilities in new[] { new[] { "unknown" }, new[] { "simulation", "simulation" }, new[] { "carryover" } })
            Unavailable(Load(Index(2, Profile(), capabilities)), "invalid_capabilities_" + string.Join('_', capabilities));
        foreach (var change in new (string Name, Action<JObject> Change)[]
        {
            ("unknown_index_field", value => value["extra"] = true),
            ("unknown_index_version", value => value["version"] = 3),
            ("overflow_index_version", value => value["version"] = long.MaxValue),
            ("wrong_server", value => Entry(value)["server"] = "other"),
            ("bad_content_version", value => Entry(value)["content_version"] = "2026"),
            ("wrong_frame_size", value => Entry(value)["frame_size"] = new JArray(1920, 1080)),
            ("duplicate_identity", value => ((JArray)value["entries"]!).Add(Entry(value).DeepClone())),
            ("invalid_profile_hash", value => Entry(value)["profile_sha256"] = "not-a-digest"),
            ("escaping_profile_path", value => Entry(value)["profile_path"] = "../outside.json"),
            ("capabilities_not_array", value => Entry(value)["capabilities"] = "simulation")
        })
        {
            index = Index(2, Profile(), "simulation"); change.Change(index);
            Unavailable(Load(index), change.Name);
        }
        var mismatch = Profile(); mismatch["calibration_id"] = "different";
        Unavailable(Load(Index(2, mismatch, "simulation")), "profile_identity_mismatch");
        index = Index(2, Profile(), "simulation"); Load(index);
        File.AppendAllText(profilePath, " ", new UTF8Encoding(false));
        Unavailable(Read(), "profile_bytes_changed");
        File.Delete(profilePath);
        Unavailable(Read(), "profile_missing");
        File.WriteAllText(indexPath, "{\"version\":2,\"version\":2,\"entries\":[]}", new UTF8Encoding(false));
        Unavailable(Read(), "duplicate_json_key");

        Load(Index(2, Profile(), "formal", "carryover"));
        var request = new JObject { ["slot_plans"] = new JObject { ["1"] = Plan(), ["2"] = Plan(), ["3"] = Plan() } };
        var before = request.ToString(Formatting.None);
        Reject(() => PcrClanDailyExecution.RequireCapabilities(request, root), "formal_gate_uses_pinned_reader", "production_daily_adapter_unavailable");
        Check(request.ToString(Formatting.None) == before, "formal_gate_preserves_request");
        foreach (var invalid in new[]
        {
            new JObject(), new JObject { ["slot_plans"] = new JObject() },
            new JObject { ["slot_plans"] = new JObject { ["1"] = Plan(), ["2"] = Plan(), ["4"] = Plan() } },
            new JObject { ["slot_plans"] = new JObject { ["1"] = Plan(), ["2"] = Plan(), ["3"] = true } }
        })
        {
            try { PcrClanDailyExecution.RequireCapabilities(invalid, root); }
            catch (InvalidDataException ex) when (ex.Message.Contains("完整三目标")) { count++; continue; }
            throw new InvalidOperationException("clan_calibration_expected_rejection_incomplete_formal_targets");
        }
        var autoCalibration = new PcrClanCalibration(2, new HashSet<string> { "simulation", "timed", "formal" }, null,
            AutoBattleSpeeds: new HashSet<int> { 1, 2, 4 });
        foreach (var speed in new[] { 1, 2, 4 }) { autoCalibration.Require(Plan(false), "simulation", speed); count++; }
        Reject(() => autoCalibration.Require(Plan(), "simulation", 3), "invalid_speed", "speed_invalid");
        Reject(() => autoCalibration.Require(Plan(timed: true), "simulation", 4), "axis_cannot_accelerate", "auto_speed_uncalibrated");
        Reject(() => autoCalibration.Require(Plan(), "formal", 4), "formal_four_rejected", "daily_speed_invalid");
        Reject(() => autoCalibration.Require(Plan(), "formal", 2), "simulation_opening_does_not_enable_formal", "daily_opening_calibration_missing");
        Reject(() => new PcrClanCalibration(2, new HashSet<string> { "simulation" }, null).Require(Plan(), "simulation", 4), "old_profile_no_fast_speed", "auto_speed_uncalibrated");
        var shippedEntry = JObject.Parse(File.ReadAllText(bundledIndex))["entries"]!.OfType<JObject>().FirstOrDefault();
        Check(PcrClanCalibration.Read(sourceRoot, "202610141448").Supports("simulation"), "shipped_october_simulation_still_available");
        if (shippedEntry != null)
        {
            var shipped = PcrClanCalibration.Read(sourceRoot, (string?)shippedEntry["content_version"]);
            Check(shipped.Error == null && shipped.AutoBattleSpeeds?.SetEquals(new[] { 1, 2 }) == true, "shipped_opening_and_speed_dependencies_verified");
            Check(shipped.ManageInheritance && shipped.AutoBattleSummary.Contains("保留当前战斗继承")
                && shipped.AutoBattleSummary.Contains("只调整与作业不同的项")
                && shipped.AutoBattleSummary.Contains("正常流程不例行进入设置页检查继承")
                && shipped.AutoBattleSummary.Contains("受影响角色首次 UB 前")
                && !shipped.AutoBattleSummary.Contains("临时关闭"), "shipped_inheritance_preserved_and_first_ub_boundary");
        }
        Console.WriteLine($"clan_calibration_checks={count}; synthetic fixtures only, game input=0");
    }
}
