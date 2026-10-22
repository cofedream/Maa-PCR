using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Reflection;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class GuideOpeningChecks
{
    public static void Run(string sourceRoot,string directory)
    {
        var root=Path.Combine(directory,"guide-opening"); Directory.CreateDirectory(root);
        var count=0; var now=DateTimeOffset.Now; var date=now.ToString("yyyy-MM-dd"); var plans=new JArray();
        void Check(bool value,string name) { if(!value)throw new Exception("opening_"+name);count++; }
        void Reject(Action action,string name) { try { action(); } catch { count++;return; } throw new Exception("opening_expected_rejection_"+name); }
        PcrGuideInput Input(string stage)
        {
            var identity=PcrGuideIdentity.Load(sourceRoot,stage);
            var input=new PcrGuideInput { Identity=identity,SourceUrl=PcrGuideIdentity.Url,GuideServer="国服 B 站",TargetServer="国服 B 站",
                GuideStage=stage,TargetStage=stage,GuideVersion="synthetic",TargetVersion="synthetic",Mode="SET/AUTO",
                SourceConfirmed=true,CoverageConfirmed=true,Material="持有 星级 O X",RetrievedOn=date,ValidThrough=date,
                Catalog=new(){Server="cn-bilibili",ContentVersion="synthetic",Source="synthetic",Sha256=new string('a',64),
                    Units=identity.Members.Select(m=>new PcrGuideUnit(m.UnitId,m.Name)).ToList()},
                Snapshot=new("123456789","cn-bilibili","synthetic",now.AddMinutes(-1).ToString("O"),new string('b',64),"synthetic-roster.json"),
                SnapshotConfirmed=true,AccountLabel="synthetic",AccountServer="国服 B 站",AccountVersion="synthetic",AccountObservedOn=date };
            foreach(var m in identity.Members)
            {
                foreach(var (field,value) in new[]{("持有","是"),("星级",m.UnitId is 101001 or 104701 or 107101?"6":"5")})
                {
                    input.Conditions.Add(new(){UnitId=m.UnitId,Character=m.Name,Form="编号 "+m.UnitId,Field=field,Expected=value,Evidence=field,Comparison=field=="持有"?"=":"参考"});
                    input.Facts.Add(new(){UnitId=m.UnitId,Character=m.Name,Form="编号 "+m.UnitId,Field=field,Value=value,Source="synthetic",Confirmed=true});
                }
                input.Conditions.Add(new(){UnitId=m.UnitId,Character=m.Name,Form="编号 "+m.UnitId,Field="SET标记",Expected=m.UnitId==129701?"X":"O",Evidence="O X",Comparison="参考"});
            }
            input.OpeningSets=PcrGuideTrial.SuggestedSets(input); input.OpeningConfirmed=true; return input;
        }
        var candidate=Input("红焰深域 8-3");
        var simpleRoot=Path.Combine(root,"simple"); var cache=new PcrGuideAccountStore(simpleRoot);
        var teams=new[]{"红焰深域 8-3","红焰深域 8-4","红焰深域 8-6","红焰深域 8-8"}.Select(Input).ToArray();
        var allCatalog=teams[0].Catalog!;
        allCatalog.Units=teams.SelectMany(t=>t.Identity!.Members).DistinctBy(m=>m.UnitId).Select(m=>new PcrGuideUnit(m.UnitId,m.Name)).ToList();
        var normalized=new JObject { ["schema_version"]=1,["server"]="cn-bilibili",["content_version"]="synthetic",
            ["account_id"]="123456789",["observed_at"]=now.AddMinutes(-5).ToString("O"),["ownership_complete"]=true,["unknown_fields"]=new JArray(),
            ["units"]=new JArray(allCatalog.Units.Select(u=>new JObject{["unit_id"]=u.UnitId,["battle_rarity"]=u.UnitId is 101001 or 104701 or 107101?6:5,["rarity"]=u.UnitId is 101001 or 104701 or 107101?6:5})) };
        cache.Save(Encoding.UTF8.GetBytes(normalized.ToString()),allCatalog,"123456789",now,null);
        var stamp=cache.Stamp(allCatalog,"123456789"); var reads=0; var accountId="123456789";var simplePlans=new JArray();
        var simplePath=Path.Combine(simpleRoot,"review.json");
        var simple=new PcrGuideReviewView(simplePath,(_,_)=>{reads++;throw new Exception("unexpected_login");},()=>accountId);
        foreach(var team in teams)
        {
            team.RequiresImageReview=team.Identity!.ImportedSource!=null;
            team.Catalog=allCatalog; team.SourceConfirmed=false; team.CoverageConfirmed=false;
            team.SnapshotConfirmed=false; team.OpeningConfirmed=false; team.ValidThrough="2000-01-01";team.GuideVersion="old";
            team.Conditions.First(c=>c.Field=="星级").Expected="1";
            team.Conditions.First(c=>c.Field=="星级").Comparison="=";
            team.Conditions.Add(new(){Character="账号",Form="",Field="职能强化",Comparison="=",Expected="3-2",Evidence="未取得"});
            simple.Display(team); simple.ConfirmTeamAndSave();
            var saved=new PcrGuideStore(simplePath).Load();
            Check(saved.Report!.Summary.StartsWith("可以试打") && saved.Input.Identity!.Matches(saved.Input),"one_save_ready_"+team.GuideStage);
            Check(!saved.Input.SourceConfirmed&&!saved.Input.CoverageConfirmed&&saved.Input.SnapshotConfirmed&&saved.Input.OpeningConfirmed,"only_core_confirmations_"+team.GuideStage);
            Check(saved.Input.Snapshot!.Sha256==stamp&&saved.Input.Snapshot.ObservedAt==(string)normalized["observed_at"]!&&reads==0,"one_save_cache_unchanged_"+team.GuideStage);
            var onePlan=PcrGuideTrial.CreatePlan(saved.Input); simplePlans.Add(onePlan);
            Check(onePlan["unit_ids"]!.Values<int>().SequenceEqual(team.Identity!.Members.Select(m=>m.UnitId)),"one_save_original_plan_"+team.GuideStage);
        }
        Check(simple.Capture().Identity!.Members.All(m=>m.Score==teams[^1].Identity!.Members.Single(t=>t.UnitId==m.UnitId).Score),"original_recognition_scores_preserved");
        var savedBytes=File.ReadAllBytes(simplePath);
        foreach(var fault in new[]{"account","stage","role","set"})
        {
            var wrong=Input("红焰深域 8-3");wrong.Catalog=allCatalog;
            if(fault=="account")accountId="999";
            if(fault=="stage")wrong.TargetStage="红焰深域 8-4";
            if(fault=="role")wrong.Conditions[0].UnitId=100101;
            if(fault=="set")wrong.OpeningSets![129701]=null;
            simple.Display(wrong);var fingerprint=PcrGuideReview.Fingerprint(simple.Capture());
            Reject(simple.ConfirmTeamAndSave,"one_save_"+fault);
            Check(File.ReadAllBytes(simplePath).SequenceEqual(savedBytes)&&PcrGuideReview.Fingerprint(simple.Capture())==fingerprint,"failed_save_keeps_data_"+fault);
            accountId="123456789";
        }
        foreach(var fault in new[]{"stale","missing","partial"})
        {
            var isolated=Path.Combine(simpleRoot,fault);var data=(JObject)normalized.DeepClone();
            if(fault=="stale")data["observed_at"]=now.AddHours(-25).ToString("O");
            else { data["units"]!.First!.Remove();data["ownership_complete"]=fault!="partial"; }
            new PcrGuideAccountStore(isolated).Save(Encoding.UTF8.GetBytes(data.ToString()),allCatalog,"123456789",now,null);
            var refused=new PcrGuideReviewView(Path.Combine(isolated,"review.json"),configuredAccount:()=>"123456789");
            var input=Input("红焰深域 8-3");input.Catalog=allCatalog;refused.Display(input);
            Reject(refused.ConfirmTeamAndSave,"one_save_"+fault);
            Check(!File.Exists(Path.Combine(isolated,"review.json")),"invalid_account_not_saved_"+fault);
        }
        Check(candidate.Identity!.Reviewable()&&!candidate.Identity.Valid(),"uncertain_source_reviewable_but_not_confirmed");
        Reject(()=>PcrGuideTrial.CreatePlan(candidate),"unconfirmed_portrait");
        candidate.Identity.Confirm(1,candidate);
        Check(candidate.Identity.Valid()&&candidate.Identity.Matches(candidate)&&candidate.Identity.Members[1].Score<.82,"explicit_confirmation_keeps_failed_score");
        var draft=PcrGuideDraft.Import(new(),Encoding.UTF8.GetBytes(PcrGuideDraft.Export(candidate)));
        Check(draft.Identity!.Members.All(m=>m.ConfirmedFor==null)&&!draft.OpeningConfirmed,"draft_cannot_import_confirmation");
        var changed=PcrGuideReview.Parse(PcrGuideReview.Serialize(new PcrGuideDocument{Input=candidate})).Input; changed.Material+="改变";
        Reject(()=>PcrGuideTrial.CreatePlan(changed),"changed_material_invalidates_confirmation");
        var path=Path.Combine(root,"guide-review.json"); var view=new PcrGuideReviewView(path); view.Display(candidate);
        var opening=(StackPanel)typeof(PcrGuideReviewView).GetField("openingPanel",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(view)!;
        var confirm=(CheckBox)typeof(PcrGuideReviewView).GetField("openingConfirmed",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(view)!;
        var ids=candidate.Identity.Members.Select(m=>m.UnitId).ToArray();
        Check(opening.Children.Cast<CheckBox>().All(b=>b.Content!.ToString()!.Contains(b.IsChecked==true ? "SET：开启" : "SET：关闭")),"explicit_current_set_labels");
        for(var mask=0;mask<32;mask++)
        {
            for(var i=0;i<5;i++) ((CheckBox)opening.Children[i]).IsChecked=(mask&(1<<i))!=0;
            confirm.IsChecked=true; view.Check(); view.Save();
            if(mask==0) Check(opening.Children.Cast<CheckBox>().All(b=>b.Content!.ToString()!.Contains("SET：关闭")),"set_label_updates_after_toggle");
            var reopened=new PcrGuideStore(path).Load(); var plan=PcrGuideTrial.CreatePlan(reopened.Input);
            Check((int?)plan["version"]==3&&plan["all_set"]==null&&(bool?)plan["auto"]==true,"versioned_plan_"+mask);
            Check(ids.Select((id,i)=>(bool?)plan["set_by_unit"]![id.ToString()]==((mask&(1<<i))!=0)).All(x=>x),"ui_save_plan_"+mask);
            plans.Add(plan);
        }
        var stage88=Input("红焰深域 8-8"); stage88.RequiresImageReview=true;
        Check(stage88.Identity!.Executable()&&!stage88.Identity.Valid(),"source88_exact_import_requires_confirmation");
        Reject(()=>PcrGuideTrial.CreatePlan(stage88),"source88_not_confirmed");
        for(var i=0;i<5;i++) stage88.Identity.Confirm(i,stage88);
        Check(stage88.Identity.Matches(stage88),"source88_confirmation_preserves_provenance");
        view.Display(stage88);
        for(var mask=0;mask<32;mask++)
        {
            for(var i=0;i<5;i++) ((CheckBox)opening.Children[i]).IsChecked=(mask&(1<<i))!=0;
            confirm.IsChecked=true; view.Check(); view.Save();
            var reopened=new PcrGuideStore(path).Load(); var plan=PcrGuideTrial.CreatePlan(reopened.Input);
            Check(plan["source_confirmations"]!.Count()==5 && (int?)plan["version"]==3 && plan["all_set"]==null,"source88_all_five_bindings_"+mask);
            Check(stage88.Identity.Members.Select((m,i)=>(bool?)plan["set_by_unit"]![m.UnitId.ToString()]==((mask&(1<<i))!=0)).All(x=>x),"source88_ui_save_plan_"+mask);
            Check(reopened.Input.Identity!.ImportedSource==PcrGuideIdentity.Source88Evidence,"source88_keeps_original_evidence_"+mask);
            plans.Add(plan);
        }
        var importedDraft=PcrGuideDraft.Import(new(),Encoding.UTF8.GetBytes(PcrGuideDraft.Export(stage88)));
        Check(!importedDraft.Identity!.Valid()&&importedDraft.Identity.Members.All(m=>m.ConfirmedFor==null),"source88_draft_clears_confirmation");
        foreach(var fault in new[]{"material","locator","source","image","role","stage","missing_source"})
        {
            var wrong=PcrGuideReview.Parse(PcrGuideReview.Serialize(new PcrGuideDocument{Input=stage88})).Input;
            if(fault=="material")wrong.Material+="changed";
            if(fault=="locator")wrong.Identity!.ImportedSource=PcrGuideIdentity.Source88Evidence with {Locator="another row"};
            if(fault=="source")wrong.Identity!.SourceUrl="https://example.com/other";
            if(fault=="image")wrong.Identity!.ImageSha256=PcrGuideIdentity.Source86Hash;
            if(fault=="role")wrong.Identity!.Members[2].UnitId=122601;
            if(fault=="stage")wrong.TargetStage="红焰深域 8-7";
            if(fault=="missing_source")wrong.Identity!.ImportedSource=null;
            Reject(()=>PcrGuideTrial.CreatePlan(wrong),"source88_rejects_"+fault);
        }
        stage88.OpeningSets=null;stage88.OpeningConfirmed=false;
        Reject(()=>PcrGuideTrial.CreatePlan(stage88),"source88_never_uses_legacy_settings");
        var other=Input("红焰深域 8-4"); plans.Add(PcrGuideTrial.CreatePlan(other));
        Check(plans.Last!["unit_ids"]!.ToString()!=plans.First!["unit_ids"]!.ToString(),"different_team_same_entry");
        var next=Input("红焰深域 8-5"); plans.Add(PcrGuideTrial.CreatePlan(next));
        Check((int?)plans.Last!["version"]==3 && next.Identity!.Members.All(m=>m.Automatic),"next_stage_independent_source_v3");
        next.OpeningSets=null; next.OpeningConfirmed=false;
        Reject(()=>PcrGuideTrial.CreatePlan(next),"new_stage_never_falls_back_to_legacy_all_set");
        view.Display(next);
        Check(((CheckBox)opening.Children[0]).IsChecked==false,"new_stage_x_default_preserved");
        var stage86=Input("红焰深域 8-6"); plans.Add(PcrGuideTrial.CreatePlan(stage86));
        Check(stage86.Identity!.Members.Select(m=>m.UnitId).SequenceEqual(new[]{132301,129701,134701,130201,104701}),"source86_distinct_team");
        stage86.OpeningSets=null;stage86.OpeningConfirmed=false;
        Reject(()=>PcrGuideTrial.CreatePlan(stage86),"source86_requires_v3_settings");
        foreach(var stage in new[]{"红焰深域 8-3","红焰深域 8-5","红焰深域 8-6"})
        {
            var source=Input(stage); if(stage=="红焰深域 8-3")source.Identity!.Confirm(1,source);
            var before=PcrGuideReview.Fingerprint(source);
            var seeded=PcrGuideDraft.FromSource(source,source.Identity!);
            Check(PcrGuideReview.Fingerprint(source)==before,"source_seed_does_not_mutate_"+stage);
            Check(seeded.Conditions.Where(c=>c.Field=="持有").Select(c=>c.UnitId!.Value).SequenceEqual(source.Identity!.Members.Select(m=>m.UnitId)),"source_seed_five_"+stage);
            Check(seeded.OpeningSets!.All(p=>p.Value==null)&&!seeded.OpeningConfirmed&&!seeded.SourceConfirmed&&!seeded.SnapshotConfirmed&&!seeded.CoverageConfirmed,"source_seed_unknown_not_inferred_"+stage);
            Check(seeded.Identity!.Members.All(m=>m.ConfirmedFor==null)&&seeded.Snapshot!.ObservedAt==source.Snapshot!.ObservedAt,"source_seed_no_confirmation_or_new_timestamp_"+stage);
            Reject(()=>PcrGuideTrial.CreatePlan(seeded),"source_seed_never_authorizes_"+stage);
            view.Display(source);view.ApplySourceTeam();
            Check(view.Capture().Conditions.Count==seeded.Conditions.Count&&view.Capture().OpeningSets!.All(p=>p.Value==null),"source_seed_panel_"+stage);
            source.TargetStage="红焰深域 8-2";Reject(()=>PcrGuideDraft.FromSource(source,source.Identity!),"source_seed_wrong_stage_"+stage);
        }
        var bad=Input("红焰深域 8-4"); bad.OpeningConfirmed=false; Reject(()=>PcrGuideTrial.CreatePlan(bad),"settings_unconfirmed");
        bad.OpeningConfirmed=true;bad.OpeningSets![bad.Identity!.Members[0].UnitId]=null;Reject(()=>PcrGuideTrial.CreatePlan(bad),"ambiguous_mark");
        bad=Input("红焰深域 8-4");bad.Facts.RemoveAll(f=>f.Field=="星级");Reject(()=>PcrGuideTrial.CreatePlan(bad),"no_actual_rarity");
        var legacy=Input("红焰深域 8-2"); legacy.OpeningSets=null;legacy.OpeningConfirmed=false;
        legacy.Conditions.RemoveAll(c=>c.Field=="SET标记");
        var legacyPlan=PcrGuideTrial.CreatePlan(legacy);
        Check((int?)legacyPlan["version"]==2&&(bool?)legacyPlan["all_set"]==true,"legacy_plan_unchanged");
        var serialized=JObject.Parse(PcrGuideReview.Serialize(legacy)); Check(serialized["OpeningSets"]==null&&serialized["OpeningConfirmed"]==null,"legacy_fingerprint_omits_new_defaults");
        foreach(var onePlan in simplePlans)plans.Add(onePlan);
        File.WriteAllText(Path.Combine(root,"managed-plan-cases.json"),plans.ToString(),new UTF8Encoding(false));
        view.Display(candidate); view.Background=Avalonia.Media.Brushes.White;
        var tabs=(TabControl)typeof(PcrGuideReviewView).GetField("tabs",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(view)!;
        tabs.SelectedIndex=0;
        var host=new Window{Content=view,Width=1120,Height=960,ShowInTaskbar=false,ShowActivated=false,Opacity=0};host.Show();
        try
        {
            using(var animation=new System.Threading.CancellationTokenSource(500))Dispatcher.UIThread.MainLoop(animation.Token);
            view.Measure(new Size(1120,960));view.Arrange(new Rect(0,0,1120,960));view.UpdateLayout();Dispatcher.UIThread.RunJobs();
            Check(opening.Children.Count==5&&opening.IsEffectivelyVisible,"five_visible_set_controls");
            using var image=new RenderTargetBitmap(new PixelSize(1120,960),new Vector(96,96));image.Render(view);image.Save(Path.Combine(root,"opening-settings.png"));
        }
        finally{host.Close();}
        Check(PcrAlphaOwner.ControllerCreations==0&&PcrAlphaOwner.AgentCreations==0&&PcrAlphaOwner.Grants==0,"no_game_grant");
        Console.WriteLine($"Guide opening checks passed: {count}; manual confirmations and account are synthetic fixtures, no game authorization");
    }
}
