using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Avalonia.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

/// <summary>一条可筛选的区域首领。名称与 ID 取自 assets/labyrinth/labyrinth-bosses.json，
/// 该文件同时记录图标路径；图标来源与摘要见 assets/labyrinth/boss-pack/PROVENANCE.json。
/// ordinal 是该首领在区域轮转中的位置：协议 quest_id 的序号按 (ordinal-1)%5 映射到本目录，
/// 因此每区域五个 ordinal 1..5 必须齐全；Python 侧 routes.BOSSES 顺序即此顺序。</summary>
public sealed class PcrLabyrinthBossInfo {
    internal PcrLabyrinthBossInfo(int area,int id,int ordinal,string name,string icon) {
        Area=area; Id=id; Ordinal=ordinal; Name=name; IconPath=icon;
    }
    public int Area { get; }
    public int Id { get; }
    public int Ordinal { get; }
    public string Name { get; }
    /// <summary>图标绝对路径（按打开目录解析），供列表懒加载。</summary>
    public string IconPath { get; }
}

/// <summary>Boss 筛选目录：只读取打包资源 assets/labyrinth/labyrinth-bosses.json。
/// 与 PcrLabyrinthEventCatalog/PcrLabyrinthCharacterCatalog 一样，加载失败由设置页转成可见错误，
/// 并让 Snapshot 拒绝生成配置：宁可本次不能开始，也不能静默把筛选变成“不限制”。
/// 保存契约不变：route.area3_bosses / route.area5_bosses 仍是 ID 数组。</summary>
public sealed class PcrLabyrinthBossCatalog {
    private const string ResourcePath = "assets/labyrinth/labyrinth-bosses.json";
    private const string IconRoot = "assets/labyrinth";
    private static readonly Regex IconName = new(@"^boss-pack/icons/([0-9]{6})\.png$", RegexOptions.CultureInvariant);
    private static readonly object Gate = new();
    private static string? repositoryRootOverride;
    private readonly IReadOnlyDictionary<int, IReadOnlyList<PcrLabyrinthBossInfo>> areas;
    private readonly IReadOnlyDictionary<int, int[]> defaults;
    private PcrLabyrinthBossCatalog(IReadOnlyDictionary<int, IReadOnlyList<PcrLabyrinthBossInfo>> areas,
        IReadOnlyDictionary<int, int[]> defaults) {
        this.areas=areas; this.defaults=defaults;
    }
    /// <summary>测试或独立宿主可显式指向数据目录；正常运行时沿用 pcr-repository.txt。</summary>
    public static void UseRepositoryRoot(string repositoryRoot) {
        lock (Gate) repositoryRootOverride=Path.GetFullPath(repositoryRoot);
    }
    private static string ResolveRepositoryRoot() =>
        repositoryRootOverride ?? PcrRuntimePaths.ResolveDataRepository(AppContext.BaseDirectory);
    public static PcrLabyrinthBossCatalog Load() => LoadFrom(ResolveRepositoryRoot());
    public static PcrLabyrinthBossCatalog LoadFrom(string repositoryRoot) {
        var root=Path.GetFullPath(repositoryRoot);
        var path=Path.Combine(root, ResourcePath.Replace('/', Path.DirectorySeparatorChar));
        if (!File.Exists(path)) throw new InvalidOperationException($"缺少 Boss 目录：{path}");
        var data=JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
        if (data["schemaVersion"]?.Type!=JTokenType.Integer || (int)data["schemaVersion"]! != 1)
            throw new InvalidOperationException("Boss 目录格式无效。");
        if (!(data["bosses"] is JArray rows) || rows.Count==0)
            throw new InvalidOperationException("Boss 目录缺少 bosses。");
        var iconRoot=Path.Combine(root, IconRoot.Replace('/', Path.DirectorySeparatorChar));
        var grouped=new Dictionary<int, List<PcrLabyrinthBossInfo>>();
        var seen=new HashSet<int>();
        foreach (var row in rows.OfType<JObject>()) {
            if (row["id"]?.Type!=JTokenType.Integer || row["area"]?.Type!=JTokenType.Integer
                || row["ordinal"]?.Type!=JTokenType.Integer || row["name"]?.Type!=JTokenType.String
                || row["icon"]?.Type!=JTokenType.String)
                throw new InvalidOperationException("Boss 目录存在无效条目。");
            var id=(int)row["id"]!; var area=(int)row["area"]!; var ordinal=(int)row["ordinal"]!;
            var name=(string)row["name"]!; var icon=(string)row["icon"]!;
            if (id<=0 || area is not (3 or 5) || ordinal is < 1 or > 5 || string.IsNullOrWhiteSpace(name))
                throw new InvalidOperationException($"Boss {id} 的区域/序号/名称无效。");
            var match=IconName.Match(icon);
            if (!match.Success || match.Groups[1].Value!=id.ToString(System.Globalization.CultureInfo.InvariantCulture))
                throw new InvalidOperationException($"Boss {id} 的图标路径无效：{icon}");
            if (!seen.Add(id)) throw new InvalidOperationException($"Boss ID 重复：{id}");
            var iconPath=Path.Combine(iconRoot, icon.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(iconPath)) throw new InvalidOperationException($"Boss 图标缺失：{icon}");
            if (!grouped.TryGetValue(area, out var list)) grouped[area]=list=new List<PcrLabyrinthBossInfo>();
            list.Add(new PcrLabyrinthBossInfo(area,id,ordinal,name,iconPath));
        }
        if (grouped.Count!=2) throw new InvalidOperationException("Boss 目录必须同时包含第三、第五区域。");
        var ordered=new Dictionary<int, IReadOnlyList<PcrLabyrinthBossInfo>>();
        foreach (var (area,list) in grouped) {
            var ordinals=list.Select(row=>row.Ordinal).OrderBy(value=>value).ToArray();
            if (ordinals.Length!=5 || !ordinals.SequenceEqual(Enumerable.Range(1,5)))
                throw new InvalidOperationException($"区域 {area} 的 Boss 轮转序号不是 1..5 各一条。");
            ordered[area]=list.OrderBy(row=>row.Ordinal).ToArray();
        }
        if (!(data["defaultSelection"] is JObject selection) || selection.Count!=2)
            throw new InvalidOperationException("Boss 目录缺少 defaultSelection。");
        var defaultIds=new Dictionary<int, int[]>();
        foreach (var area in new[]{3,5}) {
            if (selection[area.ToString(System.Globalization.CultureInfo.InvariantCulture)] is not JArray values
                || values.Any(value=>value.Type!=JTokenType.Integer))
                throw new InvalidOperationException($"区域 {area} 的默认勾选格式无效。");
            var ids=values.Select(value=>value.Value<int>()).ToArray();
            if (ids.Distinct().Count()!=ids.Length || ids.Any(id=>ordered[area].All(row=>row.Id!=id)))
                throw new InvalidOperationException($"区域 {area} 的默认勾选不在 Boss 目录内。");
            defaultIds[area]=ids;
        }
        return new PcrLabyrinthBossCatalog(ordered, defaultIds);
    }
    public IReadOnlyList<PcrLabyrinthBossInfo> Rows(int area) =>
        areas.TryGetValue(area, out var rows) ? rows : Array.Empty<PcrLabyrinthBossInfo>();
    public int[] DefaultIds(int area) =>
        defaults.TryGetValue(area, out var ids) ? ids.ToArray() : Array.Empty<int>();
    public List<PcrLabyrinthBossChoice> Choices(int area, PcrLabyrinthSettings owner) =>
        Rows(area).Select(row=>new PcrLabyrinthBossChoice(row, owner)).ToList();
    public int[] SelectedIds(int area, IEnumerable<PcrLabyrinthBossChoice> choices) {
        var selected=choices.Where(choice=>choice.Selected).Select(choice=>choice.Id).ToHashSet();
        return Rows(area).Select(row=>row.Id).Where(selected.Contains).ToArray();
    }
}

public partial class PcrLabyrinthBossChoice : ObservableObject {
    private readonly PcrLabyrinthSettings owner;
    private Bitmap? icon;
    private bool iconLoaded;
    internal PcrLabyrinthBossChoice(PcrLabyrinthBossInfo info, PcrLabyrinthSettings owner) {
        Area=info.Area; Id=info.Id; Name=info.Name; IconPath=info.IconPath; this.owner=owner;
    }
    public int Area { get; }
    public int Id { get; }
    public string Name { get; }
    public string IconPath { get; }
    /// <summary>图标只在首次绑定时解码；文件损坏或缺失时静默退化为纯文字，不阻断勾选。</summary>
    public Bitmap? Icon {
        get {
            if (!iconLoaded) {
                iconLoaded=true;
                try { icon=new Bitmap(IconPath); } catch { icon=null; }
            }
            return icon;
        }
    }
    public string Label => $"{Name}（{Id}）";
    [ObservableProperty] private bool selected;
    partial void OnSelectedChanged(bool value) => owner.BossSelectionEdited();
}
