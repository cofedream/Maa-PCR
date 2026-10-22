using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Reflection;
using System.Threading.Tasks;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class GuideAccountChecks
{
    public static void Run(string directory)
    {
        var root = Path.Combine(directory, "guide-accounts-check"); Directory.CreateDirectory(root);
        var store = new PcrGuideAccountStore(root); var now = DateTimeOffset.Now; var count = 0;
        void Check(bool value, string label) { if (!value) throw new Exception("account_" + label); count++; }
        void Reject(Action action, string label) { try { action(); } catch { count++; return; } throw new Exception("account_expected_reject_" + label); }
        byte[] Bytes(JObject o) => Encoding.UTF8.GetBytes(o.ToString());
        var ids = new[] { 101001,134901,129701,116701,134701,135501,104701,100101,110101 };
        var catalog = new PcrGuideCatalog { Server="cn-bilibili", ContentVersion="202610141431", Source="synthetic", Sha256=new string('a',64),
            Units=ids.Select(id=>new PcrGuideUnit(id,"合成角色"+id)).ToList() };
        var data = new JObject { ["schema_version"]=1,["server"]=catalog.Server,["account_id"]="123",["content_version"]=catalog.ContentVersion,
            ["observed_at"]=now.AddMinutes(-5).ToString("O"),["ownership_complete"]=true,["unknown_fields"]=new JArray(),
            ["units"]=new JArray(ids.Select(id=>new JObject { ["unit_id"]=id,["level"]=365,["rank"]=38,["rarity"]=5,["battle_rarity"]=5 })) };
        PcrGuideInput Team(int offset)
        {
            var input = new PcrGuideInput { Catalog=catalog };
            foreach (var id in ids.Skip(offset).Take(5)) foreach(var field in new[]{"持有","等级","Rank","星级"})
            { var c = new PcrGuideCondition { Field=field }; PcrGuideData.Bind(c,catalog.Units.Single(u=>u.UnitId==id)); input.Conditions.Add(c); }
            return input;
        }
        store.Save(Bytes(data),catalog,"123",now,null); var hash=store.Stamp(catalog,"123");
        for (var team=0;team<3;team++)
        {
            var result=store.Reuse(Team(team*2),"123",now);
            Check(result.Facts.Count==20 && result.Facts.All(f=>f.Confirmed),"three_team_projection_"+team);
            Check(result.Snapshot!.ObservedAt==(string)data["observed_at"]! && result.Snapshot.Sha256==hash,"original_timestamp_hash_"+team);
            Check(store.Describe(result,now).Contains("完整持有名单") && store.Describe(result,now).Contains("可复用"),"coverage_"+team);
        }
        var input0=store.Reuse(Team(0),"123",now);
        Check(input0.Facts.Any(f=>f.UnitId==101001&&f.Field=="持有"&&f.Value=="是") && input0.Facts.Any(f=>f.UnitId==134901&&f.Field=="等级"&&f.Value=="365"),"maho_kanon_synthetic_projection");
        Check(store.Describe(input0,now.AddHours(25)).Contains("过期"),"expiry_not_refreshed");
        Reject(()=>store.Reuse(input0,"456",now),"wrong_account");
        Reject(()=>store.Save(Bytes(data),catalog,"456",now,null),"wrong_read_identity");
        Reject(()=>store.Save(Bytes(data),catalog,"123",now,null),"concurrent_change");
        var invalid=(JObject)data.DeepClone(); invalid["units"]![8]!["level"]=-1;
        Reject(()=>store.Save(Bytes(invalid),catalog,"123",now,hash),"unselected_bad_unit");
        invalid=(JObject)data.DeepClone(); invalid["item_list"]=new JArray();
        Reject(()=>store.Save(Bytes(invalid),catalog,"123",now,hash),"raw_inventory");
        Check(store.Stamp(catalog,"123")==hash,"failed_save_preserves");
        var partial=(JObject)data.DeepClone(); partial["ownership_complete"]=false; partial["units"]=new JArray(data["units"]!.Take(5).Select(x=>x.DeepClone()));
        Reject(()=>store.Save(Bytes(partial),catalog,"123",now,hash),"no_downgrade");
        var partialStore=new PcrGuideAccountStore(Path.Combine(root,"partial")); partialStore.Save(Bytes(partial),catalog,"123",now,null);
        var missing=partialStore.Reuse(Team(4),"123",now);
        Check(missing.Facts.Where(f=>f.UnitId==104701).All(f=>!f.Confirmed&&f.Value==""),"partial_absence_unknown");
        Check(partialStore.Describe(missing,now).Contains("部分资料"),"partial_label");
        var absent=(JObject)data.DeepClone(); absent["units"]=new JArray(data["units"]!.Take(5).Select(x=>x.DeepClone()));
        Check(PcrGuideData.Import(Team(4),Bytes(absent),"synthetic",now).Facts.Single(f=>f.UnitId==104701&&f.Field=="持有").Value=="否","complete_absence_unowned");
        var viewPath=Path.Combine(root,"guide.json"); var reads=0;
        var view=new PcrGuideReviewView(viewPath,(_,_)=>{reads++;return Task.FromResult(Bytes(data));},()=>"123");
        view.Display(new PcrGuideInput { Catalog=catalog }); view.ReadSnapshot();
        Check(reads==1&&view.Capture().Snapshot!=null,"read_without_guide_identity_or_team");
        foreach(var offset in new[]{0,2,4})
        {
            view.ApplyTeamTable("角色\t等级\tRank\n"+string.Join("\n",ids.Skip(offset).Take(5).Select(id=>$"{id}\t365\t38")));
            Check(view.Capture().Facts.Any(f=>f.UnitId==ids[offset]&&f.Field=="等级"&&f.Value=="365")&&reads==1,"ui_switch_without_login_"+offset);
        }
        var before=store.Stamp(catalog,"123");
        var failed=new PcrGuideReviewView(viewPath,(_,_)=>Task.FromException<byte[]>(new Exception("synthetic private")),()=>"123");
        failed.Display(input0); failed.ReadSnapshot(); Check(store.Stamp(catalog,"123")==before&&failed.Capture().Snapshot?.Sha256==hash,"read_failure_keeps_cache_and_panel");
        var large=(JObject)data.DeepClone(); large["units"]=new JArray(Enumerable.Range(100000,2000).Select(id=>new JObject{["unit_id"]=id,["level"]=365,["skills"]=new JObject{["ub"]=365,["s1"]=365,["s2"]=365,["ex"]=365}}));
        var largeCatalog=new PcrGuideCatalog{Server=catalog.Server,ContentVersion=catalog.ContentVersion,Source="synthetic",Sha256=new string('b',64),Units=Enumerable.Range(100000,2000).Select(id=>new PcrGuideUnit(id,"合成"+id)).ToList()};
        var message=new JObject{["kind"]="result",["outcome"]="complete",["snapshot"]=large};
        var read=typeof(PcrGuideInput).Assembly.GetType("MFAAvalonia.Pcr.PcrGuideSyncOwner")!.GetMethod("ReadResult",BindingFlags.NonPublic|BindingFlags.Static)!;
        var bytes=(byte[])read.Invoke(null,new object[]{message.ToString()})!;
        Check(message.ToString().Length>262144 && PcrGuideData.AccountSnapshot(bytes,largeCatalog,"123",now)["units"]!.Count()==2000,"large_owner_response");
        Console.WriteLine($"Guide account checks passed: {count}; data synthetic; protocol logins=0");
    }
}
