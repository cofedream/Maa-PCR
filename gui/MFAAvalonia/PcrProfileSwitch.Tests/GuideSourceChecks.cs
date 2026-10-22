using System;
using System.IO;
using System.Linq;
using System.Text;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class GuideSourceChecks
{
    public static void Run(string sourceRoot, string directory)
    {
        int count=0;
        void Check(bool value,string name) { if(!value) throw new Exception("source_"+name); count++; }
        void Reject(Action action,string name) { try { action(); } catch { count++; return; } throw new Exception("source_expected_rejection_"+name); }
        byte[] Bytes(object value) => Encoding.UTF8.GetBytes(PcrGuideReview.Serialize(value));
        var legacy=PcrGuideIdentity.Load(sourceRoot,"红焰深域 8-6");
        var oldJson=PcrGuideReview.Serialize(legacy);
        Check(!oldJson.Contains("ImportedSource"),"legacy_serialization_unchanged");
        var input=new PcrGuideInput { SourceUrl=PcrGuideIdentity.Url,GuideServer="国服 B 站",TargetServer="国服 B 站",
            GuideStage="红焰深域 8-8",TargetStage="红焰深域 8-8",Material="真实原图的离线核对夹具",RetrievedOn="2026-10-19",
            Catalog=new() { Server="cn-bilibili", ContentVersion="fixture", Source="fixture", Sha256=new string('a',64),
                Units=legacy.Members.Select(m=>new PcrGuideUnit(m.UnitId,m.Name)).ToList() } };
        legacy.Stage=input.GuideStage;
        legacy.ImportedSource=new(new string('b',64),"fixture; crop=0,0,344,66; layout=fire8-344x66","2026-10-19");
        var source=PcrGuideIdentity.Import(Bytes(legacy),input);
        Check(source.Reviewable() && !source.Valid(),"import_never_trusts_scores");
        input=PcrGuideDraft.FromSource(input,source);
        Check(input.RequiresImageReview && input.Identity!.Matches(input)==false,"generated_team_still_needs_confirmation");
        Check(input.OpeningSets!.Values.All(v=>v==null),"image_does_not_guess_sets");
        var view=new PcrGuideReviewView(Path.Combine(directory,"guide-source","review.json"));
        view.Display(input); view.ImportIdentity(Bytes(source));
        for(int i=0;i<5;i++)
        {
            view.ConfirmPortrait(i);
            Check(view.Capture().Identity!.Valid()==(i==4),"all_five_required_"+i);
        }
        var confirmed=view.Capture();
        Check(confirmed.Identity!.Matches(confirmed),"panel_confirmation_binds_source_and_conditions");
        Check(confirmed.Identity.Members.Select(m=>m.Score).SequenceEqual(source.Members.Select(m=>m.Score)),"scores_preserved");
        Reject(()=>PcrGuideTrial.CreatePlan(confirmed),"offline_import_has_no_trial");
        view.Check(); view.Save();
        var reopened=new PcrGuideReviewView(Path.Combine(directory,"guide-source","review.json"));
        Check(reopened.Capture().Identity!.Matches(reopened.Capture()),"save_reopen_keeps_same_material_confirmation");
        var draft=PcrGuideDraft.Import(new(),Bytes(new PcrGuideDraftFile { Input=JObject.Parse(PcrGuideDraft.Export(confirmed))["Input"]!.ToObject<PcrGuideInput>()! }));
        Check(draft.RequiresImageReview && !draft.Identity!.Valid() && draft.Identity.Members.All(m=>m.ConfirmedFor==null),"public_draft_clears_all_confirmation");
        var changed=PcrGuideReview.Parse(PcrGuideReview.Serialize(new PcrGuideDocument { Input=confirmed })).Input;
        changed.Material+="changed";
        Check(!changed.Identity!.Matches(changed),"material_change_invalidates");
        changed=PcrGuideReview.Parse(PcrGuideReview.Serialize(new PcrGuideDocument { Input=confirmed })).Input;
        changed.Identity!.ImportedSource=source.ImportedSource! with { Locator="another row" };
        Check(!changed.Identity.Valid(),"locator_change_invalidates");
        changed=PcrGuideDraft.ApplyTable(confirmed,"角色\tSET标记\n"+string.Join('\n',source.Members.Select(m=>$"{m.UnitId}\tO")));
        Check(changed.Identity==null && changed.RequiresImageReview && PcrGuideIdentity.Required(changed),"table_change_cannot_remove_gate");
        Check(PcrGuideReview.Check(changed,DateTimeOffset.Now).Findings.Any(f=>f.Item=="原图角色核对" && f.Status=="未知"),"missing_identity_reported");
        var raw=JObject.FromObject(source);
        void Bad(Action<JObject> edit,string name) { var value=(JObject)raw.DeepClone(); edit(value); Reject(()=>PcrGuideIdentity.Import(Bytes(value),input),name); }
        Bad(j=>j["ImageSha256"]=new string('0',64),"image_hash");
        Bad(j=>j["Stage"]="红焰深域 8-9","wrong_stage");
        Bad(j=>j["SourceUrl"]="file:///private","non_web_source");
        Bad(j=>j["Members"]![0]!["UnitId"]=107101,"wrong_role");
        Bad(j=>j["Members"]![0]!["Name"]="夏日优衣","wrong_name");
        Bad(j=>j["Members"]![0]!["IconUnitId"]=107131,"wrong_form");
        Bad(j=>j["Members"]![0]!["IconUnitId"]=132399,"missing_form_icon");
        Bad(j=>j["Members"]![0]!["Score"]=2,"invalid_score");
        Bad(j=>j["Members"]![0]!["Attribute"]="DARK","wrong_attribute");
        Bad(j=>j["ImportedSource"]!["RetrievedOn"]="2026-02-31","invalid_date");
        Bad(j=>j["ImportedSource"]!["RetrievedOn"]="2026-10-18","acquisition_date_mismatch");
        Bad(j=>j["ImportedSource"]!["OriginalSha256"]="missing","missing_original_hash");
        Bad(j=>j["unknown"]=true,"unsupported_field");
        Reject(()=>PcrGuideIdentity.Import(new byte[150001],input),"size_limit");
        Reject(()=>PcrGuideIdentity.Import(Encoding.UTF8.GetBytes(raw.ToString().Replace("\"Stage\":","\"Stage\":\"duplicate\",\"Stage\":")),input),"duplicate_key");
        var before=PcrGuideReview.Fingerprint(view.Capture());
        Reject(()=>view.ImportIdentity(Encoding.UTF8.GetBytes("bad JSON")),"bad_import");
        Check(PcrGuideReview.Fingerprint(view.Capture())==before,"bad_import_preserves_current");
        Console.WriteLine($"Guide source checks passed: {count}; offline only.");
    }
}
