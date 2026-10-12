using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MFAAvalonia.Configuration;
using MFAAvalonia.ViewModels.Pages;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
namespace MFAAvalonia.Pcr;
public partial class PcrLabyrinthGoal(int id, string name) : ObservableObject {
    public int GuildId { get; } = id;
    public string Name { get; } = name;
    [ObservableProperty] private bool enabled;
    [ObservableProperty] private string clears = "1";
    public string[] TargetModes { get; } = { "通关次数", "尝试次数" };
    [ObservableProperty] private string targetMode = "通关次数";
    [ObservableProperty] private string openingRoster = "";
}
public partial class PcrLabyrinthSettings : ObservableObject {
    public const string Key = "PcrLabyrinthSettingsV1";
    public const string CredentialsKey = "PcrLabyrinthCredentialsV1";
    private readonly TaskQueueViewModel vm;
    private bool ready;
    private bool credentialsDirty;
    private string? saved;
    private int? loadedMaxSeconds;
    private string? loadedMaxMinutes;
    public ObservableCollection<PcrLabyrinthGoal> Goals { get; } = new(new[] {
        new PcrLabyrinthGoal(1,"美食殿堂"), new PcrLabyrinthGoal(2,"破晓之星"), new PcrLabyrinthGoal(3,"咲恋救济院"),
        new PcrLabyrinthGoal(4,"王宫骑士团"),new PcrLabyrinthGoal(5,"拉比林斯") });
    public string[] RouteModes { get; } = { "价值路线 v2", "固定模板路线" };
    public string[] ThirdChoices { get; } = { "必须遗物", "必须事件", "两者都行" };
    [ObservableProperty] private string difficulty = "1";
    [ObservableProperty] private bool reroll;
    [ObservableProperty] private string maxAttempts = "100";
    [ObservableProperty] private string maxMinutes = "30";
    [ObservableProperty] private bool buyRelics = true;
    [ObservableProperty] private string routeMode = "价值路线 v2";
    [ObservableProperty] private string allowance = "1";
    [ObservableProperty] private string thirdChoice = "必须遗物";
    [ObservableProperty] private bool perfectStart = true;
    [ObservableProperty] private string area3BossIds = "312505,319604";
    [ObservableProperty] private string area5BossIds = "310103";
    public string[] BossModes { get; } = { "多队", "单队" };
    [ObservableProperty] private string bossMode = "多队";
    [ObservableProperty] private string deploymentWeights = "1,0.35,0.2";
    [ObservableProperty] private string damageWeight = "0.5";
    [ObservableProperty] private string survivalWeight = "0.23";
    [ObservableProperty] private string functionWeight = "0.13";
    [ObservableProperty] private string formationWeight = "0.04";
    [ObservableProperty] private string cohesionWeight = "0.1";
    [ObservableProperty] private string playerWeight = "70";
    [ObservableProperty] private bool attributePairingBonusEnabled = true;
    [ObservableProperty] private string duplicateRolePenalty = "8";
    [ObservableProperty] private string mixedDamagePenalty = "15";
    [ObservableProperty] private string effectiveCharacterSystemBonus = "8";
    [ObservableProperty] private bool preferPureBossDamageSystem = true;
    [ObservableProperty] private bool singleBossFallbackToMultiAfterThreeFailures = true;
    [ObservableProperty] private string singleBossRetryCountBeforeMulti = "2";
    [ObservableProperty] private bool rerollAfterThreeBattleFailures = true;
    [ObservableProperty] private string baselineLastArea = "3";
    [ObservableProperty] private string baselineTarget = "4";
    [ObservableProperty] private string debuffPivotMinimum = "12";
    [ObservableProperty] private string debuffPivotTolerance = "2";
    [ObservableProperty] private string relicMarkPriority = "";
    [ObservableProperty] private bool refreshShop = true;
    [ObservableProperty] private string refreshFromArea = "5";
    [ObservableProperty] private string normalWinRate = "95";
    [ObservableProperty] private string extremeWinRate = "70";
    private JObject personalScores = new();
    [ObservableProperty] private string expectedPlayerId = "";
    [ObservableProperty] private string username = "";
    [ObservableProperty] private string password = "";
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CredentialMask), nameof(CredentialsVisibilityText))]
    private bool showCredentials;
    public char CredentialMask => ShowCredentials ? '\0' : '*';
    public string CredentialsVisibilityText => ShowCredentials ? "隐藏" : "查看";
    [ObservableProperty] private string credentialsStatus = "玩家 ID 与账号凭据自动加密保存到本机当前 Windows 用户。";
    [ObservableProperty] private string status = "设置自动保存；从尚未推进的新开局开始。";
    [ObservableProperty] private string progress = "尚未开始";
    public PcrLabyrinthSettings(TaskQueueViewModel owner) {
        vm=owner; Goals[0].Enabled=true;
        saved=vm.Processor.InstanceConfiguration.GetValue(Key, "");
        if (!string.IsNullOrEmpty(saved)) {
            try {
                var d=JObject.Parse(saved);
                Difficulty=d["difficulty"]?.ToString() ?? "1"; Reroll=(bool?)d["reroll"] ?? false;
                MaxAttempts=d["max_attempts"]?.ToString() ?? "100";
                MaxMinutes=(((int?)d["max_seconds"] ?? 1800)/60).ToString(CultureInfo.InvariantCulture);
                loadedMaxSeconds=(int?)d["max_seconds"];loadedMaxMinutes=MaxMinutes;
                BuyRelics=(bool?)d["buy_relics"] ?? true;RefreshShop=(bool?)d["refresh_shop"] ?? true;
                if (d["strategy"] is JObject strategy) {
                    PlayerWeight=strategy["player_weight"]?.ToString() ?? "70";
                    AttributePairingBonusEnabled=(bool?)strategy["attribute_pairing_bonus_enabled"] ?? true;
                    DuplicateRolePenalty=strategy["duplicate_role_penalty"]?.ToString() ?? "8";
                    MixedDamagePenalty=strategy["mixed_damage_penalty"]?.ToString() ?? "15";
                    EffectiveCharacterSystemBonus=strategy["effective_character_system_bonus"]?.ToString() ?? "8";
                    PreferPureBossDamageSystem=(bool?)strategy["prefer_pure_boss_damage_system"] ?? true;
                    SingleBossFallbackToMultiAfterThreeFailures=(bool?)strategy["single_boss_fallback_to_multi_after_three_failures"] ?? true;
                    SingleBossRetryCountBeforeMulti=strategy["single_boss_retry_count_before_multi"]?.ToString() ?? "2";
                    RerollAfterThreeBattleFailures=(bool?)strategy["reroll_after_three_battle_failures"] ?? true;
                    BaselineLastArea=strategy["baseline_last_area"]?.ToString() ?? "3";
                    BaselineTarget=strategy["baseline_target"]?.ToString() ?? "4";
                    DebuffPivotMinimum=strategy["debuff_pivot_minimum"]?.ToString() ?? "12";
                    DebuffPivotTolerance=strategy["debuff_pivot_tolerance"]?.ToString() ?? "2";
                    if (strategy["relic_mark_priority"] is JArray priority) RelicMarkPriority=string.Join(",",priority.Values<string>());
                    RefreshFromArea=strategy["refresh_from_area"]?.ToString() ?? "5";
                    NormalWinRate=strategy["normal_win_rate"]?.ToString() ?? "95";
                    ExtremeWinRate=strategy["extreme_win_rate"]?.ToString() ?? "70";
                    if (strategy["opening_rosters"] is JObject rosters) foreach (var goal in Goals) if (rosters[goal.GuildId.ToString(CultureInfo.InvariantCulture)] is JArray slots) goal.OpeningRoster=string.Join(" / ",slots.Select(slot=>string.Join(",",slot.Values<string>())));
                    BossMode=(string?)strategy["boss_mode"]=="single" ? BossModes[1] : BossModes[0];
                    if (strategy["deployment_weights"] is JArray weights) DeploymentWeights=string.Join(",",weights.Select(x=>x.ToString(Formatting.None)));
                    var scores=strategy["scoring_overrides"];
                    DamageWeight=scores?["systemDamageWeight"]?.ToString() ?? "0.5";
                    SurvivalWeight=scores?["systemSurvivalWeight"]?.ToString() ?? "0.23";
                    FunctionWeight=scores?["systemFunctionWeight"]?.ToString() ?? "0.13";
                    FormationWeight=scores?["systemFormationWeight"]?.ToString() ?? "0.04";
                    CohesionWeight=scores?["systemCohesionWeight"]?.ToString() ?? "0.1";
                    if (strategy["personal_scores"] is JObject scoresByRole) personalScores=(JObject)scoresByRole.DeepClone();
                }

                RouteMode=(string?)d["route"]?["mode"]=="template" ? RouteModes[1] : RouteModes[0];
                ThirdChoice=(string?)d["route"]?["third_choice"] switch { "event"=>ThirdChoices[1],"either"=>ThirdChoices[2],_=>ThirdChoices[0] };
                Allowance=d["route"]?["allowance"]?.ToString() ?? "1";
                PerfectStart=(bool?)d["route"]?["perfect_start"] ?? true;
                if (d["route"]?["area3_bosses"] is JArray b3) Area3BossIds=string.Join(",",b3.Values<int>());
                if (d["route"]?["area5_bosses"] is JArray b5) Area5BossIds=string.Join(",",b5.Values<int>());
                foreach (var g in Goals) { var row=(d["draft_goals"] ?? d["goals"])?.FirstOrDefault(x=>(int?)x["guild_id"]==g.GuildId); g.Enabled=(bool?)row?["enabled"] ?? row!=null; g.Clears=row?["clears"]?.ToString() ?? "1"; g.TargetMode=(string?)row?["mode"]=="attempts" ? g.TargetModes[1]:g.TargetModes[0]; }
                Status="已载入黎明界设置。";
            } catch { saved=null; Status="保存的黎明界设置无法读取，请核对后重新保存。原文件已保留。"; }
        }
        LoadCredentials();
        foreach (var g in Goals) g.PropertyChanged+=(_,_)=>Save();
        ready=true;
    }
    protected override void OnPropertyChanged(PropertyChangedEventArgs e) {
        base.OnPropertyChanged(e);
        if (!ready || e.PropertyName is nameof(ShowCredentials) or nameof(CredentialMask) or nameof(CredentialsVisibilityText) or nameof(CredentialsStatus)) return;
        vm.NotifyPcrSelectionChanged();
        if (e.PropertyName is nameof(ExpectedPlayerId) or nameof(Username) or nameof(Password)) {
            credentialsDirty=true;
            if (vm.CanEditPcrTarget) SaveCredentials();
        }
        else if (e.PropertyName is not (nameof(Status) or nameof(Progress))) Save();
    }
    [RelayCommand] public void ToggleCredentials() => ShowCredentials=!ShowCredentials;
    public void HideCredentials() => ShowCredentials=false;
    private void LoadCredentials() {
        var encrypted=vm.Processor.InstanceConfiguration.GetValue(CredentialsKey, "");
        if (string.IsNullOrEmpty(encrypted)) return;
        try {
            if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
            var bytes=ProtectedData.Unprotect(Convert.FromBase64String(encrypted),null,DataProtectionScope.CurrentUser);
            try {
                var data=JObject.Parse(Encoding.UTF8.GetString(bytes));
                if ((int?)data["version"]!=1 || new[]{"expected_player_id","username","password"}.Any(key=>data[key]?.Type!=JTokenType.String))
                    throw new InvalidOperationException();
                ExpectedPlayerId=(string)data["expected_player_id"]!;
                Username=(string)data["username"]!;
                Password=(string)data["password"]!;
                CredentialsStatus="已载入本机加密保存的账号凭据；点击查看可显示。";
            } finally { CryptographicOperations.ZeroMemory(bytes); }
        } catch { CredentialsStatus="账号凭据无法解密，原有保存内容已保留；请在原 Windows 用户下打开，或重新填写。"; }
    }
    private void SaveCredentials() {
        if (!credentialsDirty) return;
        try {
            if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
            var bytes=Encoding.UTF8.GetBytes(new JObject { ["version"]=1,["expected_player_id"]=ExpectedPlayerId,
                ["username"]=Username,["password"]=Password }.ToString(Formatting.None));
            string encrypted;
            try { encrypted=Convert.ToBase64String(ProtectedData.Protect(bytes,null,DataProtectionScope.CurrentUser)); }
            finally { CryptographicOperations.ZeroMemory(bytes); }
            vm.Processor.InstanceConfiguration.SetValueAndPersist(CredentialsKey,encrypted);
            credentialsDirty=false;
            CredentialsStatus="账号凭据已加密保存；重开软件自动载入。";
        } catch { CredentialsStatus="账号凭据保存失败，原有保存内容已保留；请重试。输入内容仍可用于本次运行。"; }
    }
    private static int Number(string s,int min,int max,string label) {
        if (!int.TryParse(s,NumberStyles.None,CultureInfo.InvariantCulture,out var n) || n<min || n>max) throw new InvalidOperationException($"{label}请输入 {min}–{max} 的整数。");
        return n;
    }
    private static JArray Bosses(string text, int[] allowed) {
        if (string.IsNullOrWhiteSpace(text)) return new JArray();
        var ids=text.Split(',',StringSplitOptions.TrimEntries).Select(s=>Number(s,1,999999,"Boss ID")).ToArray();
        if (ids.Distinct().Count()!=ids.Length || ids.Any(x=>!allowed.Contains(x))) throw new InvalidOperationException("Boss ID 不在当前支持列表中。");
        return new JArray(ids);
    }
    private static double Weight(string text,double max,string label) {
        if (!double.TryParse(text,NumberStyles.AllowDecimalPoint,CultureInfo.InvariantCulture,out var value)
                || !double.IsFinite(value) || value<0 || value>max)
            throw new InvalidOperationException($"{label}请输入 0–{max} 的数值。");
        return value;
    }
    private JObject OpeningRostersSnapshot() {
        var rosters=new JObject();
        foreach (var goal in Goals.Where(g=>!string.IsNullOrWhiteSpace(g.OpeningRoster))) {
            var slots=goal.OpeningRoster.Split('/',StringSplitOptions.TrimEntries).Select(s=>s.Split(',',StringSplitOptions.TrimEntries)).ToArray();
            var roles=slots.SelectMany(s=>s).ToArray();
            if (slots.Length!=3 || roles.Any(s=>!System.Text.RegularExpressions.Regex.IsMatch(s,@"^[0-9]{4,6}$")) || roles.Distinct().Count()!=roles.Length)
                throw new InvalidOperationException($"{goal.Name}开局方案需要三个槽位；槽位用 / 分隔，候选角色ID用逗号分隔，角色不能重复。");
            rosters[goal.GuildId.ToString(CultureInfo.InvariantCulture)]=new JArray(slots.Select(slot=>new JArray(slot)));
        }
        return rosters;
    }
    private JArray RelicPrioritySnapshot() {
        if (string.IsNullOrWhiteSpace(RelicMarkPriority)) return new JArray();
        var marks=RelicMarkPriority.Split(',',StringSplitOptions.TrimEntries).Select(mark=>mark switch { "暴击"=>"会心", "削弱" or "崩弱"=>"弱体", _=>mark }).ToArray();
        if (marks.Distinct().Count()!=marks.Length || marks.Any(mark=>!new[]{"加速","会心","守备","强化","弱体"}.Contains(mark)))
            throw new InvalidOperationException("遗物印记优先级请填写不重复的加速、会心、守备、强化、弱体，用逗号分隔。");
        return new JArray(marks);
    }
    private JObject StrategySnapshot() {
        if (!BossModes.Contains(BossMode)) throw new InvalidOperationException("Boss 编组模式无效。");
        var parts=DeploymentWeights.Split(',',StringSplitOptions.TrimEntries);
        if (parts.Length!=3) throw new InvalidOperationException("招募权重请填写三队数值，用逗号分隔。");
        var deployment=parts.Select(x=>Weight(x,10,"招募权重")).ToArray();
        if (deployment[0]<=0) throw new InvalidOperationException("第一队招募权重必须大于 0。");
        var scores=new JObject {
            ["systemDamageWeight"]=Weight(DamageWeight,1,"伤害权重"),
            ["systemSurvivalWeight"]=Weight(SurvivalWeight,1,"生存权重"),
            ["systemFunctionWeight"]=Weight(FunctionWeight,1,"功能权重"),
            ["systemFormationWeight"]=Weight(FormationWeight,1,"站位权重"),
            ["systemCohesionWeight"]=Weight(CohesionWeight,1,"元素契合权重") };
        if (Math.Abs(scores.Properties().Sum(x=>(double)x.Value)-1)>0.000001)
            throw new InvalidOperationException("五项队伍评分权重之和必须为 1。");
        return new JObject { ["boss_mode"]=BossMode==BossModes[0] ? "multi":"single",
            ["player_weight"]=Number(PlayerWeight,0,100,"玩家评分占比"),
            ["attribute_pairing_bonus_enabled"]=AttributePairingBonusEnabled,
            ["duplicate_role_penalty"]=Number(DuplicateRolePenalty,0,30,"重复职能扣分"),
            ["mixed_damage_penalty"]=Number(MixedDamagePenalty,0,30,"物法混编扣分"),
            ["effective_character_system_bonus"]=Number(EffectiveCharacterSystemBonus,0,30,"有效效果角色加分"),
            ["prefer_pure_boss_damage_system"]=PreferPureBossDamageSystem,
            ["single_boss_fallback_to_multi_after_three_failures"]=SingleBossFallbackToMultiAfterThreeFailures,
            ["single_boss_retry_count_before_multi"]=Number(SingleBossRetryCountBeforeMulti,0,10,"单队Boss转多队前重试次数"),
            ["reroll_after_three_battle_failures"]=RerollAfterThreeBattleFailures,
            ["baseline_last_area"]=Number(BaselineLastArea,0,5,"补基础层数截止区域"),
            ["baseline_target"]=Number(BaselineTarget,1,4,"基础目标层数"),
            ["debuff_pivot_minimum"]=Number(DebuffPivotMinimum,5,15,"转追弱体最低层数"),
            ["debuff_pivot_tolerance"]=Number(DebuffPivotTolerance,0,5,"弱体落后容差"),
            ["relic_mark_priority"]=RelicPrioritySnapshot(),
            ["refresh_from_area"]=Number(RefreshFromArea,1,5,"商店刷新起始区域"),
            ["normal_win_rate"]=Number(NormalWinRate,0,100,"普通战胜率"),
            ["extreme_win_rate"]=Number(ExtremeWinRate,0,100,"EX战胜率"),
            ["opening_rosters"]=OpeningRostersSnapshot(),
            ["deployment_weights"]=new JArray(deployment),["scoring_overrides"]=scores,
            ["personal_scores"]=personalScores.DeepClone() };
    }
    public JObject Snapshot() {
        if (Goals.Any(g=>!g.TargetModes.Contains(g.TargetMode))) throw new InvalidOperationException("目标计数方式无效。");
        var goals=new JArray(Goals.Where(g=>g.Enabled).Select(g=>new JObject { ["guild_id"]=g.GuildId,["clears"]=Number(g.Clears,1,999,g.Name+"目标次数"),["mode"]=g.TargetMode==g.TargetModes[0] ? "clears":"attempts" }));
        if (goals.Count==0) throw new InvalidOperationException("请至少选择一个目标公会。");
        if (!RouteModes.Contains(RouteMode) || !ThirdChoices.Contains(ThirdChoice)) throw new InvalidOperationException("路线设置无效。");
        return new JObject { ["version"]=1,["goals"]=goals,["difficulty"]=Number(Difficulty,1,5,"难度"),["reroll"]=Reroll,
            ["max_attempts"]=Number(MaxAttempts,1,100,"刷取次数"),["max_seconds"]=loadedMaxSeconds is >=1 and <=1800 && MaxMinutes==loadedMaxMinutes ? loadedMaxSeconds.Value:Number(MaxMinutes,1,30,"刷取分钟")*60,
            ["buy_relics"]=BuyRelics,["refresh_shop"]=RefreshShop,["strategy"]=StrategySnapshot(),["route"]=new JObject {
                ["mode"]=RouteMode==RouteModes[0] ? "value":"template",["perfect_start"]=PerfectStart,["allowance"]=Number(Allowance,0,3,"价值容差"),
                ["third_choice"]=ThirdChoice==ThirdChoices[0] ? "relic":ThirdChoice==ThirdChoices[1] ? "event":"either",
                ["area3_bosses"]=Bosses(Area3BossIds,new[]{301206,303306,319604,312505,306604}),
                ["area5_bosses"]=Bosses(Area5BossIds,new[]{302501,301701,310103,315004,319401}) } };
    }
    [RelayCommand] public void Save() {
        if (!ready || !vm.CanEditPcrTarget) return;
        vm.NotifyPcrSelectionChanged();
        SaveCredentials();
        try {
            var data=Snapshot();
            data["draft_goals"]=new JArray(Goals.Select(g=>new JObject { ["guild_id"]=g.GuildId,["enabled"]=g.Enabled,["clears"]=g.Clears,["mode"]=g.TargetMode==g.TargetModes[0] ? "clears":"attempts" }));
            var value=data.ToString(Formatting.None);
            if (value==saved) return;
            vm.Processor.InstanceConfiguration.SetValueAndPersist(Key,value);
            if (InstanceConfiguration.ReadValueFromFile(vm.Processor.InstanceId,Key)!=value) throw new InvalidOperationException("设置未写入磁盘，请重新保存。");
            saved=value; Status="黎明界设置已保存。";
        } catch (Exception ex) { Status=ex.Message; }
    }
}
