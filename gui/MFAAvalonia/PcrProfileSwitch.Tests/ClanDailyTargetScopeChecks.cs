using System;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class ClanDailyTargetScopeChecks
{
    private const string Period = "2026-11";

    private static JObject Target(string phase, int boss, params int[] speeds)
    {
        var row = new JObject { ["period"] = Period, ["phase"] = phase, ["boss"] = boss };
        if (speeds.Length > 0) row["speeds"] = new JArray(speeds);
        return row;
    }

    public static void Run(JObject fixture, Func<JObject, string[], PcrClanCalibration> load,
        Func<string, int, string, JObject> plan, Action<bool, string> check, Action<Action, string, string> reject)
    {
        fixture["phase_labels"] = new JObject { ["第2阶段"] = "B", ["第3阶段"] = "C", ["第4阶段"] = "D" };
        string[] modes = ["simulation", "formal", "carryover"];
        var scoped = (JObject)fixture.DeepClone();
        scoped["simulation_targets"] = new JArray(Target("C", 1, 1), Target("D", 3, 2));
        scoped["formal_targets"] = new JArray(Target("D", 1), Target("D", 3), Target("D", 5));
        scoped["carryover_targets"] = new JArray(Target("D", 2));

        var current = load(scoped, modes);
        void Accept(PcrClanCalibration calibration, string mode, string phase, int boss, int speed)
        {
            calibration.Require(plan(phase, boss, Period), mode, speed);
            check(true, $"accepted_{mode}_{phase}{boss}_{speed}x");
        }
        void Deny(PcrClanCalibration calibration, string mode, string phase, int boss, int speed, string month = Period)
        {
            reject(() => calibration.Require(plan(phase, boss, month), mode, speed),
                $"unregistered_{mode}_{month}_{phase}{boss}_{speed}x", $"clan_battle_{mode}_target_uncalibrated");
        }
        void DenySpeed(PcrClanCalibration calibration, string phase, int boss, int speed)
        {
            reject(() => calibration.Require(plan(phase, boss, Period), "simulation", speed),
                $"unverified_speed_{phase}{boss}_{speed}x", "clan_battle_auto_speed_uncalibrated");
        }
        foreach (var boss in new[] { 1, 3, 5 }) Accept(current, "formal", "D", boss, 2);
        Accept(current, "carryover", "D", 2, 2);
        Deny(current, "formal", "D", 2, 2);
        Deny(current, "formal", "C", 1, 2);
        Deny(current, "formal", "D", 1, 2, "2026-12");
        Deny(current, "carryover", "D", 1, 2);
        Deny(current, "carryover", "D", 2, 2, "2026-12");
        check(current.Summary.Contains("D5") && current.Summary.Contains("D2"), "summary_includes_both_formal_scopes");
        Accept(current, "simulation", "C", 1, 1);
        Accept(current, "simulation", "D", 3, 2);
        DenySpeed(current, "C", 1, 2);
        DenySpeed(current, "D", 3, 1);
        Deny(current, "simulation", "D", 5, 2);

        var shared = (JObject)scoped.DeepClone();
        shared["simulation_targets"] = new JArray(Target("B", 1, 1), Target("C", 1, 2), Target("D", 3));
        shared["formal_targets"] = new JArray(Target("C", 1));
        var sharedCalibration = load(shared, modes);
        Accept(sharedCalibration, "simulation", "BC", 1, 1);
        DenySpeed(sharedCalibration, "BC", 1, 2);
        DenySpeed(sharedCalibration, "B", 1, 2);
        DenySpeed(sharedCalibration, "C", 1, 1);
        Accept(sharedCalibration, "simulation", "C", 1, 2);
        Accept(sharedCalibration, "simulation", "D", 3, 1);
        Accept(sharedCalibration, "simulation", "D", 3, 2);
        Accept(sharedCalibration, "formal", "BC", 1, 2);
        Deny(sharedCalibration, "formal", "B", 1, 2);

        var cNavigationOnly = (JObject)scoped.DeepClone();
        var choices = (JObject)cNavigationOnly["actions"]!["select_difficulty"]!["choices"]!;
        choices.Remove("D");
        cNavigationOnly["simulation_targets"] = new JArray(Target("C", 1));
        Accept(load(cNavigationOnly, modes), "formal", "D", 1, 2);

        var old = load(fixture, modes);
        Accept(old, "formal", "D", 4, 2);
        Accept(old, "carryover", "D", 4, 2);
        var withoutFormal = load(scoped, ["simulation"]);
        reject(() => withoutFormal.Require(plan("D", 1, Period), "formal", 2),
            "target_cannot_grant_capability", "capability_missing:formal");
        var withoutCarryover = load(scoped, ["simulation", "formal"]);
        reject(() => withoutCarryover.Require(plan("D", 2, Period), "carryover", 2),
            "target_cannot_grant_carryover", "capability_missing:carryover");

        var releaseProfile = (JObject)scoped.DeepClone();
        releaseProfile.Remove("carryover_targets");
        releaseProfile["simulation_targets"] = new JArray(Target("C", 1, 1),
            Target("D", 1, 2), Target("D", 3, 2), Target("D", 5, 2));
        var release = load(releaseProfile, ["simulation", "formal"]);
        foreach (var boss in new[] { 1, 3, 5 })
        {
            Accept(release, "simulation", "D", boss, 2);
            Accept(release, "formal", "D", boss, 2);
            DenySpeed(release, "D", boss, 1);
            reject(() => release.Require(plan("D", boss, Period), "carryover", 2),
                "november_has_no_compensation_" + boss, "capability_missing:carryover");
        }
        foreach (var boss in new[] { 2, 4 })
        {
            Deny(release, "simulation", "D", boss, 2);
            Deny(release, "formal", "D", boss, 2);
        }
        Accept(release, "simulation", "C", 1, 1);
        DenySpeed(release, "C", 1, 2);
        Deny(release, "formal", "C", 1, 2);

        var embeddedTemplate = Target("D", 1);
        embeddedTemplate["extra"] = new JObject { ["recognition"] = "TemplateMatch",
            ["template"] = "clan-synthetic/nonexistent.png", ["sha256"] = new string('0', 64) };
        foreach (var mode in new[] { "formal", "carryover" })
        {
            foreach (var invalid in new JToken[] { new JArray(), new JArray(embeddedTemplate.DeepClone()),
                new JArray(Target("D", 1, 2)), new JArray(Target("BC", 1)), new JArray(Target("E", 1)),
                new JArray(Target("D", 1), Target("D", 1)) })
            {
                var invalidProfile = (JObject)scoped.DeepClone();
                invalidProfile[mode + "_targets"] = invalid;
                var calibration = load(invalidProfile, modes);
                var other = mode == "formal" ? "carryover" : "formal";
                check(!calibration.Supports(mode) && calibration.Supports("simulation") && calibration.Supports(other),
                    "invalid_scope_isolated_" + mode);
                reject(() => calibration.Require(plan("D", 1, Period), mode, 2), "malformed_" + mode,
                    $"clan_battle_{mode}_targets_invalid");
            }
        }
        foreach (var invalidSpeeds in new JToken[] { JValue.CreateNull(), new JArray(), new JArray(1, 1),
            new JArray(4), new JArray("2"), new JArray(2.0), new JArray(true) })
        {
            var invalidProfile = (JObject)scoped.DeepClone();
            invalidProfile["simulation_targets"]![0]!["speeds"] = invalidSpeeds;
            var calibration = load(invalidProfile, modes);
            check(!calibration.Supports("simulation") && calibration.Supports("formal") && calibration.Supports("carryover"),
                "malformed_per_target_speeds");
            reject(() => calibration.Require(plan("C", 1, Period), "simulation", 1), "malformed_simulation_speeds",
                "clan_battle_simulation_targets_invalid");
        }
    }
}
