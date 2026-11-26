using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Numerics;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

public static partial class PcrClanDailyPlan
{
    private sealed record Candidate(PcrClanDailyPlanSlot Reference, int[] Units, string Label);

    private static int[]? ReadOwnedTeam(JArray members, HashSet<int> owned)
    {
        var units = new int[members.Count];
        for (var i = 0; i < units.Length; i++)
        {
            if (members[i].Type != JTokenType.Integer || !int.TryParse(members[i].ToString(), out var id) || !owned.Contains(id))
                return null;
            units[i] = id;
        }
        return units;
    }

    public static PcrClanDailyPlanDocument Recommend(PcrClanDailyPlanDocument document, PcrClanGuideDocument currentGuide,
        int? lockedSlot, string guideStatePath, string configDirectory, string repository, DateTimeOffset now)
        => Recommend(document, currentGuide, lockedSlot, guideStatePath, configDirectory, repository, now, out _);

    internal static PcrClanDailyPlanDocument Recommend(PcrClanDailyPlanDocument document, PcrClanGuideDocument currentGuide,
        int? lockedSlot, string guideStatePath, string configDirectory, string repository, DateTimeOffset now, out string exclusions)
    {
        Validate(document);
        if (lockedSlot is < 1 or > 3) throw new ArgumentOutOfRangeException(nameof(lockedSlot));
        var locked = document.Slots.SingleOrDefault(s => s.Slot == lockedSlot);
        if (lockedSlot.HasValue && locked == null) throw new InvalidDataException($"第 {lockedSlot} 刀尚未设置，无法锁定。");
        var inputs = new PcrClanGuide.PlanInputs();
        var snapshot = PcrClanGuide.Snapshot(currentGuide, configDirectory, repository, now, inputs, out var catalog);
        var period = (string?)currentGuide.Job["period"];
        if (string.IsNullOrWhiteSpace(period)) throw new InvalidDataException("请先选择本期作业和账号资料。");
        var saved = PcrClanGuide.Read(Local(guideStatePath), out var stamp);
        var knownPaths = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var slot in document.Slots) knownPaths[slot.BundleSha256] = slot.BundlePath;
        knownPaths[saved.BundleSha256] = saved.BundlePath;
        knownPaths[currentGuide.BundleSha256] = currentGuide.BundlePath;
        foreach (var (key, entry) in saved.SavedJobs)
            if (key.Length > 65 && key[64] == ':' && !string.IsNullOrEmpty(entry.BundlePath))
                knownPaths.TryAdd(key[..64], entry.BundlePath);

        var candidates = new List<Candidate>();
        var owned = ((JArray)snapshot["units"]!).Select(u => (int)u["unit_id"]!).ToHashSet();
        var unitNames = catalog.Units.ToDictionary(u => u.UnitId, u => u.Name);
        var stars = new PcrClanGuide.AccountStars(snapshot, unitNames);
        var starExclusions = new List<string>();
        string? skipped = null;
        foreach (var (key, entry) in saved.SavedJobs.OrderBy(p => p.Key, StringComparer.Ordinal))
        {
            var job = entry.Job;
            if (!Digest(entry.ConfirmedFor) || key.Length <= 65 || key[64] != ':'
                || !string.Equals(entry.SnapshotPath, currentGuide.SnapshotPath, StringComparison.OrdinalIgnoreCase)
                || entry.SnapshotSha256 != currentGuide.SnapshotSha256 || (string?)job["period"] != period
                || job["switches"] is not JArray { Count: 0 } || (string?)job["support_mode"] is not ("none" or "qsjs")
                || job["unit_ids"] is not JArray { Count: 5 } members) continue;
            var units = ReadOwnedTeam(members, owned);
            if (units == null) continue;
            var bundleHash = key[..64];
            if (stars.Difference(job, useAccountForUnspecified: true) is { } difference)
            {
                var reason = $"“{job["team_label"]}”未入选。{difference}";
                if (locked != null && locked.BundleSha256 == bundleHash && locked.JobId == key[65..])
                    throw new InvalidDataException($"第 {locked.Slot} 刀不能锁定。{reason}");
                starExclusions.Add(reason);
                continue;
            }
            if (!knownPaths.TryGetValue(bundleHash, out var bundlePath) || string.IsNullOrEmpty(bundlePath))
            {
                skipped ??= $"请重新载入“{job["team_label"]}”的原作业包并保存。";
                continue;
            }
            var reference = new PcrClanDailyPlanSlot { BundlePath = bundlePath, BundleSha256 = bundleHash,
                JobId = key[65..], MaterialSha256 = Material(job), ConfirmedFor = entry.ConfirmedFor! };
            candidates.Add(new(reference, units, $"{job["package"]} / {job["team_label"]}（BOSS {job["boss"]}）"));
        }

        if (locked != null)
        {
            var index = candidates.FindIndex(c => c.Reference.BundleSha256 == locked.BundleSha256 && c.Reference.JobId == locked.JobId
                && c.Reference.ConfirmedFor == locked.ConfirmedFor && c.Reference.MaterialSha256 == locked.MaterialSha256);
            if (index < 0) throw new InvalidDataException($"第 {locked.Slot} 刀的作业、持有资料或核对状态已变化，无法锁定。");
            var first = candidates[index] with { Reference = locked };
            candidates.RemoveAt(index); candidates.Insert(0, first);
        }

        exclusions = string.Join(Environment.NewLine, starExclusions);
        var validity = new Dictionary<Candidate, bool>();
        bool CanUse(Candidate candidate)
        {
            if (validity.TryGetValue(candidate, out var valid)) return valid;
            try
            {
                _ = Resolve(candidate.Reference, saved, configDirectory, repository, now, new PcrClanGuide.PlanInputs());
                return validity[candidate] = true;
            }
            catch (Exception ex) when (ex is IOException or InvalidDataException or InvalidOperationException or ArgumentException or JsonException)
            {
                if (locked != null && candidate.Reference == locked) throw new InvalidDataException($"第 {locked.Slot} 刀不能锁定：{ex.Message}", ex);
                skipped = $"{candidate.Label} 待重新核对：{ex.Message}";
                return validity[candidate] = false;
            }
        }
        var chosen = ChooseThree(candidates, locked != null, unitNames, CanUse, out var conflict);
        if (chosen == null)
            throw new InvalidDataException($"所选账号在 {period} 期没有三支互不占用角色的已核对全自有无轴队伍（候选 {candidates.Count} 队）。{conflict}{skipped}{exclusions}");
        // Recheck the three originals together. Retain source bytes only for the final trio.
        foreach (var candidate in chosen) _ = Resolve(candidate.Reference, saved, configDirectory, repository, now, inputs);
        inputs.Verify();
        if (PcrClanGuide.Stamp(guideStatePath) != stamp) throw new IOException("作业在推荐过程中发生变化，请重新读取。");
        var result = new PcrClanDailyPlanDocument();
        var available = new Queue<Candidate>(locked == null ? chosen : chosen.Skip(1));
        for (var slot = 1; slot <= 3; slot++)
        {
            var reference = slot == lockedSlot ? locked! : available.Dequeue().Reference;
            result.Slots.Add(new() { Slot = slot, BundlePath = reference.BundlePath, BundleSha256 = reference.BundleSha256,
                JobId = reference.JobId, ConfirmedFor = reference.ConfirmedFor, MaterialSha256 = reference.MaterialSha256 });
        }
        Validate(result);
        return result;
    }

    internal static void ReviewRecommendation(PcrClanDailyPlanDocument proposed, PcrClanGuideDocument selectedGuide,
        string guideStatePath, string configDirectory, string repository, DateTimeOffset now)
    {
        _ = Review(proposed, guideStatePath, configDirectory, repository, now, true, out var plans);
        var inputs = new PcrClanGuide.PlanInputs();
        var snapshot = PcrClanGuide.Snapshot(selectedGuide, configDirectory, repository, now, inputs, out var catalog);
        var stars = new PcrClanGuide.AccountStars(snapshot, catalog.Units.ToDictionary(unit => unit.UnitId, unit => unit.Name));
        foreach (var (slot, document, plan) in plans)
        {
            if (document.SnapshotSha256 != selectedGuide.SnapshotSha256
                || !string.Equals(document.SnapshotPath, selectedGuide.SnapshotPath, StringComparison.OrdinalIgnoreCase)
                || !JToken.DeepEquals(plan["period"], selectedGuide.Job["period"]))
                throw new InvalidDataException("当前账号资料或期次与预览时不同，请重新配三刀。");
            if (stars.Difference(plan) is { } reason) throw new InvalidDataException($"第 {slot} 刀未入选。{reason}");
        }
        inputs.Verify();
    }

    // Each row marks disjoint teams with a larger index. A pair can be completed
    // by any set bit shared by its two rows; avoid trying every triple separately.
    private static Candidate[]? ChooseThree(List<Candidate> teams, bool lockFirst, Dictionary<int, string> names, Func<Candidate, bool> canUse, out string? conflict)
    {
        conflict = null;
        var blocks = (teams.Count + 63) / 64;
        var later = new ulong[teams.Count][];
        for (var i = 0; i < teams.Count; i++)
        {
            later[i] = new ulong[blocks];
            for (var j = i + 1; j < teams.Count; j++)
            {
                int shared = 0;
                foreach (var unit in teams[i].Units)
                    if (Array.IndexOf(teams[j].Units, unit) >= 0) { shared = unit; break; }
                if (shared == 0) later[i][j / 64] |= 1UL << (j % 64);
                else conflict ??= $"“{teams[i].Label}”与“{teams[j].Label}”都使用 {names.GetValueOrDefault(shared)}（{shared}）。";
            }
        }
        var remaining = Enumerable.Repeat(ulong.MaxValue, blocks).ToArray();
        for (var i = 0; i < teams.Count && (!lockFirst || i == 0); i++)
        {
            if (!canUse(teams[i])) continue;
            for (var j = i + 1; j < teams.Count; j++)
            {
                if ((later[i][j / 64] & (1UL << (j % 64))) == 0 || !canUse(teams[j])) continue;
                for (var block = j / 64; block < blocks; block++)
                {
                    var shared = later[i][block] & later[j][block] & remaining[block];
                    while (shared != 0)
                    {
                        var bit = BitOperations.TrailingZeroCount(shared);
                        var third = teams[block * 64 + bit];
                        if (canUse(third)) return new[] { teams[i], teams[j], third };
                        remaining[block] &= ~(1UL << bit);
                        shared &= shared - 1;
                    }
                }
            }
        }
        return null;
    }
}
