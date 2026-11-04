using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using CommunityToolkit.Mvvm.ComponentModel;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// 事件目录只读打包资源：22 个事件、41 个选项。类型由事件 ID 固定映射，
// 用于把同名事件（招募、斗技场）区分开；加载失败由设置页转成可见提示。
public sealed class PcrLabyrinthEventChoice {
    public PcrLabyrinthEventChoice(string id, string name, string description) {
        Id=id; Name=name; Description=description;
    }
    public string Id { get; }
    public string Name { get; }
    public string Description { get; }
}

public sealed class PcrLabyrinthEventInfo {
    public PcrLabyrinthEventInfo(string id, string name, int area, string typeName, IReadOnlyList<PcrLabyrinthEventChoice> choices) {
        Id=id; Name=name; Area=area; TypeName=typeName; Choices=choices;
    }
    public string Id { get; }
    public string Name { get; }
    public int Area { get; }
    public string TypeName { get; }
    public IReadOnlyList<PcrLabyrinthEventChoice> Choices { get; }
    public bool Configurable => Choices.Count>1;
    public string Detail => $"区域 {Area} · {TypeName} · ID {Id}";
}

public sealed class PcrLabyrinthEventCatalogData {
    internal PcrLabyrinthEventCatalogData(IReadOnlyList<PcrLabyrinthEventInfo> events) { Events=events; }
    public IReadOnlyList<PcrLabyrinthEventInfo> Events { get; }
}

public static class PcrLabyrinthEventCatalog {
    private const string ResourcePath = "assets/labyrinth/event-recognition.json";
    private static readonly object Gate = new();
    private static string? repositoryRootOverride;
    public static void UseRepositoryRoot(string repositoryRoot) {
        lock (Gate) repositoryRootOverride=Path.GetFullPath(repositoryRoot);
    }
    private static string ResolveRepositoryRoot() =>
        repositoryRootOverride ?? PcrRuntimePaths.ResolveDataRepository(AppContext.BaseDirectory);
    public static PcrLabyrinthEventCatalogData Load() => LoadFrom(ResolveRepositoryRoot());
    public static PcrLabyrinthEventCatalogData LoadFrom(string repositoryRoot) {
        var path=Path.Combine(Path.GetFullPath(repositoryRoot), ResourcePath.Replace('/', Path.DirectorySeparatorChar));
        if (!File.Exists(path)) throw new InvalidOperationException($"缺少事件目录：{path}");
        var root=JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
        if (root["schemaVersion"]?.Type!=JTokenType.Integer || (int)root["schemaVersion"]! != 1)
            throw new InvalidOperationException("事件目录格式无效。");
        if (!(root["events"] is JArray rows)) throw new InvalidOperationException("事件目录缺少 events。");
        var events=new List<PcrLabyrinthEventInfo>();
        foreach (var row in rows.OfType<JObject>()) {
            var id=(string?)row["id"];
            var name=(string?)row["name"];
            if (string.IsNullOrWhiteSpace(id) || string.IsNullOrWhiteSpace(name) || row["area"]?.Type!=JTokenType.Integer)
                throw new InvalidOperationException("事件目录存在无效事件。");
            if (!(row["choices"] is JArray choiceRows) || choiceRows.Count==0)
                throw new InvalidOperationException($"事件 {id} 缺少选项。");
            var choices=new List<PcrLabyrinthEventChoice>();
            foreach (var item in choiceRows.OfType<JObject>()) {
                var choiceId=(string?)item["id"];
                var choiceName=(string?)item["name"];
                if (string.IsNullOrWhiteSpace(choiceId) || string.IsNullOrWhiteSpace(choiceName))
                    throw new InvalidOperationException($"事件 {id} 存在无效选项。");
                choices.Add(new PcrLabyrinthEventChoice(choiceId, choiceName, (string?)item["description"] ?? ""));
            }
            events.Add(new PcrLabyrinthEventInfo(id!, name!, (int)row["area"]!, TypeNameFor(id!), choices));
        }
        if (events.Count==0 || events.Select(item=>item.Id).Distinct().Count()!=events.Count)
            throw new InvalidOperationException("事件目录为空或事件 ID 重复。");
        return new PcrLabyrinthEventCatalogData(
            events.OrderBy(item=>item.Area).ThenBy(item=>item.Id, StringComparer.Ordinal).ToArray());
    }
    private static string TypeNameFor(string id) => id switch {
        "1101" or "1102" or "1103" or "1104" => "职阶招募",
        "1105" or "1109" or "1113" or "1117" => "战斗挑战",
        "1107" => "石板",
        "1108" => "地下城岔路",
        "1110" => "猜拳",
        "1111" => "箱子",
        "1112" => "活动会场",
        "1314" or "1315" or "1316" => "连续事件入口",
        "2314" or "3314" => "钓鱼连续事件",
        "2315" or "3315" => "料理连续事件",
        "2316" or "3316" => "再较量连续事件",
        _ => "其他"
    };
}

// 单个事件的选项配置：默认“自动（按价值模型）”，可指定该事件的具体选项。
// 只有多选项事件会创建该配置项；单选事件不显示。修改后自动保存为
// strategy.event_choice_overrides。
public partial class PcrLabyrinthEventSetting : ObservableObject {
    public const string AutoLabel = "自动（按价值模型）";
    private readonly PcrLabyrinthSettings owner;
    private readonly IReadOnlyList<PcrLabyrinthEventChoice> choices;
    public PcrLabyrinthEventSetting(PcrLabyrinthEventInfo info, PcrLabyrinthSettings owner) {
        EventId=info.Id; Title=info.Name; Detail=info.Detail;
        this.owner=owner; choices=info.Choices;
        Choices=new[] { AutoLabel }.Concat(info.Choices.Select(choice=>choice.Name)).ToArray();
        selected=Choices[0];
    }
    public string EventId { get; }
    public string Title { get; }
    public string Detail { get; }
    public string[] Choices { get; }
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(Hint))]
    private string selected;
    public string Hint {
        get {
            var choice=SelectedChoice;
            return choice is null ? "未单独指定，按价值模型自动选择。" : choice.Description;
        }
    }
    public int? SelectedChoiceId {
        get {
            if (SelectedChoice is not { } choice) return null;
            return int.TryParse(choice.Id, out var id) ? id : null;
        }
    }
    private PcrLabyrinthEventChoice? SelectedChoice =>
        choices.FirstOrDefault(choice=>choice.Name==Selected);
    internal void LoadSelection(int? choiceId) {
        var choice=choiceId is null ? null : choices.FirstOrDefault(item=>item.Id==choiceId.Value.ToString());
        Selected=choice?.Name ?? AutoLabel;
    }
    partial void OnSelectedChanged(string value) => owner.EventChoiceEdited();
}
