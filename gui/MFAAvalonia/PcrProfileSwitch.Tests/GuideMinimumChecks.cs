using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Collections.Generic;
using Avalonia.Controls;
using MFAAvalonia.Pcr;

internal static class GuideMinimumChecks
{
    public static void Run(string directory)
    {
        var now=DateTimeOffset.Now;var count=0;
        void Require(bool value,string name) { if(!value)throw new Exception("guide_minimum_"+name);count++; }
        PcrGuideInput Fixture()
        {
            var v=new PcrGuideInput { Title="合成单队",GuideServer="国服 B 站",TargetServer="国服 B 站",
                GuideStage="合成关卡",TargetStage="合成关卡",GuideVersion="fixture-v1",TargetVersion="fixture-v1",Mode="SET/AUTO",
                SourceUrl="https://example.com/fixture",Material="合成明确要求及持有依据",SourceConfirmed=true,CoverageConfirmed=true,
                RetrievedOn=now.ToString("yyyy-MM-dd"),ValidThrough=now.ToString("yyyy-MM-dd"),
                AccountLabel="合成账号",AccountServer="国服 B 站",AccountVersion="fixture-v1",AccountObservedOn=now.ToString("yyyy-MM-dd"),SnapshotConfirmed=true,
                Snapshot=new("12345","cn-bilibili","fixture-v1",now.ToString("O"),new string('a',64),"fixture.json"),
                Catalog=new(){Server="cn-bilibili",ContentVersion="fixture-v1",Source="fixture-catalog",Sha256=new string('b',64),
                    Units=Enumerable.Range(1,5).Select(n=>new PcrGuideUnit(100001+n*100,"角色"+n)).ToList()} };
            foreach(var unit in v.Catalog.Units)
            {
                v.Conditions.Add(new(){UnitId=unit.UnitId,Character=unit.Name,Form="编号 "+unit.UnitId,Field="持有",Comparison="=",Expected="是",Evidence="持有依据"});
                v.Facts.Add(new(){UnitId=unit.UnitId,Character=unit.Name,Form="编号 "+unit.UnitId,Field="持有",Value="是",Source="fixture",Confirmed=true});
            }
            Add(v,"等级",">=","360","355");return v;
        }
        void Add(PcrGuideInput v,string field,string comparison,string expected,string? actual)
        {
            v.Conditions.Add(new(){UnitId=100101,Character="角色1",Form="编号 100101",Field=field,Comparison=comparison,Expected=expected,Evidence="合成明确要求"});
            if(actual!=null)v.Facts.Add(new(){UnitId=100101,Character="角色1",Form="编号 100101",Field=field,Value=actual,Source="fixture",Confirmed=true});
        }
        PcrGuideReport Check(PcrGuideInput v,string? account="12345")=>PcrGuideMinimum.Check(v,PcrGuideReview.Check(v,now),now,account);
        bool Gap(PcrGuideReport p)=>p.Findings.Any(f=>f.Status=="明确数值缺口");
        var input=Fixture();var hash=PcrGuideReview.Fingerprint(input);var report=Check(input);
        Require(report.Text.Contains("355 → 360（差 5）") && Gap(report),"explicit_minimum_delta");
        Require(PcrGuideReview.Fingerprint(input)==hash,"read_only");
        Add(input,"等级",">=","362",null);Add(input,"等级","<=","365",null);
        Require(Check(input).Text.Contains("355 → 362（差 7）") && Check(input).Findings.Count(f=>f.Status=="明确数值缺口")==1,"merge_lower_bounds_minimum_target");
        Add(input,"等级","=","370",null);
        Require(!Gap(Check(input)) && Check(input).Summary.Contains("条件冲突"),"contradictory_constraints_stop");
        input.Conditions.Last().Form="其他展示文本";
        Require(!Gap(Check(input)) && Check(input).Summary.Contains("条件冲突"),"same_id_constraints_not_split_by_display_form");
        input=Fixture();input.Facts.Last().Value="367";input.Conditions.Last().Comparison="<=";
        Require(!Gap(Check(input)) && Check(input).Text.Contains("继续升级无法解决"),"upper_bound_not_upgrade");
        input=Fixture();input.Facts.Last().Value="360";
        Require(!Gap(Check(input)),"already_sufficient");
        input=Fixture();input.Conditions.Last().Comparison="参考";
        Require(!Gap(Check(input)) && Check(input).Findings.Any(f=>f.Status=="参考差异"),"reference_not_requirement");
        input.Conditions.Last().Expected="";
        Require(!Gap(Check(input)) && Check(input).Text.Contains("作者要求未知"),"unknown_author_not_upgrade");
        input=Fixture();input.Facts.Last().Confirmed=false;
        Require(!Gap(Check(input)) && Check(input).Text.Contains("账号值待核实"),"unconfirmed_stat_unknown");
        input=Fixture();input.Facts.Add(input.Facts.Last());
        Require(!Gap(Check(input)),"duplicate_fact_not_arbitrarily_selected");
        input=Fixture();input.Facts.Last().Value="NaN";
        Require(!Gap(Check(input)) && Check(input).Text.Contains("数值无效"),"bad_number");
        input=Fixture();input.Conditions.Last().Field=null!;input.Facts.Last().Field=null!;
        Require(!Gap(Check(input)),"missing_field_not_a_training_target");
        input=Fixture();input.Conditions.Last().Evidence="不存在的原文";
        Require(!Gap(Check(input)),"unbacked_requirement");
        foreach(var mutate in new Action<PcrGuideInput>[] {
            v=>v.TargetStage="另一关卡", v=>v.TargetServer="日服", v=>v.AccountVersion="wrong",
            v=>v.SourceConfirmed=false, v=>v.SnapshotConfirmed=false, v=>v.Snapshot=null,
            v=>v.Material=null!,
            v=>v.Snapshot=v.Snapshot! with {ObservedAt=now.AddHours(-25).ToString("O")},
            v=>v.ValidThrough=now.AddDays(-1).ToString("yyyy-MM-dd"),
            v=>v.Facts[0].Value="否",v=>v.Facts.RemoveAt(0),v=>v.Conditions[0].UnitId=999999,
            v=>v.Conditions[0].Character="其他形态",v=>v.Conditions[0].Evidence="未取得" })
        { input=Fixture();mutate(input);Require(!Gap(Check(input)) && Check(input).Summary.Contains("先核对"),"scope_and_ownership_gate_"+count); }
        Require(!Gap(Check(Fixture(),"99999")),"wrong_configured_account");
        input=Fixture();report=PcrGuideReview.Check(input,now);input.Material+="变化";
        Require(!Gap(PcrGuideMinimum.Check(input,report,now)),"stale_fingerprint");
        input=Fixture();report=PcrGuideReview.Check(input,now);
        Require(!Gap(PcrGuideMinimum.Check(input,report,now.AddDays(2))),"cached_report_expiry_rechecked");
        input=Fixture();input.Conditions.RemoveAt(input.Conditions.Count-1);input.Facts.RemoveAt(input.Facts.Count-1);
        Add(input,"星级",">=","6","5");Add(input,"EX装备1","=","作者指定EX","当前已装备EX");
        Require(!Gap(Check(input)) && Check(input).Text.Contains("未读取仓库") && Check(input).Findings.Count(f=>f.Status=="配置核对")==2,"stars_and_ex_do_not_invent_costs");
        input.Conditions.First(c=>c.Field=="星级").Comparison="=";input.Conditions.First(c=>c.Field=="星级").Expected="5";
        input.Facts.First(f=>f.Field=="星级").Value="6";
        Require(!Check(input).Findings.Any(f=>f.Status=="条件冲突") && Check(input).Text.Contains("星级切换不计为培养"),"lower_battle_star_is_setting_not_downgrade_training");
        var path=Path.Combine(directory,"minimum","guide.json");var view=new PcrGuideReviewView(path);view.Display(Fixture());view.Check();
        Require(view.MinimumText.Contains("355 → 360（差 5）"),"panel_check_connected");view.Save();
        view=new PcrGuideReviewView(path);Require(view.MinimumText.Contains("355 → 360（差 5）"),"panel_saved_input_recomputes_advice");
        var fields=(Dictionary<string,TextBox>)typeof(PcrGuideReviewView).GetField("fields",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(view)!;
        fields["Material"].Text+="编辑";Require(view.MinimumText.Contains("失效"),"panel_edit_invalidates_advice");
        var wrong=new PcrGuideReviewView(path,configuredAccount:()=>"99999");wrong.Check();Require(wrong.MinimumText.Contains("账号身份") && !wrong.MinimumText.Contains("355 →"),"panel_current_account_bound");
        Require(PcrAlphaOwner.ControllerCreations==0 && PcrAlphaOwner.AgentCreations==0 && PcrAlphaOwner.Grants==0,"no_execution_objects");
        Console.WriteLine($"Guide minimum checks passed: {count}; synthetic requirements, no resource cost or game action inferred");
    }
}
