using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Reflection;
using System.Diagnostics;
using System.Threading.Tasks;
using Avalonia.Controls;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class GuideProgressChecks
{
    public static void Run(string sourceRoot, string directory, string python)
    {
        var root=Path.Combine(directory,"account-progress");Directory.CreateDirectory(root);int count=0;
        PcrAlphaOwner.Install(sourceRoot,Path.Combine(root,"isolated-owner.json"));
        void Check(bool value,string name){if(!value)throw new Exception("progress_"+name);count++;}
        void Reject(Action action,string name){try{action();}catch{count++;return;}throw new Exception("progress_expected_reject_"+name);}
        byte[] Bytes(JObject value)=>Encoding.UTF8.GetBytes(value.ToString());
        var script=Path.Combine(root,"normalize.py");
        File.WriteAllText(script,"""
            import sys,json
            from pathlib import Path
            from datetime import datetime,timezone,timedelta
            sys.path.insert(0,str(Path(sys.argv[1])/'src'))
            from pcr_maa.guide_snapshot import normalize,reference
            rules=reference('202610141431','account-progress')
            source=dict(user_info=dict(viewer_id=123),unit_list=[dict(id=n,unit_level=365,promotion_level=38,unit_rarity=5,battle_rarity=5) for n in (129701,116701,134701,180901,107101)],
                princess_knight_info=dict(talent_level_info_list=[dict(talent_id=n,total_point=rules['attribute_levels'][399][0]) for n in range(1,6)]),
                unit_role_list=[dict(unit_role_id=n,**{f'{key}_{slot}':value for slot in range(1,5) for key,value in (('slot_level',3),('enhance_level',2))}) for n in range(1,9)])
            result=normalize(source,123,'202610141431',observed_at=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat())
            print(json.dumps(dict(kind='result',outcome='complete',snapshot=result),ensure_ascii=False))
            """,new UTF8Encoding(false));
        using var child=new Process { StartInfo=new ProcessStartInfo(python) { WorkingDirectory=sourceRoot,UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8 } };
        foreach(var arg in new[]{"-X","utf8","-B",script,sourceRoot})child.StartInfo.ArgumentList.Add(arg);
        child.Start();var stdout=child.StandardOutput.ReadToEndAsync();var stderr=child.StandardError.ReadToEndAsync();
        if(!child.WaitForExit(15000)){child.Kill(true);throw new Exception("progress_projection_timeout");}
        Check(child.ExitCode==0,"python_projection_exit_0_"+stderr.GetAwaiter().GetResult());
        var read=typeof(PcrGuideInput).Assembly.GetType("MFAAvalonia.Pcr.PcrGuideSyncOwner")!.GetMethod("ReadResult",BindingFlags.NonPublic|BindingFlags.Static)!;
        var bytes=(byte[])read.Invoke(null,new object[]{stdout.GetAwaiter().GetResult().Trim()})!;
        var data=JObject.Parse(Encoding.UTF8.GetString(bytes));File.WriteAllBytes(Path.Combine(root,"synthetic-snapshot.json"),bytes);
        var now=DateTimeOffset.Now;
        var catalog=new PcrGuideCatalog {Server="cn-bilibili",ContentVersion="202610141431",Source="synthetic",Sha256=new string('a',64),
            Units=new[]{129701,116701,134701,180901,107101}.Select(id=>new PcrGuideUnit(id,"合成角色"+id)).ToList()};
        PcrGuideInput Input()
        {
            var input=new PcrGuideInput {Catalog=catalog};
            foreach(var unit in catalog.Units){var c=new PcrGuideCondition {Field="持有"};PcrGuideData.Bind(c,unit);input.Conditions.Add(c);}
            input.Conditions.Add(new(){Character="账号",Field="属性等级",Expected="400",Comparison="参考"});
            input.Conditions.Add(new(){Character="账号",Field="职能强化",Expected="3-2",Comparison="参考"});
            input.Conditions.Add(new(){Character="账号",Form="攻击型/槽1",Field="职能强化",Expected="3-2",Comparison="参考"});
            return input;
        }
        var store=new PcrGuideAccountStore(root);store.Save(bytes,catalog,"123",now,null);var hash=store.Stamp(catalog,"123");
        var input=store.Reuse(Input(),"123",now);
        Check(input.Facts.Single(f=>f.Field=="属性等级").Value=="400","single_team_attribute_projection");
        Check(input.Facts.Single(f=>f.Field=="职能强化"&&f.Form=="攻击型/槽1").Value=="3-2","explicit_role_slot");
        Check(!input.Facts.Single(f=>f.Field=="职能强化"&&f.Form=="").Confirmed,"unscoped_author_shorthand_unknown");
        Check(store.Describe(input,now).Contains("火 400")&&store.Describe(input,now).Contains("攻击型：槽1 3-2")&&store.Describe(input,now).Contains("干扰型："),"all_attributes_and_roles_displayed");
        Check(input.Facts.Where(f=>f.Field=="属性等级"||f.Form=="攻击型/槽1").All(f=>f.Source.Contains("807905b0")),"mapping_provenance");
        var viewPath=Path.Combine(root,"panel","review.json");int calls=0;
        var view=new PcrGuideReviewView(viewPath,(_,_)=>{calls++;return Task.FromResult(bytes);},()=>"123");
        view.Display(Input());view.ReadSnapshot();Check(calls==1,"existing_read_callback_once");
        var status=(TextBlock)typeof(PcrGuideReviewView).GetField("snapshotStatus",BindingFlags.NonPublic|BindingFlags.Instance)!.GetValue(view)!;
        Check(status.Text!.Contains("火 400")&&status.Text.Contains("槽4 3-2"),"panel_status_uses_saved_details");
        var observed=view.Capture().Snapshot!.ObservedAt;view.Save();
        var reopened=new PcrGuideReviewView(viewPath,(_,_)=>{calls++;return Task.FromResult(bytes);},()=>"123");reopened.Reload();
        Check(reopened.Capture().Facts.Single(f=>f.Field=="属性等级").Value=="400"&&calls==1,"save_reopen_no_login");
        foreach(var reverse in new[]{false,true,false})
        {
            var ids=reverse?catalog.Units.AsEnumerable().Reverse():catalog.Units;
            reopened.ApplyTeamTable("角色\t等级\n"+string.Join("\n",ids.Select(u=>$"{u.UnitId}\t365")));
            Check(reopened.Capture().Snapshot!.ObservedAt==observed&&reopened.Capture().Facts.Single(f=>f.Field=="属性等级").Value=="400"&&calls==1,"switch_reuses_progress");
        }
        Check(store.Describe(input,now.AddHours(25)).Contains("过期"),"expiry_preserved");
        var old=(JObject)data.DeepClone();foreach(var key in new[]{"attribute_levels","unit_roles","progress_mapping_sha256"})((JObject)old["account"]!).Remove(key);
        Check(PcrGuideData.DescribeProgress(PcrGuideData.AccountSnapshot(Bytes(old),catalog,"123",now)).Contains("未知（快照未采集）"),"legacy_missing_not_zero");
        Check(!PcrGuideData.Import(Input(),Bytes(old),"legacy",now).Facts.Single(f=>f.Field=="属性等级").Confirmed,"legacy_facts_unknown");
        var partial=(JObject)data.DeepClone();((JArray)partial["account"]!["attribute_levels"]!).RemoveAt(0);
        Check(PcrGuideData.DescribeProgress(PcrGuideData.AccountSnapshot(Bytes(partial),catalog,"123",now)).Contains("火 未知"),"missing_attribute_unknown");
        ((JArray)partial["account"]!["unit_roles"]![0]!["slots"]!).RemoveAt(0);
        Check(PcrGuideData.DescribeProgress(PcrGuideData.AccountSnapshot(Bytes(partial),catalog,"123",now)).Contains("攻击型：槽1 未知"),"missing_slot_unknown");
        var unknown=(JObject)data.DeepClone();((JArray)unknown["unknown_fields"]!).Add("account.attribute_levels.1");
        Check(!PcrGuideData.Import(Input(),Bytes(unknown),"unknown",now).Facts.Single(f=>f.Field=="属性等级").Confirmed,"unknown_mask_respected");
        foreach(var change in new Action<JObject>[] {
            x=>x["account"]!["attribute_levels"]![0]!["level"]=401,
            x=>x["account"]!["attribute_levels"]![0]!["total_point"]=true,
            x=>x["account"]!["attribute_levels"]![0]!["talent_id"]=2,
            x=>x["account"]!["unit_roles"]![0]!["role_id"]=2,
            x=>x["account"]!["unit_roles"]![0]!["slots"]![0]!["slot"]=2,
            x=>x["account"]!["unit_roles"]![0]!["slots"]![0]!["slot_level"]=0,
            x=>x["account"]!["unit_roles"]![0]!["slots"]![0]!["enhance_level"]=6,
            x=>x["account"]!["progress_mapping_sha256"]=new string('a',64),
            x=>x["account_id"]="456",x=>x["content_version"]="209901010101",
            x=>x["account"]!["unit_role_list"]=new JArray() })
        {
            var invalid=(JObject)data.DeepClone();change(invalid);
            Reject(()=>store.Save(Bytes(invalid),catalog,"123",now,hash),"bad_details");
            Check(store.Stamp(catalog,"123")==hash,"bad_details_preserve_cache");
        }
        var pub=JObject.Parse(PcrGuideDraft.Export(input));
        Check(!pub.ToString().Contains("attribute_levels")&&pub["Input"]!["Facts"]!.Count()==0,"public_draft_no_account_details");
        Console.WriteLine($"Guide progress checks passed: {count}; synthetic normalized response, original read adapter/store/panel; logins=0");
    }
}
