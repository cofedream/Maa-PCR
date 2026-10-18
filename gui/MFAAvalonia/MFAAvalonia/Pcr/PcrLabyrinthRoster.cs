using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.RegularExpressions;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// 一个公会的「开局三人候选」：公会名 + 三个槽位，候选行直接列出该公会可选角色。
// 点候选按空槽位顺序加入，再点一次移除；三个槽位都空时沿用上游默认。
// 序列化格式保持 "a,b / c / d"，旧的多候选优先级列表仍可读取和移除。
public partial class PcrLabyrinthGoal : ObservableObject {
    private readonly PcrLabyrinthSettings owner;
    private IReadOnlyList<PcrLabyrinthCharacter>? pool;
    public PcrLabyrinthGoal(int id, string name, PcrLabyrinthSettings owner) {
        GuildId=id; Name=name; this.owner=owner;
        Slots=new[] { new PcrLabyrinthSlot(1,this), new PcrLabyrinthSlot(2,this), new PcrLabyrinthSlot(3,this) };
    }
    public int GuildId { get; }
    public string Name { get; }
    public IReadOnlyList<PcrLabyrinthSlot> Slots { get; }
    // 第一次访问时读取图鉴并同步已选高亮；失败时返回空列表，不阻塞设置页。
    public IReadOnlyList<PcrLabyrinthCharacter> Pool {
        get {
            if (pool is null) { pool=owner.PoolForGuild(this); RefreshPool(); }
            return pool;
        }
    }
    [ObservableProperty] private bool enabled;
    [ObservableProperty] private string clears = "1";
    public string[] TargetModes { get; } = { "通关次数", "尝试次数" };
    [ObservableProperty] private string targetMode = "通关次数";
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasRosterWarning))]
    private string rosterWarning = "";
    public bool HasRosterWarning => RosterWarning.Length>0;
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasRosterHint))]
    private string rosterHint = "";
    public bool HasRosterHint => RosterHint.Length>0;
    public bool HasCustomRoster => Slots.Any(slot=>!slot.IsEmpty);
    public void Toggle(PcrLabyrinthCharacter character) {
        var selected=Slots.FirstOrDefault(slot=>slot.Contains(character));
        if (selected is not null) {
            selected.Remove(character);
        } else {
            if (character.IsPlaceholder) return;
            var empty=Slots.FirstOrDefault(slot=>slot.IsEmpty);
            if (empty is null) { RosterHint="三个槽位都已选满；点击上方已选角色可移除。" ; return; }
            empty.Add(character);
        }
        RosterHint="";
        RefreshPool();
        RosterEdited();
    }
    internal void RefreshPool() {
        if (pool is null) return;
        for (var index=0; index<Slots.Count; index++)
            foreach (var character in Slots[index].Candidates) {
                character.IsSelected=true;
                character.Badge=(index+1).ToString();
            }
        var selected=Slots.SelectMany(slot=>slot.Candidates).ToHashSet();
        foreach (var character in pool)
            if (!selected.Contains(character)) { character.IsSelected=false; character.Badge=""; }
    }
    internal void LoadOpeningRoster(JArray rows) {
        foreach (var slot in Slots) slot.ClearLoaded();
        try {
            if (rows.Count!=3) throw new InvalidOperationException();
            var roster=owner.TryCharacterCatalog()?.ForGuild(GuildId);
            var seen=new HashSet<string>(StringComparer.Ordinal);
            for (var index=0; index<3; index++) {
                if (rows[index] is not JArray row || row.Count==0) throw new InvalidOperationException();
                foreach (var token in row) {
                    var id=token.Value<string>() ?? "";
                    if (!Regex.IsMatch(id, @"^[0-9]{4,6}$") || !seen.Add(id)) throw new InvalidOperationException();
                    var character=roster?.Characters.FirstOrDefault(item=>item.Id==id) ?? owner.PlaceholderCharacter(id);
                    character.ToggleCommand=new RelayCommand(()=>Toggle(character));
                    Slots[index].Load(character);
                }
            }
        } catch {
            foreach (var slot in Slots) slot.ClearLoaded();
            RosterWarning="保存的开局候选无法读取，请重新选择。";
        }
        foreach (var slot in Slots) slot.Refresh();
        RefreshPool();
    }
    internal void RosterEdited() {
        RosterWarning="";
        owner.RosterEdited();
    }
}

public sealed partial class PcrLabyrinthSlot : ObservableObject {
    private readonly PcrLabyrinthGoal goal;
    public PcrLabyrinthSlot(int index, PcrLabyrinthGoal goal) { Index=index; this.goal=goal; }
    public int Index { get; }
    public PcrLabyrinthGoal Goal => goal;
    public string Title => $"槽位 {Index}";
    public ObservableCollection<PcrLabyrinthCharacter> Candidates { get; } = new();
    public bool IsEmpty => Candidates.Count==0;
    public string HeaderText => Candidates.Count==0
        ? "未选"
        : string.Join(" → ", Candidates.Select(character=>character.Name));
    public bool Contains(PcrLabyrinthCharacter character) => Candidates.Contains(character);
    internal void Add(PcrLabyrinthCharacter character) {
        if (Contains(character)) return;
        Candidates.Add(character);
        Refresh();
    }
    internal void Remove(PcrLabyrinthCharacter character) {
        if (!Candidates.Remove(character)) return;
        Refresh();
    }
    internal void Load(PcrLabyrinthCharacter character) => Candidates.Add(character);
    internal void ClearLoaded() => Candidates.Clear();
    internal void Refresh() {
        OnPropertyChanged(nameof(IsEmpty));
        OnPropertyChanged(nameof(HeaderText));
        goal.RefreshPool();
    }
}
