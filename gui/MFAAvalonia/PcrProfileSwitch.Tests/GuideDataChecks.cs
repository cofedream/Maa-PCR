using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Pcr;
using MFAAvalonia.Extensions.MaaFW;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json.Linq;

internal static class GuideDataChecks
{
    public static void Run(string sourceRoot, string directory)
    {
        var root = Path.Combine(directory, "guide-data"); Directory.CreateDirectory(root);
        var count = 0; var now = DateTimeOffset.Now; var date = now.ToString("yyyy-MM-dd");
        void Require(bool ok, string name) { if (!ok) throw new Exception("guide_data_" + name); count++; }
        void Reject(Action action, string name) { try { action(); } catch { count++; return; } throw new Exception("guide_data_expected_rejection_" + name); }
        byte[] Bytes(object value) => Encoding.UTF8.GetBytes(value is string s ? s : value.ToString()!);
        JObject Catalog() => JObject.Parse("""
            {"schema_version":1,"server":"cn-bilibili","content_version":"synthetic-v1","source":"synthetic test, not game data",
             "units":[{"unit_id":100101,"name":"合成角色"},{"unit_id":110101,"name":"合成角色（夏日）"}]}
            """);
        JObject Snapshot() => JObject.Parse("""
            {"schema_version":1,"server":"cn-bilibili","account_id":"123456789","content_version":"synthetic-v1",
             "observed_at":"2000-01-01T00:00:00Z","ownership_complete":false,"unknown_fields":[],
             "units":[{"unit_id":100101,"name":"合成角色","level":123,"rank":30,"rarity":5,
               "skills":{"ub":100,"s1":123,"s2":null,"ex":80},
               "unique_equipment":[{"slot":1,"equipment_id":13001,"is_equipped":true,"level":110}]}]}
            """);
        PcrGuideInput Input()
        {
            var catalog = PcrGuideData.Catalog(Bytes(Catalog()), "synthetic-catalog.json");
            var input = new PcrGuideInput { Catalog = catalog, Title = "合成测试", SourceUrl = "https://example.com/guide",
                Material = "持有 等级 Rank 解放星级 星级 专武1等级 专武2等级 UB等级 技能1等级 技能2等级 EX技能等级",
                SourceConfirmed = true, CoverageConfirmed = true, RetrievedOn = date, ValidThrough = date,
                GuideServer = "国服 B 站", TargetServer = "国服 B 站", GuideVersion = "synthetic-v1", TargetVersion = "synthetic-v1",
                GuideStage = "红焰深域 2-1", TargetStage = "红焰深域 2-1", Mode = "SET/AUTO" };
            foreach (var (field, expected) in new[] { ("持有", "是"), ("等级", "123"), ("Rank", "30"), ("解放星级", "5"),
                ("星级", "3"), ("专武1等级", "110"), ("专武2等级", "0"), ("UB等级", "100"), ("技能1等级", "123"), ("技能2等级", "1"), ("EX技能等级", "80") })
            {
                var c = new PcrGuideCondition { Field = field, Expected = expected, Evidence = field };
                PcrGuideData.Bind(c, catalog.Units[0]); input.Conditions.Add(c);
            }
            return input;
        }
        PcrGuideInput Import(Action<JObject>? change = null, PcrGuideInput? input = null)
        {
            var s = Snapshot(); s["observed_at"] = now.AddSeconds(-1).ToString("O"); change?.Invoke(s);
            return PcrGuideData.Import(input ?? Input(), Bytes(s), "synthetic-snapshot.json", now);
        }
        string Value(PcrGuideInput i, string field) => i.Facts.Single(f => f.Field == field).Value;
        bool Finding(PcrGuideInput i, string name) => PcrGuideReview.Check(i, now).Findings.Any(f => f.Status == "未知" && f.Item.Contains(name));
        var imported = Import();
        PcrGuideInput TrialInput()
        {
            var ids = new[] {129701,116701,134701,135501,104701};
            var cat = Catalog(); var units = new JArray(); var roster = new JArray();
            foreach(var id in ids) {
                var name="合成"+id; units.Add(new JObject{["unit_id"]=id,["name"]=name});
                var unit=(JObject)Snapshot()["units"]![0]!.DeepClone(); unit["unit_id"]=id; unit["name"]=name; roster.Add(unit);
            }
            cat["units"]=units;
            var input=Input(); input.Catalog=PcrGuideData.Catalog(Bytes(cat),"synthetic-trial-catalog"); input.Conditions.Clear();
            input.GuideStage=input.TargetStage="红焰深域 7-3";
            input.SourceUrl=PcrGuideIdentity.Url; input.Identity=PcrGuideIdentity.Load(sourceRoot);
            foreach(var unit in input.Catalog.Units) {
                var c=new PcrGuideCondition{Field="持有",Expected="是",Evidence="持有"};
                PcrGuideData.Bind(c,unit); input.Conditions.Add(c);
            }
            var s=Snapshot(); s["units"]=roster; s["observed_at"]=now.AddSeconds(-1).ToString("O");
            input=PcrGuideData.Import(input,Bytes(s),"synthetic-trial-snapshot",now); input.SnapshotConfirmed=true;
            return input;
        }
        var trialInput=TrialInput(); var trialPlan=PcrGuideTrial.CreatePlan(trialInput);
        var expired=TrialInput(); expired.RetrievedOn=expired.ValidThrough=now.AddDays(-1).ToString("yyyy-MM-dd");
        Require(PcrGuideTrial.CreatePlan(expired)["stage"]!.ToString()==expired.TargetStage,"guide_expiry_only_warns");
        expired.Snapshot=expired.Snapshot! with { ObservedAt=now.AddHours(-25).ToString("O") };
        expired.AccountObservedOn=now.AddHours(-25).ToString("yyyy-MM-dd");
        Reject(()=>PcrGuideTrial.CreatePlan(expired),"expired_account_cannot_start_battle");
        var observationPlan=(JObject)trialPlan.DeepClone(); observationPlan["source_hash"]=PcrGuideReview.Fingerprint(expired);
        var observationRoot=Path.Combine(root,"progress-observation");
        var observationLedger=Path.Combine(observationRoot,".runtime","guide-trial-work","ledger"); Directory.CreateDirectory(observationLedger);
        var observationId=Guid.NewGuid().ToString("N");
        var observationIntent=new JObject{["state"]="in_flight",["operation"]="guide_trial",["intent_id"]=observationId,
            ["account_id"]=observationPlan["account_id"],["stage"]=observationPlan["stage"],["source_hash"]=observationPlan["source_hash"]};
        var journalBytes=Bytes(observationIntent.ToString(Newtonsoft.Json.Formatting.None)+"\n");
        File.WriteAllBytes(Path.Combine(observationLedger,"transactions.jsonl"),journalBytes);
        var observationRequest=new JObject{["version"]=1,["intent_id"]=observationId,["plan"]=observationPlan.DeepClone(),
            ["ledger_sha256"]=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(journalBytes)).ToLowerInvariant(),
            ["confirmation"]=new JObject{["source"]="direct_user_message",["navigation_only"]=true}};
        var requestPath=Path.Combine(observationLedger,$"progress-observation-{observationId}.json");
        File.WriteAllText(requestPath,observationRequest.ToString());
        Require(JToken.DeepEquals(PcrGuideTrial.CreatePlan(expired,observationRoot),observationPlan),"expired_guide_bound_observation_only");
        File.AppendAllText(Path.Combine(observationLedger,"transactions.jsonl"),"\n");
        Reject(()=>PcrGuideTrial.CreatePlan(expired,observationRoot),"observation_changed_journal_rejected");
        File.WriteAllBytes(Path.Combine(observationLedger,"transactions.jsonl"),journalBytes);
        expired.Title+="changed";
        Reject(()=>PcrGuideTrial.CreatePlan(expired,observationRoot),"observation_changed_plan_rejected");
        expired.Title=expired.Title[..^7];
        File.WriteAllText(Path.Combine(observationLedger,$"progress-observation-{observationId}-claimed.json"),"{}");
        Reject(()=>PcrGuideTrial.CreatePlan(expired,observationRoot),"claimed_observation_cannot_bypass_expiry");
        Require((int)trialPlan["max_attempts"]! == 3 && (bool)trialPlan["all_set"]! && (bool)trialPlan["auto"]!
            && trialPlan["unit_ids"]!.Values<int>().SequenceEqual(new[]{129701,116701,134701,135501,104701})
            && (int)trialPlan["version"]! == 2 && (string?)trialPlan["source_image_sha256"]==PcrGuideIdentity.SourceHash,"trial_fixed_plan");
        Reject(()=>{var i=TrialInput();i.Identity=null;PcrGuideTrial.CreatePlan(i);},"trial_old_report_no_identity");
        Reject(()=>{var i=TrialInput();i.Identity!.ImageBase64+="!";PcrGuideTrial.CreatePlan(i);},"trial_source_image_tampered");
        Reject(()=>{var i=TrialInput();i.SourceUrl="https://example.com/another";PcrGuideTrial.CreatePlan(i);},"trial_other_source");
        Reject(()=>{var i=TrialInput();i.Identity!.Members[2].Attribute="WATER";PcrGuideTrial.CreatePlan(i);},"trial_wrong_attribute");
        Reject(()=>{var i=TrialInput();i.Identity!.Members[2].Margin=.01;PcrGuideTrial.CreatePlan(i);},"trial_ambiguous_source");
        var consistentWrong=PcrGuideReview.Parse(PcrGuideReview.Serialize(new PcrGuideDocument{Input=TrialInput()}).Replace("134701","122601")).Input;
        consistentWrong.Identity=PcrGuideIdentity.Load(sourceRoot);
        Require(PcrGuideReview.Check(consistentWrong,now).Findings.Any(f=>f.Item=="原图角色核对" && f.Status=="冲突"),"independent_source_rejects_consistent_wrong_mapping");
        Reject(()=>PcrGuideTrial.CreatePlan(consistentWrong),"consistent_wrong_team_no_plan");
        var identityPath=Path.Combine(root,"identity-store.json");var identityStore=new PcrGuideStore(identityPath);identityStore.Load();
        identityStore.Save(new(){Input=trialInput,Report=PcrGuideReview.Check(trialInput,now)});
        var identityReload=new PcrGuideStore(identityPath).Load();
        Require(identityReload.Input.Identity!.Matches(identityReload.Input) && identityReload.Report!.Summary.StartsWith("可以试打"),"source_identity_persists");
        var oldFingerprint=identityReload.Report!.InputHash;identityReload.Input.Identity!.Members[2].UnitId=122601;
        Require(PcrGuideReview.Fingerprint(identityReload.Input)!=oldFingerprint,"source_identity_changes_invalidate_report");
        Reject(()=>{var i=TrialInput();i.SnapshotConfirmed=false;PcrGuideTrial.CreatePlan(i);},"trial_unconfirmed_snapshot");
        Reject(()=>{var i=TrialInput();i.TargetStage="红焰深域 7-4";PcrGuideTrial.CreatePlan(i);},"trial_wrong_stage");
        var second=TrialInput(); second.TargetStage=second.GuideStage="红焰深域 7-4";
        Reject(()=>PcrGuideTrial.CreatePlan(second),"trial_same_team_wrong_source_row");
        second.Identity=PcrGuideIdentity.Load(sourceRoot,second.TargetStage);
        var secondPlan=PcrGuideTrial.CreatePlan(second);
        Require((string?)secondPlan["stage"]==second.TargetStage
            && (string?)secondPlan["source_image_sha256"]==PcrGuideIdentity.Source74Hash,"trial_second_stage_bound");
        var third=TrialInput(); third.TargetStage=third.GuideStage="红焰深域 7-5";
        third.Identity=second.Identity;
        Reject(()=>PcrGuideTrial.CreatePlan(third),"trial_third_stage_rejects_previous_row");
        third.Identity=PcrGuideIdentity.Load(sourceRoot,third.TargetStage);
        var thirdPlan=PcrGuideTrial.CreatePlan(third);
        Require((string?)thirdPlan["stage"]==third.TargetStage
            && (string?)thirdPlan["source_image_sha256"]==PcrGuideIdentity.Source75Hash,"trial_third_stage_bound");
        var next=TrialInput(); next.GuideStage=next.TargetStage="红焰深域 8-2";
        Reject(()=>PcrGuideTrial.CreatePlan(next),"trial_82_rejects_previous_row");
        next.Identity=PcrGuideIdentity.Load(sourceRoot,next.TargetStage);
        var nextPlan=PcrGuideTrial.CreatePlan(next);
        Require((string?)nextPlan["stage"]==next.TargetStage
            && (string?)nextPlan["source_image_sha256"]==PcrGuideIdentity.Source82Hash,"trial_82_source_and_stage_bound");
        Require(next.Identity.Members[2].UnitId==134701,"trial_82_phoenix_not_summer_yui");
        Reject(()=>PcrGuideIdentity.Load(sourceRoot,"红焰深域 7-6"),"unsupported_stage_no_source_fallback");
        Reject(()=>{var i=TrialInput();i.Conditions.RemoveAt(0);PcrGuideTrial.CreatePlan(i);},"trial_incomplete_team");
        var noTextConfirmation=TrialInput();noTextConfirmation.SourceConfirmed=false;
        Require(PcrGuideTrial.CreatePlan(noTextConfirmation)["stage"]!.ToString()==noTextConfirmation.TargetStage,"trial_requires_identity_not_text_checkbox");
        trialInput.Title+="changed";
        Require((string?)PcrGuideTrial.CreatePlan(trialInput)["source_hash"]!=(string?)trialPlan["source_hash"],"trial_input_bound");
        Require(Value(imported, "持有") == "是" && Value(imported, "等级") == "123" && Value(imported, "Rank") == "30", "known_unit_fields");
        Require(Value(imported, "解放星级") == "5" && Value(imported, "星级") == "", "unlocked_rarity_is_not_battle_rarity");
        Require(Value(imported, "专武1等级") == "110" && Value(imported, "专武2等级") == "", "missing_unique_slot_is_not_zero");
        Require(Value(imported, "技能1等级") == "123" && Value(imported, "技能2等级") == "", "missing_skill_unknown");
        PcrGuideInput EquipmentInput()
        {
            var input = Input(); input.Material += " 装备件数 突破";
            foreach (var (field, expected) in new[] { ("装备件数", "4"), ("突破", "是") })
            {
                var condition = new PcrGuideCondition { Field=field, Expected=expected, Evidence=field };
                PcrGuideData.Bind(condition,input.Catalog!.Units[0]); input.Conditions.Add(condition);
            }
            return input;
        }
        var equipment = Import(s => { s["units"]![0]!["equipment_count"]=4; s["units"]![0]!["limit_break"]=true; }, EquipmentInput());
        Require(Value(equipment,"装备件数")=="4" && Value(equipment,"突破")=="是", "equipment_and_breakthrough");
        var missingEquipment = Import(input:EquipmentInput());
        Require(Value(missingEquipment,"装备件数")=="" && Value(missingEquipment,"突破")=="", "old_snapshot_equipment_unknown");
        var noEquipment = Import(s => { s["units"]![0]!["equipment_count"]=0; s["units"]![0]!["limit_break"]=false; }, EquipmentInput());
        Require(Value(noEquipment,"装备件数")=="0" && Value(noEquipment,"突破")=="否", "explicit_zero_and_false");
        var unknownEquipment=Import(s => { s["units"]![0]!["equipment_count"]=4; s["units"]![0]!["limit_break"]=true;
            s["unknown_fields"]=new JArray("units.100101.equipment_count","units.100101.limit_break"); },EquipmentInput());
        Require(Value(unknownEquipment,"装备件数")=="" && Value(unknownEquipment,"突破")=="", "unknown_equipment_overrides_values");
        Reject(()=>Import(s=>s["units"]![0]!["equipment_count"]=7,EquipmentInput()),"equipment_count_bound");
        Reject(()=>Import(s=>s["units"]![0]!["equipment_count"]=true,EquipmentInput()),"equipment_count_boolean");
        Reject(()=>Import(s=>s["units"]![0]!["limit_break"]=1,EquipmentInput()),"breakthrough_requires_boolean");
        PcrGuideInput ProgressInput()
        {
            var input = Input();
            foreach (var field in new[] { "属性技能", "大师技能" })
                input.Conditions.Add(new() { Character="账号",Form="账号",Field=field,Comparison="参考",Expected="1",Evidence=field });
            foreach (var field in new[] { "EX装备1", "EX装备2", "EX装备3" })
            {
                var condition = new PcrGuideCondition { Field=field,Expected="参考",Evidence=field };
                PcrGuideData.Bind(condition,input.Catalog!.Units[0]); input.Conditions.Add(condition);
            }
            return input;
        }
        void Progress(JObject s)
        {
            s["account"]=JObject.Parse("""{"attribute_skill":{"page":5,"completed_merge":3},"master_skill":{"node":75}}""");
            s["account"]!["mapping_sha256"]=new string('a',64);
            s["units"]![0]!["ex_equipment"]=JArray.Parse("""[{"slot":1,"is_equipped":true,"equipment_id":4101301,"name":"贵族之光","rarity":3,"rank":2,"enhancement_level":5},{"slot":2,"is_equipped":false}]""");
        }
        var progress = Import(Progress,ProgressInput());
        Require(Value(progress,"属性技能")=="第5页第3合流点" && Value(progress,"大师技能")=="75","account_progress_imported");
        Require(Value(progress,"EX装备1").Contains("贵族之光") && Value(progress,"EX装备1").Contains("强化 5")
            && Value(progress,"EX装备2")=="未装备" && Value(progress,"EX装备3")=="","ex_slots_separate_missing_and_empty");
        Require(progress.Facts.Single(f=>f.Field=="大师技能").Source.Contains(new string('a',64)),"progress_mapping_provenance");
        var clanGear=Import(s=>{Progress(s);s["units"]![0]!["ex_equipment"]![0]!["clan_battle_equipment"]=true;},ProgressInput());
        Require(Value(clanGear,"EX装备1").Contains("当前为普通栏位，对深域生效"),"clan_mark_keeps_equipment_visible");
        Require(Value(Import(s=>s["units"]![0]!["battle_rarity"]=5),"星级")=="5","selected_battle_rarity_imported");
        Require(Value(Import(s=>{s["units"]![0]!["battle_rarity"]=5;s["unknown_fields"]=new JArray("units.100101.battle_rarity");}),"星级")=="","unknown_battle_rarity_wins");
        Reject(()=>Import(s=>s["units"]![0]!["battle_rarity"]=0),"unresolved_zero_not_a_star");
        Reject(()=>Import(s=>s["units"]![0]!["battle_rarity"]=true),"boolean_not_a_star");
        Require(Value(Import(s=>{Progress(s);s["units"]![0]!["ex_equipment"]![0]!["effects"]="附加：物理贯通 +11 点";},ProgressInput()),"EX装备1").Contains("物理贯通 +11 点"),"ex_effects_imported");
        Require(Value(Import(s=>{Progress(s);s["units"]![0]!["ex_equipment"]![0]!["effects"]="unverified";s["unknown_fields"]=new JArray("units.100101.ex_equipment.1.effects");},ProgressInput()),"EX装备1").Contains("未采集"),"unknown_ex_effects_wins");
        Require(Value(Import(s=>{Progress(s);s["unknown_fields"]=new JArray("units.100101.ex_equipment.1.clan_battle_equipment");},ProgressInput()),"EX装备1")=="","unknown_clan_mark_overrides_value");
        Reject(()=>Import(s=>{Progress(s);s["units"]![0]!["ex_equipment"]![0]!["clan_battle_equipment"]=1;},ProgressInput()),"clan_mark_boolean");
        var oldProgress = Import(input:ProgressInput());
        Require(Value(oldProgress,"属性技能")=="" && Value(oldProgress,"大师技能")=="" && Value(oldProgress,"EX装备1")=="","old_progress_unknown");
        var unknownProgress=Import(s=>{Progress(s);s["unknown_fields"]=new JArray("account.attribute_skill.page","account.master_skill","units.100101.ex_equipment.1");},ProgressInput());
        Require(Value(unknownProgress,"属性技能")=="" && Value(unknownProgress,"大师技能")=="" && Value(unknownProgress,"EX装备1")=="","progress_unknown_overrides_values");
        Reject(()=>Import(s=>{Progress(s);s["account"]!["master_skill"]!["node"]=true;},ProgressInput()),"master_node_boolean");
        Reject(()=>Import(s=>{Progress(s);s["account"]!["attribute_skill"]!["page"]=0;},ProgressInput()),"attribute_page_zero");
        Reject(()=>Import(s=>{Progress(s);s["account"]!["mapping_sha256"]="invalid";},ProgressInput()),"progress_mapping_hash");
        Reject(()=>Import(s=>{Progress(s);((JArray)s["units"]![0]!["ex_equipment"]!).Add(s["units"]![0]!["ex_equipment"]![0]!.DeepClone());},ProgressInput()),"duplicate_ex_slot");
        Reject(()=>Import(s=>{Progress(s);s["units"]![0]!["ex_equipment"]![1]!["equipment_id"]=4101301;},ProgressInput()),"empty_ex_with_id");
        Reject(()=>Import(s=>{Progress(s);s["units"]![0]!["ex_equipment"]![0]!["rarity"]=6;},ProgressInput()),"ex_rarity_bound");
        var progressPath=Path.Combine(root,"progress.json");var progressStore=new PcrGuideStore(progressPath);progressStore.Load();
        progressStore.Save(new(){Input=progress,Report=PcrGuideReview.Check(progress,now)});
        Require(PcrGuideReview.Fingerprint(new PcrGuideStore(progressPath).Load().Input)==PcrGuideReview.Fingerprint(progress),"progress_save_reopen");
        Require(imported.Facts.All(f => f.Source.Contains("SHA256") && f.Source.Contains("123456789") && f.UnitId == 100101), "provenance_and_id");
        Require(!imported.SnapshotConfirmed && Finding(imported, "导入账号确认"), "import_does_not_confirm_account");
        Require(Value(Import(s => s["unknown_fields"] = new JArray("units.100101.level")), "等级") == "", "explicit_unknown_overrides_value");
        Require(Value(Import(s => s["unknown_fields"] = new JArray("units.100101.unique_equipment.1.level")), "专武1等级") == "", "unknown_unique_level");
        Require(Value(Import(s => s["unknown_fields"] = new JArray("units.100101.unique_equipment.1.is_equipped")), "专武1等级") == "", "unknown_unique_state");
        Require(Value(Import(s => s["unknown_fields"] = new JArray("units")), "等级") == "", "global_unknown");
        Require(Value(Import(s => s["unknown_fields"] = new JArray("units.100101.ownership")), "持有") == "", "unit_ownership_unknown");
        Require(Value(Import(s => s["units"] = new JArray()), "持有") == "", "partial_roster_absence_unknown");
        Require(Value(Import(s => { s["units"] = new JArray(); s["ownership_complete"] = true; }), "持有") == "否", "complete_roster_absence_known");
        var otherForm = Input(); PcrGuideData.Bind(otherForm.Conditions[0], otherForm.Catalog!.Units[1]);
        Require(Value(Import(input: otherForm), "持有") == "", "same_character_other_form_not_owned_by_inference");
        var original = Input(); var fingerprint = PcrGuideReview.Fingerprint(original);
        Reject(() => Import(s => s["content_version"] = "other", original), "content_version_mismatch");
        Require(PcrGuideReview.Fingerprint(original) == fingerprint, "failed_import_preserves_input");
        Reject(() => Import(s => s["server"] = "jp"), "wrong_server");
        Reject(() => Import(s => s["schema_version"] = 2), "future_schema");
        Reject(() => Import(s => s["unknown_fields"] = null), "missing_unknown_coverage");
        Reject(() => Import(s => { s["ownership_complete"] = true; s["unknown_fields"] = new JArray("ownership"); }), "contradictory_ownership");
        Reject(() => Import(s => ((JArray)s["units"]!).Add(s["units"]![0]!.DeepClone())), "duplicate_units");
        Reject(() => Import(s => s["units"]![0]!["name"] = "另一个形态"), "snapshot_name_id_conflict");
        Reject(() => Import(s => s["units"]![0]!["level"] = -1), "invalid_level");
        Reject(() => Import(s => s["units"]![0]!["level"] = true), "boolean_level");
        Reject(() => Import(s => s["units"]![0]!["unique_equipment"]![0]!["is_equipped"] = false), "unequipped_with_level");
        Reject(() => Import(s => s["units"]![0]!["unique_equipment"]![0]!["is_equipped"] = "yes"), "invalid_equipped_state");
        Reject(() => Import(s => s["units"]![0]!["unique_equipment"]![0]!["equipment_id"] = 0), "equipped_without_id");
        Reject(() => Import(s => ((JArray)s["units"]![0]!["unique_equipment"]!).Add(s["units"]![0]!["unique_equipment"]![0]!.DeepClone())), "duplicate_unique_slot");
        Reject(() => Import(s => s["observed_at"] = now.AddMinutes(1).ToString("O")), "future_snapshot");
        Reject(() => Import(s => s["observed_at"] = "2026-10-18T12:00:00"), "timezone_required");
        Reject(() => Import(s => s["extra"] = new JObject { ["Password"] = "synthetic-not-a-secret" }), "nested_credentials");
        Reject(() => PcrGuideData.Catalog(Bytes("{\"schema_version\":1,\"schema_version\":1}"), "test.json"), "duplicate_json_key");
        Reject(() => PcrGuideData.Catalog([0xff], "bad.json"), "invalid_utf8");
        Reject(() => { var c = Catalog(); ((JArray)c["units"]!).Add(c["units"]![0]!.DeepClone()); PcrGuideData.Catalog(Bytes(c), "test.json"); }, "duplicate_catalog_id");
        Reject(() => { var c = Catalog(); c["source"] = new JObject(); PcrGuideData.Catalog(Bytes(c), "test.json"); }, "empty_catalog_source");
        Reject(() => Import(s => s["account_id"] = "987654321", imported), "cross_account_refresh_blocked");
        var refreshed = Import(s => s["units"]![0]!["level"] = 124, imported);
        Require(Value(refreshed, "等级") == "124" && !refreshed.SnapshotConfirmed, "refresh_replaces_and_requires_confirmation");
        Require(Finding(Import(s => s["observed_at"] = now.AddDays(-2).ToString("O")), "快照时效"), "old_snapshot_unknown");
        imported.SnapshotConfirmed = true;
        imported.Conditions.RemoveAll(c => new[] { "星级", "专武2等级", "技能2等级" }.Contains(c.Field));
        Require(PcrGuideReview.Check(imported, now).Findings.All(f => f.Status == "相符"), "confirmed_known_subset_matches");
        Require(PcrGuideReview.Check(imported, now).Findings.Any(f => f.Item.Contains("持有") && f.Source.Contains(imported.Catalog!.Sha256)), "mapping_source_in_report");
        var edited = Import(); edited.SnapshotConfirmed = true; edited.AccountVersion = "edited";
        Require(PcrGuideReview.Check(edited, now).Findings.Any(f => f.Status == "冲突" && f.Item == "快照内容版本"), "edited_version_cannot_relabel_snapshot");
        var path = Path.Combine(root, "review.json"); var store = new PcrGuideStore(path); store.Load();
        store.Save(new() { Input = imported, Report = PcrGuideReview.Check(imported, now) });
        var reopened = new PcrGuideStore(path).Load();
        Require(reopened.Input.SnapshotConfirmed && reopened.Input.Catalog!.Units.Count == 2 && reopened.Input.Snapshot!.Sha256 == imported.Snapshot!.Sha256 && reopened.Report != null, "import_roundtrip");
        Require(PcrGuideReview.Parse("{\"SchemaVersion\":1,\"Input\":{\"Title\":\"old saved guide\"}}").Input.Catalog == null, "legacy_document_still_loads");
        Reject(() => PcrGuideReview.Parse("{\"Input\":{\"Snapshot\":{\"AccountId\":null}}}"), "malformed_saved_snapshot");

        var view = new PcrGuideReviewView(path); view.Check();
        var confirm = (CheckBox)typeof(PcrGuideReviewView).GetField("snapshotConfirmed", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(view)!;
        var fields = (System.Collections.Generic.Dictionary<string, TextBox>)typeof(PcrGuideReviewView).GetField("fields", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(view)!;
        confirm.IsChecked = true; fields["AccountLabel"].Text = "另一个目标账号";
        Require(!view.Capture().SnapshotConfirmed && !view.HasCurrentReport, "account_edit_clears_confirmation");
        var snapshotBytes = Snapshot(); snapshotBytes["observed_at"] = now.AddSeconds(-1).ToString("O");
        view.ImportSnapshot(Bytes(snapshotBytes), "synthetic-snapshot.json");
        Require(!view.HasCurrentReport && !view.Capture().SnapshotConfirmed, "ui_import_invalidates_old_conclusion");
        view.Save(); view.Reload();
        Require(view.Capture().Snapshot != null && view.Capture().Facts.Count > 0, "ui_import_save_reopen");
        var savedInput = PcrGuideReview.Fingerprint(view.Capture());
        Reject(() => view.ImportSnapshot(Bytes("{}"), "broken.json"), "ui_bad_import");
        Require(PcrGuideReview.Fingerprint(view.Capture()) == savedInput, "ui_bad_import_preserves_data");
        view.ClearAccount(); Require(view.Capture().Snapshot == null && view.Capture().Facts.Count == 0, "ui_clear_account_for_explicit_switch");
        view.Reload(); Require(view.Capture().Snapshot != null, "discard_unsaved_clear_restores_account");

        var ownerType = typeof(PcrGuideInput).Assembly.GetType("MFAAvalonia.Pcr.PcrGuideSyncOwner", true)!;
        var requestMethod = ownerType.GetMethod("Request", BindingFlags.Static | BindingFlags.NonPublic)!;
        var resultMethod = ownerType.GetMethod("ReadResult", BindingFlags.Static | BindingFlags.NonPublic)!;
        var syncInput = Input(); syncInput.Catalog!.ContentVersion = "202610141431";
        JObject Request(PcrGuideInput i, string player = "123456789") => (JObject)requestMethod.Invoke(null, new object[] { i, player, "synthetic", "synthetic" })!;
        var request = Request(syncInput);
        consistentWrong.Catalog!.ContentVersion = "202610141431";
        Require((int?)Request(consistentWrong)["version"] == 2,"sync_account_read_independent_of_source_team");
        Require(request["unit_ids"] == null && (int?)request["version"] == 2 && (long?)request["account_id"] == 123456789, "sync_full_roster_request");
        Reject(() => Request(syncInput, "invalid"), "sync_invalid_account_before_login");
        var wrongBinding = Input(); wrongBinding.Catalog!.ContentVersion = "202610141431"; wrongBinding.Conditions[0].Character = "wrong";
        Require((int?)Request(wrongBinding)["version"] == 2, "sync_role_mapping_does_not_block_account_read");
        Reject(() => Request(Input()), "sync_invalid_content_version_before_login");
        var existingOther = Import(); existingOther.Catalog!.ContentVersion = "202610141431";
        Reject(() => Request(existingOther, "987654321"), "sync_other_account_before_login");
        var message = new JObject { ["kind"] = "result", ["outcome"] = "complete", ["snapshot"] = snapshotBytes };
        Require(((byte[])resultMethod.Invoke(null, new object[] { message.ToString() })!).Length > 0, "sync_result_parsed");
        Reject(() => resultMethod.Invoke(null, new object[] { "{\"kind\":\"result\",\"outcome\":\"secret-upstream-error\"}" }), "sync_unknown_error_sanitized");
        Reject(() => resultMethod.Invoke(null, new object[] { "{\"kind\":\"result\",\"kind\":\"result\",\"outcome\":\"complete\"}" }), "sync_duplicate_result_rejected");
        var active = ownerType.GetField("current", BindingFlags.Static | BindingFlags.NonPublic)!;
        var gateType = ownerType.Assembly.GetType("MFAAvalonia.Pcr.PcrProfileRunGate", true)!;
        try
        {
            active.SetValue(null, System.Runtime.CompilerServices.RuntimeHelpers.GetUninitializedObject(active.FieldType));
            Require((bool)gateType.GetProperty("Busy", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!, "sync_blocks_execution_gate");
            Require((bool)gateType.GetMethod("OtherProfileBusy", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, new object[] { "pcr-alpha-v1" })!, "sync_blocks_profile_edit");
            var blockedProcessor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
            typeof(MaaProcessor).GetField("<InstanceId>k__BackingField", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(blockedProcessor, "pcr-alpha-v1");
            var blockedVm = (TaskQueueViewModel)RuntimeHelpers.GetUninitializedObject(typeof(TaskQueueViewModel));
            typeof(TaskQueueViewModel).GetField("_processorField", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(blockedVm, blockedProcessor);
            Require(PcrAlphaOwner.RequestStartAsync(blockedVm).IsCompletedSuccessfully, "sync_blocks_direct_daily_start_before_accessing_device");
        }
        finally { active.SetValue(null, null); }
        var syncView = new PcrGuideReviewView(path, (_, _) => Task.FromResult(Bytes(snapshotBytes)));
        syncView.Check(); syncView.ReadSnapshot();
        Require(syncView.Capture().Snapshot != null && !syncView.HasCurrentReport && !syncView.Capture().SnapshotConfirmed, "sync_ui_invalidates_and_requires_account_confirmation");
        var failedView = new PcrGuideReviewView(path, (_, _) => Task.FromException<byte[]>(new Exception("private-synthetic-value")));
        var beforeSync = PcrGuideReview.Fingerprint(failedView.Capture()); failedView.ReadSnapshot();
        Require(PcrGuideReview.Fingerprint(failedView.Capture()) == beforeSync && !failedView.StatusText.Contains("private-synthetic-value"), "sync_failure_preserves_and_sanitizes");
        var pending = new TaskCompletionSource<byte[]>();
        var staleView = new PcrGuideReviewView(path, (_, _) => pending.Task);
        staleView.ReadSnapshot(); staleView.ClearAccount(); pending.SetResult(Bytes(snapshotBytes)); Dispatcher.UIThread.RunJobs();
        Require(staleView.Capture().Snapshot == null && staleView.StatusText.Contains("已改变"), "sync_changed_input_discards_result");
        var picker = (ComboBox)typeof(PcrGuideReviewView).GetField("rolePicker", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(view)!;
        var grid = (DataGrid)typeof(PcrGuideReviewView).GetField("conditions", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(view)!;
        grid.SelectedItem = view.Capture().Conditions[0]; picker.SelectedItem = view.Capture().Catalog!.Units[1]; view.BindSelectedRole();
        Require(view.Capture().Conditions[0].UnitId == 110101 && !view.Capture().CoverageConfirmed, "ui_role_binding_invalidates_coverage");

        var historical = Path.Combine(sourceRoot, ".runtime/guide-data-20261018/historical-catalog.json");
        if (File.Exists(historical))
        {
            var timer = Stopwatch.StartNew(); var real = PcrGuideData.Catalog(PcrGuideData.ReadLocal(historical), "historical-catalog.json"); timer.Stop();
            Require(real.Units.Count == 418 && real.ContentVersion == "202609281214", "actual_historical_catalog_read");
            File.WriteAllText(Path.Combine(root, "historical-catalog-check.json"), PcrGuideReview.Serialize(new { units = real.Units.Count,
                version = real.ContentVersion, sha256 = real.Sha256, elapsed_ms = timer.ElapsedMilliseconds, historical_only = true }));
            view.ImportCatalog(PcrGuideData.ReadLocal(historical), "historical-catalog.json");
            Require(view.Capture().Catalog!.Units.Count == 418, "ui_actual_catalog_import");
        }
        var host = new Window { Content = view, Width = 960, Height = 760, ShowInTaskbar = false, ShowActivated = false, Opacity = 0 };
        view.Background = Avalonia.Media.Brushes.White; host.Show();
        try
        {
            var tabs = (TabControl)typeof(PcrGuideReviewView).GetField("tabs", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(view)!;
            foreach (var index in new[] { 0, 1 })
            {
                tabs.SelectedIndex = index; view.Width = 960; view.Height = 760;
                view.Measure(new Size(960, 760)); view.Arrange(new Rect(0, 0, 960, 760)); view.UpdateLayout(); Dispatcher.UIThread.RunJobs();
                using var bitmap = new RenderTargetBitmap(new PixelSize(960, 760), new Vector(96, 96)); bitmap.Render(view);
                bitmap.Save(Path.Combine(root, $"data-tab-{index}.png"));
                if (index == 0 && ((TabItem)tabs.SelectedItem!).Content is ScrollViewer scroll)
                {
                    scroll.Offset = new Vector(0, scroll.Extent.Height); view.UpdateLayout(); Dispatcher.UIThread.RunJobs();
                    using var lower = new RenderTargetBitmap(new PixelSize(960, 760), new Vector(96, 96)); lower.Render(view);
                    lower.Save(Path.Combine(root, "data-tab-0-lower.png"));
                }
            }
        }
        finally { host.Content=null; host.Close(); }
        view.Display(TrialInput()); view.Check(); view.Save(); view.Reload();
        Require(view.Capture().Identity!.Matches(view.Capture()) && view.HasCurrentReport,"ui_identity_roundtrip");
        fields["Material"].Text += "changed";
        Require(view.Capture().Identity == null && !view.HasCurrentReport,"ui_material_change_invalidates_identity");
        view.Reload();
        var identityHost=new Window { Content=view, Width=960, Height=760, ShowInTaskbar=false, ShowActivated=false, Opacity=0 };
        view.Background=Avalonia.Media.Brushes.White; identityHost.Show();
        try {
            var tabs=(TabControl)typeof(PcrGuideReviewView).GetField("tabs",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(view)!;
            tabs.SelectedIndex=0;
            ((ScrollViewer)((TabItem)tabs.SelectedItem!).Content!).Offset=new Vector(0,0);
            view.Width=960;view.Height=760;view.Measure(new Size(960,760));view.Arrange(new Rect(0,0,960,760));view.UpdateLayout();Dispatcher.UIThread.RunJobs();
            using var bitmap=new RenderTargetBitmap(new PixelSize(960,760),new Vector(96,96));bitmap.Render(view);bitmap.Save(Path.Combine(root,"source-identity.png"));
        } finally {identityHost.Close();}
        Console.WriteLine($"PCR guide data checks passed: {count}; local files and synthetic account only; network logins=0; game inputs=0");
    }
}
