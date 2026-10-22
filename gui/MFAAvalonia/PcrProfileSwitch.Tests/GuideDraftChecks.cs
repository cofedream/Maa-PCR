using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Collections.Generic;
using System.Reflection;
using Avalonia;
using Avalonia.Controls;
using Avalonia.LogicalTree;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class GuideDraftChecks
{
    public static void Run(string directory)
    {
        var root=Path.Combine(directory,"guide-drafts"); Directory.CreateDirectory(root);
        var count=0; var now=DateTimeOffset.Now;
        void Require(bool test,string name) { if(!test)throw new Exception("guide_draft_"+name);count++; }
        void Reject(Action action,string name) { try{action();}catch{count++;return;}throw new Exception("guide_draft_expected_rejection_"+name); }
        PcrGuideInput Fixture()
        {
            var input=new PcrGuideInput { Title="合成草稿",SourceUrl="https://example.com/guide",Material="账号大师参考55。",
                GuideServer="国服 B 站",TargetServer="国服 B 站",GuideStage="红焰深域 8-3",TargetStage="红焰深域 8-3",
                RetrievedOn="2026-10-18",ValidThrough="2026-10-19",SourceConfirmed=true,CoverageConfirmed=true,Mode="",
                Catalog=new(){Server="cn-bilibili",ContentVersion="fixture-v1",Source="synthetic-catalog",Sha256=new string('a',64),
                    Units=Enumerable.Range(1,7).Select(n=>new PcrGuideUnit(100001+n*100,$"角色{n}（原版）")).ToList()},
                Snapshot=new("12345","cn-bilibili","fixture-v1",now.ToString("O"),new string('b',64),"synthetic-snapshot.json"),
                SnapshotConfirmed=true,AccountLabel="合成账号",AccountServer="国服 B 站",AccountVersion="fixture-v1",AccountObservedOn=now.ToString("yyyy-MM-dd"),
                Conditions=new(){new(){Character="账号",Form="账号",Field="大师技能",Comparison="参考",Expected="55",Evidence="账号大师参考55"}},
                Facts=new(){new(){Character="账号",Form="账号",Field="大师技能",Value="75",Source="synthetic-account",Confirmed=true}} };
            foreach(var unit in input.Catalog.Units.Take(5))
                foreach(var pair in new[]{("持有","是"),("星级","5"),("等级","365"),("Rank","38")})
                    input.Facts.Add(new(){UnitId=unit.UnitId,Character=unit.Name,Form="编号 "+unit.UnitId,Field=pair.Item1,Value=pair.Item2,Source="synthetic-field",Confirmed=true});
            return input;
        }
        string Table(params int[] ids) => "角色\t星级\t等级\tRank\tSET标记\n"+string.Join('\n',ids.Select((id,index)=>$"{id}\t5\t365\t38\t{(index==1?"X":"O")}"));
        var original=Fixture();var before=PcrGuideReview.Fingerprint(original);
        var table=Table(100101,100201,100301,100601,100701);
        var parsed=PcrGuideDraft.ApplyTable(original,table);
        Require(PcrGuideReview.Fingerprint(original)==before,"original_not_mutated");
        Require(parsed.Conditions.Count==26 && parsed.Conditions.Count(c=>c.Field=="持有")==5,"five_rows_expand_and_keep_account_conditions");
        Require(parsed.Conditions.Where(c=>c.Field!="持有").All(c=>c.Comparison=="参考"),"references_not_requirements");
        Require(parsed.Conditions.Single(c=>c.UnitId==100201 && c.Field=="SET标记").Expected=="X" && parsed.Mode=="","raw_set_mark_not_inferred");
        Require(parsed.Conditions.All(c=>parsed.Material.Contains(c.Evidence)),"evidence_is_actual_row");
        Require(parsed.Facts.Count==13 && parsed.Facts.All(f=>f.UnitId is not (100401 or 100501 or 100601 or 100701)),"retain_only_shared_facts_and_account");
        Require(parsed.Snapshot==original.Snapshot && !parsed.SnapshotConfirmed && !parsed.SourceConfirmed && !parsed.CoverageConfirmed,"times_preserved_confirmations_reset");
        Require(PcrGuideReview.Check(parsed,now).Summary.Contains("资料不足"),"new_role_absence_unknown");
        Require(PcrGuideDraft.ApplyTable(parsed,table).Material==parsed.Material,"same_table_not_duplicated");
        var names=Table(100101,100201,100301,100401,100501).Replace("100101","角色1（原版）");
        Require(PcrGuideDraft.ApplyTable(original,names).Conditions[0].UnitId==100101,"exact_name_maps");
        Reject(()=>PcrGuideDraft.ApplyTable(original,names.Replace("角色1（原版）","角色1")),"nickname_not_guessed");
        Reject(()=>PcrGuideDraft.ApplyTable(original,table.Replace("100601","100101")),"duplicate_role");
        Reject(()=>PcrGuideDraft.ApplyTable(original,table.Replace("星级","星级\t星级")),"duplicate_column");
        Reject(()=>PcrGuideDraft.ApplyTable(original,table.Replace("星级","未知字段")),"unsupported_column");
        Reject(()=>PcrGuideDraft.ApplyTable(original,table+"\n100401\t5\t365\t38\tO"),"six_people");
        Reject(()=>PcrGuideDraft.ApplyTable(original,table.Replace("\t5\t","\t7\t")),"bad_star");
        Reject(()=>PcrGuideDraft.ApplyTable(original,table.Replace("\t365\t","\tNaN\t")),"bad_number");
        var blank=PcrGuideDraft.ApplyTable(original,table.Replace("\t365\t","\t未知\t"));
        Require(blank.Conditions.Where(c=>c.Field=="等级").All(c=>c.Expected==""),"unknown_kept_unknown");
        var ambiguous=Fixture();ambiguous.Catalog!.Units[5]=new(100601,"角色1（原版）");
        Reject(()=>PcrGuideDraft.ApplyTable(ambiguous,names),"ambiguous_full_name");
        var exported=PcrGuideDraft.Export(parsed);var publicJson=JObject.Parse(exported);
        Require(publicJson["Input"]!["Facts"]!.Count()==0 && publicJson["Input"]!["Snapshot"]!.Type==JTokenType.Null
            && (string?)publicJson["Input"]!["AccountLabel"]=="" && publicJson["Report"]==null,"export_excludes_account_and_report");
        byte[] Bytes(string value)=>Encoding.UTF8.GetBytes(value);
        var imported=PcrGuideDraft.Import(original,Bytes(exported));
        Require(imported.Facts.Count==13 && imported.Snapshot==original.Snapshot && !imported.SnapshotConfirmed,"local_account_reused_without_new_confirmation");
        Require(imported.RetrievedOn==parsed.RetrievedOn && imported.ValidThrough==parsed.ValidThrough,"source_dates_not_refreshed");
        var expired=Fixture();expired.Snapshot=expired.Snapshot! with { ObservedAt=now.AddDays(-2).ToString("O") };
        expired.AccountObservedOn=now.AddDays(-2).ToString("yyyy-MM-dd");
        var old=PcrGuideDraft.Import(expired,Bytes(exported));
        Require(PcrGuideReview.Check(old,now).Findings.Any(f=>f.Item=="快照时效" && f.Status=="未知"),"expired_reuse_still_expired");
        var foreign=JObject.Parse(exported);foreign["Input"]!["TargetServer"]="日服";
        Require(PcrGuideDraft.Import(original,Bytes(foreign.ToString())).Snapshot==null,"other_server_does_not_reuse_account");
        foreign=JObject.Parse(exported);foreign["Input"]!["Catalog"]!["Units"]![0]!["Name"]="伪造名称";
        Require(PcrGuideDraft.Import(original,Bytes(foreign.ToString())).Snapshot==null,"declared_catalog_hash_not_enough");
        foreign=JObject.Parse(exported);foreign["Input"]!["Snapshot"]=JToken.FromObject(original.Snapshot!);
        Reject(()=>PcrGuideDraft.Import(original,Bytes(foreign.ToString())),"imported_account_rejected");
        Reject(()=>PcrGuideDraft.Import(original,Bytes(exported.Replace("\"Format\":","\"Format\": \"pcr-guide-draft-v1\", \"Format\":"))),"duplicate_json");
        Reject(()=>PcrGuideDraft.Import(original,Bytes(exported.Replace("\"Format\":","\"format\": \"pcr-guide-draft-v1\", \"Format\":"))),"case_duplicate_json");
        foreign=JObject.Parse(exported);foreign.Remove("Format");
        Reject(()=>PcrGuideDraft.Import(original,Bytes(foreign.ToString())),"missing_format");
        Reject(()=>PcrGuideDraft.Import(original,Bytes(exported+"{}")),"trailing_document");
        Reject(()=>PcrGuideDraft.Import(original,new byte[]{255,254,255}),"invalid_utf8");
        Reject(()=>PcrGuideTrial.CreatePlan(imported),"draft_cannot_enable_new_stage_execution");
        var path=Path.Combine(root,"isolated-guide.json");var view=new PcrGuideReviewView(path);view.Display(original);view.Save();
        var saved=File.ReadAllBytes(path);view.ImportDraft(Bytes(exported));
        Require(File.ReadAllBytes(path).SequenceEqual(saved) && !view.HasCurrentReport,"draft_load_does_not_save_or_reuse_report");
        var fingerprint=PcrGuideReview.Fingerprint(view.Capture());view.ImportDraft(Bytes(PcrGuideDraft.Export(original)));
        Require(PcrGuideReview.Fingerprint(view.Capture())==fingerprint && view.StatusText.Contains("先保存"),"dirty_edit_protected");
        view.Reload();view.ApplyTeamTable(table);view.Check();view.Save();
        Require(new PcrGuideStore(path).Load().Input.Conditions.Count==26,"generated_conditions_save_and_reopen");
        Require(view.SavedTeams.Count==1 && view.TeamSummaryText.Contains("100601") && view.TeamSummaryText.Contains("等级 未知"),"library_and_summary_saved");
        var firstKey=view.SavedTeams.Single().Key;
        var second=PcrGuideDraft.ApplyTable(original,Table(100101,100201,100301,100401,100501));
        second.Title="第二队";view.ImportDraft(Bytes(PcrGuideDraft.Export(second)));view.Save();
        Require(view.SavedTeams.Count==2,"different_teams_preserved");
        var persisted=File.ReadAllBytes(path);view.LoadSavedTeam(firstKey);
        Require(view.Capture().Conditions.Any(c=>c.UnitId==100701) && !view.Capture().SourceConfirmed
            && !view.Capture().OpeningConfirmed && File.ReadAllBytes(path).SequenceEqual(persisted),"switch_load_only_no_confirmations_or_save");
        var switchHash=PcrGuideReview.Fingerprint(view.Capture());view.LoadSavedTeam(view.SavedTeams.Single(e=>e.Key!=firstKey).Key);
        Require(PcrGuideReview.Fingerprint(view.Capture())==switchHash && view.StatusText.Contains("先保存"),"switch_does_not_discard_dirty_edit");
        view.Save();view.Reload();
        Require(view.SavedTeams.Count==2,"same_team_updates_without_duplicate");
        var libRoot=Path.Combine(root,"concurrency-library");var left=new PcrGuideLibrary(libRoot);left.Reload();left.Save(parsed);
        var right=new PcrGuideLibrary(libRoot);right.Reload();
        var modified=PcrGuideDraft.Import(new(),Bytes(exported));modified.Material+="\n新说明";left.Save(modified);
        Reject(()=>right.Save(parsed),"library_concurrent_write_rejected");
        var libInput=new PcrGuideStore(Directory.GetFiles(libRoot,"*.json").Single()).Load().Input;
        Require(libInput.Facts.Count==0 && libInput.Snapshot==null && libInput.AccountLabel=="" && !libInput.SourceConfirmed,"library_excludes_account_and_confirmation");
        var corrupt=JObject.Parse(File.ReadAllText(Directory.GetFiles(libRoot,"*.json").Single()));corrupt["Input"]!["AccountLabel"]="unexpected account";
        File.WriteAllText(Directory.GetFiles(libRoot,"*.json").Single(),corrupt.ToString());
        Reject(()=>left.Reload(),"library_account_injection_rejected");
        view.Reload();view.ApplyTeamTable(table);
        var editor=(TextBox)typeof(PcrGuideReviewView).GetField("teamTable",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(view)!;
        editor.Text=table;
        view.Background=Avalonia.Media.Brushes.White;
        var host=new Window { Content=view,Width=1040,Height=900,ShowInTaskbar=false,ShowActivated=false,Opacity=0 };
        host.Show();
        try
        {
            Dispatcher.UIThread.RunJobs();
            foreach(var expander in editor.GetLogicalAncestors().OfType<Expander>()) expander.IsExpanded=true;
            using(var animation=new System.Threading.CancellationTokenSource(500)) Dispatcher.UIThread.MainLoop(animation.Token);
            view.Width=1040;view.Height=900;view.Measure(new Size(1040,900));view.Arrange(new Rect(0,0,1040,900));
            view.UpdateLayout();Dispatcher.UIThread.RunJobs();
            Require(editor.Bounds.Height>=100 && editor.IsEffectivelyVisible,"table_editor_visible_after_expansion");
            using var bitmap=new RenderTargetBitmap(new PixelSize(1040,900),new Vector(96,96));
            bitmap.Render(view);bitmap.Save(Path.Combine(root,"draft-editor.png"));
        }
        finally {host.Close();}
        Require(PcrAlphaOwner.ControllerCreations==0 && PcrAlphaOwner.AgentCreations==0 && PcrAlphaOwner.Grants==0,"no_live_owner_objects");
        Console.WriteLine($"PCR guide draft checks passed: {count}; isolated synthetic data, no game input");
    }
}
