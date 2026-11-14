using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.IO;
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
public partial class PcrLabyrinthSettings : ObservableObject {
    public const string Key = "PcrLabyrinthSettingsV1";
    public const string CredentialsKey = "PcrLabyrinthCredentialsV1";
    private readonly TaskQueueViewModel vm;
    private bool ready;
    private bool relicPriorityMigrated;
    private bool credentialsDirty;
    private string? saved;
    private int? loadedMaxSeconds;
    private string? loadedMaxMinutes;
    private PcrLabyrinthAccounts? accountLibrary;
    private PcrLabyrinthAccount? selectedAccount;
    private bool switchingAccount;
    private PcrLabyrinthBossCatalog? bossCatalog;
    private string bossCatalogError = "";
    public ObservableCollection<PcrLabyrinthAccount> Accounts { get; } = new();
    public IReadOnlyList<PcrLabyrinthServer> Servers => PcrLabyrinthServer.All;
    [ObservableProperty] private string accountName = "账号 1";
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(SelectedServer),nameof(LoginLabel),nameof(PasswordLabel),nameof(LoginHint))]
    private string server = "cn-bilibili";
    public PcrLabyrinthServer? SelectedServer { get => Servers.FirstOrDefault(item=>item.Id==Server); set { if (value is not null) Server=value.Id; } }
    public string LoginLabel => Server=="cn-bilibili" ? "登录账号" : "渠道登录 UID";
    public string PasswordLabel => Server=="cn-bilibili" ? "密码" : "渠道 access_key";
    public string LoginHint => Server=="cn-bilibili" ? "填写 B 站账号和密码；账号须与模拟器中的游戏一致。"
        : "填写本渠道授权得到的 UID 和 access_key；游戏玩家 ID 仍填个人简介中的 ID。切换渠道后请重新填写凭据。";
    [ObservableProperty, NotifyPropertyChangedFor(nameof(PauseLabel))] private bool isPaused;
    [ObservableProperty, NotifyPropertyChangedFor(nameof(PauseLabel))] private bool pausePending;
    public string PauseLabel => PausePending ? "正在等待暂停…" : IsPaused ? "继续" : "暂停";
    [ObservableProperty, NotifyPropertyChangedFor(nameof(DeleteAccountText))] private bool deleteAccountPending;
    public string DeleteAccountText => DeleteAccountPending ? "确认删除所选账号" : "删除所选账号";
    public PcrLabyrinthAccount? SelectedAccount {
        get => selectedAccount;
        set {
            if (value is null || ReferenceEquals(value,selectedAccount) || !Accounts.Contains(value)) return;
            if (!ready) { SetProperty(ref selectedAccount,value); return; }
            if (!vm.CanEditPcrTarget || !SaveBeforeAccountChange()) { OnPropertyChanged(nameof(SelectedAccount)); return; }
            try {
                accountLibrary!.Save(Accounts.ToArray(),value.Id);
                SetProperty(ref selectedAccount,value); ApplyAccount(value); vm.NotifyPcrSelectionChanged();
            } catch (Exception ex) { Status="保存失败："+ex.Message; OnPropertyChanged(nameof(SelectedAccount)); }
        }
    }
    partial void OnServerChanged(string value) {
        if (!ready || switchingAccount) return;
        var wasReady=ready; ready=false;
        try { Username=""; Password=""; ExpectedPlayerId=""; ShowCredentials=false; credentialsDirty=true; }
        finally { ready=wasReady; }
        CredentialsStatus="服务器已更改，请重新填写该服务器的玩家 ID、登录 UID 与凭据。";
    }
    public string[] SpecialEquipmentModes { get; } = { "关闭", "自动装备" };
    [ObservableProperty] private string specialEquipmentMode = "关闭";
    [ObservableProperty] private bool specialEquipmentNormal = true;
    [ObservableProperty] private bool specialEquipmentEx = true;
    [ObservableProperty] private bool specialEquipmentBoss = true;
    public int[] ResponseIntervals { get; } = { 300,500 };
    [ObservableProperty] private int responseIntervalMs = 300;
    [ObservableProperty] private bool autoDatabaseUpdate = true;
    public ObservableCollection<PcrLabyrinthGoal> Goals { get; }
    public string[] RouteModes { get; } = { "价值路线 v2", "固定模板路线" };
    public string[] ThirdChoices { get; } = { "必须遗物", "必须事件", "两者都行" };
    [ObservableProperty] private string difficulty = "1";
    [ObservableProperty] private bool reroll = true;
    [ObservableProperty] private bool keepOldRun;
    [ObservableProperty] private string maxAttempts = "100";
    [ObservableProperty] private string maxMinutes = "30";
    [ObservableProperty] private bool buyRelics = true;
    [ObservableProperty] private string routeMode = "价值路线 v2";
    [ObservableProperty] private string allowance = "1";
    [ObservableProperty] private string thirdChoice = "必须遗物";
    [ObservableProperty] private bool perfectStart = true;
    public IReadOnlyList<PcrLabyrinthBossChoice> Area3Bosses { get; }
    public IReadOnlyList<PcrLabyrinthBossChoice> Area5Bosses { get; }
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasBossFilterStatus))]
    private string bossFilterStatus = "";
    public bool HasBossFilterStatus => BossFilterStatus.Length>0;
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
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasRelicPriorityNotice))]
    private string relicPriorityNotice = "";
    public bool HasRelicPriorityNotice => RelicPriorityNotice.Length>0;
    [ObservableProperty] private bool refreshShop = true;
    [ObservableProperty] private string refreshFromArea = "5";
    [ObservableProperty] private string normalWinRate = "95";
    [ObservableProperty] private string extremeWinRate = "70";
    public string[] EventChainPolicies { get; } = { "自动（按价值模型）", "优先继续", "优先停止" };
    [ObservableProperty] private string eventChainPolicy = "自动（按价值模型）";
    public ObservableCollection<PcrLabyrinthEventSetting> EventChoiceItems { get; } = new();
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasEventChoicesStatus))]
    private string eventChoicesStatus = "";
    public bool HasEventChoicesStatus => EventChoicesStatus.Length>0;
    private JObject savedEventChoiceOverrides = new();
    private JObject personalScores = new();
    [ObservableProperty, NotifyPropertyChangedFor(nameof(AccountSummary))] private string expectedPlayerId = "";
    [ObservableProperty, NotifyPropertyChangedFor(nameof(AccountSummary))] private string username = "";
    [ObservableProperty, NotifyPropertyChangedFor(nameof(AccountSummary))] private string password = "";
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CredentialMask), nameof(CredentialsVisibilityText))]
    private bool showCredentials;
    public char CredentialMask => ShowCredentials ? '\0' : '*';
    public string CredentialsVisibilityText => ShowCredentials ? "隐藏" : "查看";
    [ObservableProperty] private string credentialsStatus = "玩家 ID 与账号凭据自动加密保存到本机当前 Windows 用户。";
    [ObservableProperty, NotifyPropertyChangedFor(nameof(SaveButtonText))] private string status = "修改后自动保存；默认再次开始会放弃旧局后新开。";
    public string SaveButtonText => credentialsDirty || Status.StartsWith("保存失败", StringComparison.Ordinal) ? "重试保存" : "重新保存";
    public string AccountSummary => credentialsDirty ? "账号有未保存修改，请展开后重试。"
        : string.IsNullOrWhiteSpace(ExpectedPlayerId) || string.IsNullOrWhiteSpace(Username) || string.IsNullOrWhiteSpace(Password) ? "账号待填写，展开后设置。"
        : "账号已填写，默认隐藏。";
    [ObservableProperty] private string progress = "尚未开始";
    private PcrLabyrinthCharacterCatalog? characterCatalog;
    public PcrLabyrinthCharacterCatalog CharacterCatalog => characterCatalog ??= CreateCharacterCatalog();
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasRosterStatus))]
    private string rosterStatus = "";
    public bool HasRosterStatus => RosterStatus.Length>0;
    public PcrLabyrinthSettings(TaskQueueViewModel owner) {
        vm=owner;
        Goals=new(new[] {
            new PcrLabyrinthGoal(1,"美食殿堂",this), new PcrLabyrinthGoal(2,"破晓之星",this), new PcrLabyrinthGoal(3,"咲恋救济院",this),
            new PcrLabyrinthGoal(4,"王宫骑士团",this),new PcrLabyrinthGoal(5,"拉比林斯",this) });
        Goals[0].Enabled=true;
        try { bossCatalog=PcrLabyrinthBossCatalog.Load(); }
        catch (Exception ex) { bossCatalogError=ex.Message; }
        Area3Bosses=bossCatalog is null ? new List<PcrLabyrinthBossChoice>() : bossCatalog.Choices(3,this);
        Area5Bosses=bossCatalog is null ? new List<PcrLabyrinthBossChoice>() : bossCatalog.Choices(5,this);
        // 先按目录里的默认勾选：账号库读取失败，或载入中途抛错而没走到本轮筛选时，
        // 界面仍保持默认限制（312505,319604／310103），而不是静默变成不限制。
        ApplyBossFilters(null);
        LoadEventChoices();
        try {
            accountLibrary=new PcrLabyrinthAccounts(vm.Processor.InstanceConfiguration);
            foreach (var account in accountLibrary.Accounts) Accounts.Add(account);
            selectedAccount=Accounts.Single(account=>account.Id==accountLibrary.SelectedId);
            ApplyAccount(selectedAccount);
        } catch (Exception ex) {
            Status="保存的黎明界账号库无法读取，原内容已保留："+ex.Message;
            CredentialsStatus="未加载账号凭据，请保留原配置并导出诊断记录。";
        }
        foreach (var g in Goals) g.PropertyChanged+=(_,_)=>SaveCore(automatic:true);
        ready=true;
        FlushRelicPriorityMigration();
    }
    private void LoadEventChoices() {
        try {
            var catalog=PcrLabyrinthEventCatalog.Load();
            foreach (var info in catalog.Events.Where(info => info.Configurable)) EventChoiceItems.Add(new PcrLabyrinthEventSetting(info,this));
            EventChoicesStatus="";
        } catch (Exception ex) {
            EventChoicesStatus="事件目录加载失败，事件选项保持已保存值："+ex.Message;
        }
    }
    private void ApplySavedEventChoices() {
        foreach (var item in EventChoiceItems) {
            var token=savedEventChoiceOverrides[item.EventId];
            item.LoadSelection(token?.Type==JTokenType.Integer ? (int?)token : null);
        }
    }
    internal void EventChoiceEdited() {
        if (!ready) return;
        vm.NotifyPcrSelectionChanged();
        SaveCore(automatic:true);
    }
    internal void BossSelectionEdited() {
        if (!ready) return;
        // 用户已重新勾选，本次保存会写入清洗后的 ID；载入时的“已忽略”提示不再成立。
        BossFilterStatus="";
        vm.NotifyPcrSelectionChanged();
        SaveCore(automatic:true);
    }
    // 读回时只保留表中仍存在的 ID：陌生 ID 逐个丢弃，全部丢弃即视为不限制（空选择）。
    private void ApplyBossFilters(JObject? route) {
        // 目录加载失败时保持可见错误；不把“无法读取目录”静默当成“不限制”。
        if (bossCatalog is null) {
            BossFilterStatus="Boss 目录加载失败，Boss 筛选暂不可用："+bossCatalogError;
            return;
        }
        var ignored=new List<int>();
        ApplyBossSelection(Area3Bosses,3,route?["area3_bosses"],ignored);
        ApplyBossSelection(Area5Bosses,5,route?["area5_bosses"],ignored);
        BossFilterStatus=ignored.Count==0 ? "" : $"已忽略不再支持的 Boss ID：{string.Join(",",ignored)}。";
    }
    private void ApplyBossSelection(IReadOnlyList<PcrLabyrinthBossChoice> choices,int area,JToken? token,List<int> ignored) {
        var requested=token is JArray array
            ? array.OfType<JValue>().Where(value=>value.Type==JTokenType.Integer).Select(value=>value.Value<int>()).ToArray()
            : bossCatalog!.DefaultIds(area);
        foreach (var choice in choices) choice.Selected=requested.Contains(choice.Id);
        ignored.AddRange(requested.Where(id=>choices.All(choice=>choice.Id!=id)));
    }
    private void ApplyAccount(PcrLabyrinthAccount account) {
        var wasReady=ready; ready=false; switchingAccount=true;
        try {
            saved=account.Settings.Count==0 ? null : account.Settings.ToString(Formatting.None);
            AccountName=account.Name; Server=account.Server;
            foreach (var goal in Goals) {
                foreach (var slot in goal.Slots) { slot.ClearLoaded(); slot.Refresh(); }
                goal.RosterWarning=""; goal.RosterHint="";
            }
            LoadSettings(account.Settings);
            ExpectedPlayerId=Username=Password="";
            LoadCredentials(account.Credentials);
            ShowCredentials=false; credentialsDirty=false; DeleteAccountPending=false;
            Status=Servers.Any(item=>item.Id==Server) ? "已载入所选账号的黎明界设置。" : "未知服务器，请重新选择服务器并填写凭据。";
        } finally { ready=wasReady; switchingAccount=false; }
        FlushRelicPriorityMigration();
        OnPropertyChanged(nameof(AccountSummary));
    }
    private void LoadSettings(JObject d) {
        Difficulty=d["difficulty"]?.ToString() ?? "1"; Reroll=(bool?)d["reroll"] ?? true;
        KeepOldRun=(bool?)d["keep_old_run"] ?? false;
        MaxAttempts=d["max_attempts"]?.ToString() ?? "100";
        MaxMinutes=(((int?)d["max_seconds"] ?? 1800)/60).ToString(CultureInfo.InvariantCulture);
        loadedMaxSeconds=(int?)d["max_seconds"];loadedMaxMinutes=MaxMinutes;
        BuyRelics=(bool?)d["buy_relics"] ?? true;RefreshShop=(bool?)d["refresh_shop"] ?? true;
        var strategy=d["strategy"] as JObject ?? new JObject();
        {
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
            RelicMarkPriority=NormalizeRelicPriority(strategy["relic_mark_priority"] as JArray);
            RefreshFromArea=strategy["refresh_from_area"]?.ToString() ?? "5";
            NormalWinRate=strategy["normal_win_rate"]?.ToString() ?? "95";
            ExtremeWinRate=strategy["extreme_win_rate"]?.ToString() ?? "70";
            EventChainPolicy=(string?)strategy["event_chain_policy"] switch { "continue"=>EventChainPolicies[1],"stop"=>EventChainPolicies[2],_=>EventChainPolicies[0] };
            savedEventChoiceOverrides=strategy["event_choice_overrides"] is JObject overrides ? (JObject)overrides.DeepClone() : new JObject();
            if (strategy["opening_rosters"] is JObject rosters) foreach (var goal in Goals) if (rosters[goal.GuildId.ToString(CultureInfo.InvariantCulture)] is JArray slots) goal.LoadOpeningRoster(slots);
            BossMode=(string?)strategy["boss_mode"]=="single" ? BossModes[1] : BossModes[0];
            DeploymentWeights=strategy["deployment_weights"] is JArray weights ? string.Join(",",weights.Select(x=>x.ToString(Formatting.None))) : "1,0.35,0.2";
            var scores=strategy["scoring_overrides"];
            DamageWeight=scores?["systemDamageWeight"]?.ToString() ?? "0.5";
            SurvivalWeight=scores?["systemSurvivalWeight"]?.ToString() ?? "0.23";
            FunctionWeight=scores?["systemFunctionWeight"]?.ToString() ?? "0.13";
            FormationWeight=scores?["systemFormationWeight"]?.ToString() ?? "0.04";
            CohesionWeight=scores?["systemCohesionWeight"]?.ToString() ?? "0.1";
            personalScores=strategy["personal_scores"] is JObject scoresByRole ? (JObject)scoresByRole.DeepClone() : new JObject();
            SpecialEquipmentMode=(string?)strategy["special_equipment_mode"]=="auto_equip" ? SpecialEquipmentModes[1] : SpecialEquipmentModes[0];
            var equipmentBattles=strategy["special_equipment_battles"] as JArray;
            SpecialEquipmentNormal=equipmentBattles is null || equipmentBattles.Values<string>().Contains("NORMAL");
            SpecialEquipmentEx=equipmentBattles is null || equipmentBattles.Values<string>().Contains("EX");
            SpecialEquipmentBoss=equipmentBattles is null || equipmentBattles.Values<string>().Contains("BOSS");
            ResponseIntervalMs=(int?)strategy["response_interval_ms"] ?? 300;
            AutoDatabaseUpdate=(bool?)strategy["auto_database_update"] ?? true;
        }
        ApplySavedEventChoices();

        RouteMode=(string?)d["route"]?["mode"]=="template" ? RouteModes[1] : RouteModes[0];
        ThirdChoice=(string?)d["route"]?["third_choice"] switch { "event"=>ThirdChoices[1],"either"=>ThirdChoices[2],_=>ThirdChoices[0] };
        Allowance=d["route"]?["allowance"]?.ToString() ?? "1";
        PerfectStart=(bool?)d["route"]?["perfect_start"] ?? true;
        ApplyBossFilters(d["route"] as JObject);
        foreach (var g in Goals) { var row=(d["draft_goals"] ?? d["goals"])?.FirstOrDefault(x=>(int?)x["guild_id"]==g.GuildId); g.Enabled=(bool?)row?["enabled"] ?? (row!=null || d["draft_goals"] is null && d["goals"] is null && g.GuildId==1); g.Clears=row?["clears"]?.ToString() ?? "1"; g.TargetMode=(string?)row?["mode"]=="attempts" ? g.TargetModes[1]:g.TargetModes[0]; }
    }
    private bool SaveBeforeAccountChange() {
        SaveCore(automatic:false);
        return !credentialsDirty && !Status.StartsWith("保存失败",StringComparison.Ordinal);
    }
    private void PersistAccount(JObject data,string encrypted) {
        if (accountLibrary is null || selectedAccount is null) throw new InvalidOperationException("账号库尚未成功载入，原内容不会被覆盖。");
        var name=AccountName.Trim();
        if (name.Length is <1 or >40 || name.Any(char.IsControl)) throw new InvalidOperationException("账号名称请输入 1–40 个可见字符。");
        if (!Servers.Any(item=>item.Id==Server)) throw new InvalidOperationException("请选择有效的服务器。");
        var replacement=new PcrLabyrinthAccount(selectedAccount.Id,name,Server,data,encrypted);
        var candidate=Accounts.Select(account=>account==selectedAccount ? replacement : account).ToArray();
        accountLibrary.Save(candidate,replacement.Id);
        if (selectedAccount.Server!=Server || selectedAccount.Credentials!=encrypted) ClearSdkSession(selectedAccount);
        selectedAccount.Name=name; selectedAccount.Server=Server;
        selectedAccount.Settings=(JObject)data.DeepClone(); selectedAccount.Credentials=encrypted;
    }
    [RelayCommand] public void AddAccount() {
        if (!vm.CanEditPcrTarget || !SaveBeforeAccountChange()) return;
        if (Accounts.Count>=100) { Status="账号库最多保存 100 个账号。"; return; }
        var account=new PcrLabyrinthAccount(Guid.NewGuid().ToString("N"),$"账号 {Accounts.Count+1}","cn-bilibili",new JObject(),"");
        try {
            accountLibrary!.Save(Accounts.Append(account).ToArray(),account.Id);
            Accounts.Add(account); selectedAccount=account; OnPropertyChanged(nameof(SelectedAccount)); ApplyAccount(account);
            vm.NotifyPcrSelectionChanged();
        } catch (Exception ex) { Status="保存失败："+ex.Message; }
    }
    [RelayCommand] public void DeleteAccount() {
        if (!vm.CanEditPcrTarget || selectedAccount is null || accountLibrary is null) return;
        if (!DeleteAccountPending) { DeleteAccountPending=true; Status="再次点击确认删除该账号及其保存的设置；其他账号保持。"; return; }
        var removed=selectedAccount;
        var remaining=Accounts.Where(account=>account!=removed).ToList();
        if (remaining.Count==0) remaining.Add(new PcrLabyrinthAccount(Guid.NewGuid().ToString("N"),"账号 1","cn-bilibili",new JObject(),""));
        try {
            accountLibrary.Save(remaining,remaining[0].Id);
            ClearSdkSession(removed);
            Accounts.Remove(removed); if (Accounts.Count==0) Accounts.Add(remaining[0]);
            selectedAccount=remaining[0]; OnPropertyChanged(nameof(SelectedAccount)); ApplyAccount(selectedAccount);
            vm.NotifyPcrSelectionChanged();
        } catch (Exception ex) { Status="保存失败："+ex.Message; }
    }
    public JObject GetPersonalScores() => (JObject)personalScores.DeepClone();
    public void ApplyPersonalScores(JObject values) {
        if (!vm.CanEditPcrTarget) throw new InvalidOperationException("任务运行中不能修改个人评分。");
        if (values.Count>1000 || values.Properties().Any(item=>!System.Text.RegularExpressions.Regex.IsMatch(item.Name,@"^[1-9][0-9]{3,5}$")
            || item.Value.Type is not (JTokenType.Integer or JTokenType.Float) || !double.IsFinite(item.Value.Value<double>())
            || item.Value.Value<double>() is <0 or >100)) throw new InvalidOperationException("个人评分数据无效。");
        var previous=personalScores;
        personalScores=(JObject)values.DeepClone(); SaveCore(automatic:true);
        if (Status.StartsWith("保存失败",StringComparison.Ordinal)) { personalScores=previous; throw new InvalidOperationException(Status); }
    }
    private PcrLabyrinthCharacterCatalog CreateCharacterCatalog() => PcrLabyrinthCharacterCatalog.Load();
    internal PcrLabyrinthCharacterCatalog? TryCharacterCatalog() {
        try {
            characterCatalog ??= CreateCharacterCatalog();
            RosterStatus="";
            return characterCatalog;
        } catch (Exception ex) { RosterStatus="角色图鉴加载失败："+ex.Message; return null; }
    }
    internal IReadOnlyList<PcrLabyrinthCharacter> PoolForGuild(PcrLabyrinthGoal goal) {
        if (TryCharacterCatalog() is not { } catalog) return Array.Empty<PcrLabyrinthCharacter>();
        var roster=catalog.ForGuild(goal.GuildId);
        foreach (var character in roster.Characters)
            character.ToggleCommand=new RelayCommand(()=>goal.Toggle(character));
        return roster.Characters;
    }
    internal PcrLabyrinthCharacter PlaceholderCharacter(string id) =>
        new(id, $"未列入预置名单（{id}）", PcrLabyrinthCharacterCatalog.Normalize(id), null, placeholder:true);
    internal void RosterEdited() {
        if (!ready) return;
        vm.NotifyPcrSelectionChanged();
        SaveCore(automatic:true);
    }
    protected override void OnPropertyChanged(PropertyChangedEventArgs e) {
        base.OnPropertyChanged(e);
        if (!ready || e.PropertyName is nameof(ShowCredentials) or nameof(CredentialMask) or nameof(CredentialsVisibilityText) or nameof(CredentialsStatus)
                or nameof(RosterStatus) or nameof(HasRosterStatus) or nameof(AccountSummary) or nameof(SaveButtonText)
                or nameof(SelectedAccount) or nameof(SelectedServer) or nameof(LoginLabel) or nameof(PasswordLabel) or nameof(LoginHint)
                or nameof(IsPaused) or nameof(PausePending) or nameof(PauseLabel) or nameof(DeleteAccountPending) or nameof(DeleteAccountText)
                or nameof(BossFilterStatus) or nameof(HasBossFilterStatus)
                or nameof(RelicPriorityNotice) or nameof(HasRelicPriorityNotice)) return;
        vm.NotifyPcrSelectionChanged();
        if (e.PropertyName is nameof(ExpectedPlayerId) or nameof(Username) or nameof(Password)) {
            credentialsDirty=true;
            if (vm.CanEditPcrTarget) SaveCredentials();
        }
        else if (e.PropertyName is not (nameof(Status) or nameof(Progress))) SaveCore(automatic:true);
    }
    [RelayCommand] public void ToggleCredentials() => ShowCredentials=!ShowCredentials;
    public void HideCredentials() => ShowCredentials=false;
    private void ClearSdkSession(PcrLabyrinthAccount account) {
        if (string.IsNullOrEmpty(account.Credentials)) return;
        try {
            var bytes=ProtectedData.Unprotect(Convert.FromBase64String(account.Credentials),null,DataProtectionScope.CurrentUser);
            try {
                var data=JObject.Parse(Encoding.UTF8.GetString(bytes));
                if (!long.TryParse((string?)data["expected_player_id"],NumberStyles.None,CultureInfo.InvariantCulture,out var id) || id<=0) return;
                var name=Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(account.Server+"\0"+id.ToString(CultureInfo.InvariantCulture)))).ToLowerInvariant()+".bin";
                File.Delete(Path.Combine(PcrRuntimePaths.ResolveDataRepository(AppContext.BaseDirectory),"data","auth",name));
            } finally { CryptographicOperations.ZeroMemory(bytes); }
        } catch { CredentialsStatus="账号已保存；旧登录缓存清理失败。修改后的凭据不会复用旧令牌。"; }
    }
    private void LoadCredentials(string encrypted) {
        if (string.IsNullOrEmpty(encrypted)) { CredentialsStatus="请填写所选账号的玩家 ID 和登录凭据；保存时会加密。"; return; }
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
            PersistAccount(selectedAccount?.Settings ?? new JObject(),encrypted);
            credentialsDirty=false;
            CredentialsStatus="账号凭据已加密保存；重开软件自动载入。";
        } catch { CredentialsStatus="账号凭据保存失败，原有保存内容已保留；请重试。输入内容仍可用于本次运行。"; }
        OnPropertyChanged(nameof(AccountSummary));
        OnPropertyChanged(nameof(SaveButtonText));
    }
    private static int Number(string s,int min,int max,string label) {
        if (!int.TryParse(s,NumberStyles.None,CultureInfo.InvariantCulture,out var n) || n<min || n>max) throw new InvalidOperationException($"{label}请输入 {min}–{max} 的整数。");
        return n;
    }
    private static double Weight(string text,double max,string label) {
        if (!double.TryParse(text,NumberStyles.AllowDecimalPoint,CultureInfo.InvariantCulture,out var value)
                || !double.IsFinite(value) || value<0 || value>max)
            throw new InvalidOperationException($"{label}请输入 0–{max} 的数值。");
        return value;
    }
    private JObject OpeningRostersSnapshot() {
        var rosters=new JObject();
        foreach (var goal in Goals) {
            var filled=goal.Slots.Where(slot=>slot.Candidates.Count>0).ToArray();
            if (filled.Length==0) continue;
            if (filled.Length!=goal.Slots.Count)
                throw new InvalidOperationException($"{goal.Name}的开局候选需要三个槽位都至少选择一个角色；若沿用上游默认，请把三个槽位都清空。");
            var roles=goal.Slots.SelectMany(slot=>slot.Candidates.Select(character=>character.Id)).ToArray();
            if (roles.Any(id=>!System.Text.RegularExpressions.Regex.IsMatch(id,@"^[0-9]{4,6}$")) || roles.Distinct().Count()!=roles.Length)
                throw new InvalidOperationException($"{goal.Name}的开局候选包含无效或重复的角色 ID。");
            rosters[goal.GuildId.ToString(CultureInfo.InvariantCulture)]=new JArray(goal.Slots.Select(slot=>new JArray(slot.Candidates.Select(character=>character.Id))));
        }
        return rosters;
    }
    private JArray RelicPrioritySnapshot() {
        if (string.IsNullOrWhiteSpace(RelicMarkPriority)) return new JArray();
        var marks=RelicMarkPriority.Split(',',StringSplitOptions.TrimEntries).Select(mark=>mark switch { "暴击"=>"会心", "削弱" or "崩弱"=>"弱体", _=>mark }).ToArray();
        if (marks.Distinct().Count()!=marks.Length || marks.Any(mark=>!PcrMarkPriorityList.Marks.Contains(mark)))
            throw new InvalidOperationException("遗物印记优先级请填写不重复的加速、会心、守备、强化、弱体，用逗号分隔。");
        return new JArray(marks);
    }
    // A stored ordered subset was expressible by the old text box, but the block list
    // only renders all five marks. Keep the stored relative order, append the missing
    // marks in built-in order, and migrate the account once with a visible notice.
    private string NormalizeRelicPriority(JArray? priority) {
        var stored=PcrMarkPriorityList.Normalize(priority?.Values<string>());
        if (stored.Count==0) return "";
        if (stored.Count>=PcrMarkPriorityList.Marks.Count) return string.Join(",",stored);
        var expanded=PcrMarkPriorityList.Expand(stored);
        relicPriorityMigrated=true;
        RelicPriorityNotice=$"检测到旧的子集优先级（{string.Join("，",stored)}），已补全为五项：{string.Join("，",expanded)}。";
        return string.Join(",",expanded);
    }
    private void FlushRelicPriorityMigration() {
        if (!relicPriorityMigrated || !ready || !vm.CanEditPcrTarget) return;
        SaveCore(automatic:true);
    }
    partial void OnRelicMarkPriorityChanged(string value) {
        if (ready) RelicPriorityNotice="";
    }
    private JObject EventChoiceOverridesSnapshot() {
        var result=(JObject)savedEventChoiceOverrides.DeepClone();
        foreach (var item in EventChoiceItems) {
            if (item.SelectedChoiceId is int choiceId) result[item.EventId]=choiceId;
            else result.Remove(item.EventId);
        }
        return result;
    }
    private JObject StrategySnapshot() {
        if (!SpecialEquipmentModes.Contains(SpecialEquipmentMode)) throw new InvalidOperationException("特别装备模式无效。");
        if (!EventChainPolicies.Contains(EventChainPolicy)) throw new InvalidOperationException("连续事件策略无效。");
        var equipmentBattles=new JArray();
        if (SpecialEquipmentNormal) equipmentBattles.Add("NORMAL");
        if (SpecialEquipmentEx) equipmentBattles.Add("EX");
        if (SpecialEquipmentBoss) equipmentBattles.Add("BOSS");
        if (SpecialEquipmentMode==SpecialEquipmentModes[1] && equipmentBattles.Count==0) throw new InvalidOperationException("请至少选择一种启用特别装备的战斗。");
        if (!ResponseIntervals.Contains(ResponseIntervalMs)) throw new InvalidOperationException("响应间隔请选择 300 或 500 毫秒。");
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
        var strategy=new JObject { ["boss_mode"]=BossMode==BossModes[0] ? "multi":"single",
            ["special_equipment_mode"]=SpecialEquipmentMode==SpecialEquipmentModes[1] ? "auto_equip" : "off",
            ["special_equipment_battles"]=equipmentBattles,["response_interval_ms"]=ResponseIntervalMs,
            ["auto_database_update"]=AutoDatabaseUpdate,
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
            ["event_chain_policy"]=EventChainPolicy==EventChainPolicies[1] ? "continue" : EventChainPolicy==EventChainPolicies[2] ? "stop" : "auto",
            ["event_choice_overrides"]=EventChoiceOverridesSnapshot(),
            ["opening_rosters"]=OpeningRostersSnapshot(),
            ["deployment_weights"]=new JArray(deployment),["scoring_overrides"]=scores,
            ["personal_scores"]=personalScores.DeepClone() };
        if (TryCharacterCatalog() is { } catalog) {
            var units=new JObject();
            foreach (var guild in catalog.Guilds)
                units[guild.GuildId.ToString(CultureInfo.InvariantCulture)]=new JArray(guild.Characters.Select(character=>character.Id));
            strategy["guild_units"]=units;
        }
        return strategy;
    }
    public JObject Snapshot() {
        if (accountLibrary is null || selectedAccount is null) throw new InvalidOperationException("账号库尚未成功载入，原内容不会被覆盖。");
        if (!Servers.Any(item=>item.Id==Server)) throw new InvalidOperationException("未知服务器，请重新选择服务器并填写凭据。");
        if (Goals.Any(g=>!g.TargetModes.Contains(g.TargetMode))) throw new InvalidOperationException("目标计数方式无效。");
        var goals=new JArray(Goals.Where(g=>g.Enabled).Select(g=>new JObject { ["guild_id"]=g.GuildId,["clears"]=Number(g.Clears,1,999,g.Name+"目标次数"),["mode"]=g.TargetMode==g.TargetModes[0] ? "clears":"attempts" }));
        if (goals.Count==0) throw new InvalidOperationException("请至少选择一个目标公会。");
        if (!RouteModes.Contains(RouteMode) || !ThirdChoices.Contains(ThirdChoice)) throw new InvalidOperationException("路线设置无效。");
        if (bossCatalog is null) throw new InvalidOperationException("Boss 目录加载失败，无法保存或开始："+bossCatalogError);
        return new JObject { ["version"]=1,["goals"]=goals,["difficulty"]=Number(Difficulty,1,5,"难度"),["reroll"]=Reroll,
            ["max_attempts"]=Number(MaxAttempts,1,100,"刷取次数"),["max_seconds"]=loadedMaxSeconds is >=1 and <=1800 && MaxMinutes==loadedMaxMinutes ? loadedMaxSeconds.Value:Number(MaxMinutes,1,30,"刷取分钟")*60,
            ["buy_relics"]=BuyRelics,["refresh_shop"]=RefreshShop,["strategy"]=StrategySnapshot(),["route"]=new JObject {
                ["mode"]=RouteMode==RouteModes[0] ? "value":"template",["perfect_start"]=PerfectStart,["allowance"]=Number(Allowance,0,3,"价值容差"),
                ["third_choice"]=ThirdChoice==ThirdChoices[0] ? "relic":ThirdChoice==ThirdChoices[1] ? "event":"either",
                ["area3_bosses"]=new JArray(bossCatalog.SelectedIds(3,Area3Bosses)),
                ["area5_bosses"]=new JArray(bossCatalog.SelectedIds(5,Area5Bosses)) } };
    }
    [RelayCommand] public void Save() => SaveCore(automatic:false);
    private void SaveCore(bool automatic) {
        if (!ready || !vm.CanEditPcrTarget) return;
        // A successful save writes the current five-mark order, so a pending one-time
        // subset migration no longer needs its own flush.
        relicPriorityMigrated=false;
        vm.NotifyPcrSelectionChanged();
        SaveCredentials();
        try {
            var data=Snapshot();
            data["keep_old_run"]=KeepOldRun;
            data["draft_goals"]=new JArray(Goals.Select(g=>new JObject { ["guild_id"]=g.GuildId,["enabled"]=g.Enabled,["clears"]=g.Clears,["mode"]=g.TargetMode==g.TargetModes[0] ? "clears":"attempts" }));
            var value=data.ToString(Formatting.None);
            if (value==saved && selectedAccount?.Name==AccountName.Trim() && selectedAccount?.Server==Server) { Status=credentialsDirty ? "任务设置已保存；账号尚未保存，请重试。" : "设置已保存，没有待保存的任务修改。"; return; }
            PersistAccount(data,selectedAccount?.Credentials ?? "");
            if (InstanceConfiguration.ReadValueFromFile(vm.Processor.InstanceId,Key)!=value) throw new InvalidOperationException("设置未写入磁盘，请重新保存。");
            saved=value; Status=credentialsDirty ? "任务设置已保存；账号尚未保存，请重试。"
                : automatic ? "已自动保存黎明界设置。" : "黎明界设置已保存。";
        } catch (Exception ex) { Status="保存失败："+ex.Message; }
    }
}
