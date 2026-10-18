using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using Avalonia.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// 开局「角色选择」只列出所选公会的成员；名称与头像来自打包的图鉴资源，
// 公会归属来自 GUI 内嵌的 labyrinth-guilds.json（见该文件 reviewRequired）。
public sealed partial class PcrLabyrinthCharacter : ObservableObject {
    private bool iconLoaded;
    private Bitmap? icon;
    public PcrLabyrinthCharacter(string id, string name, string searchText, string? iconPath, bool placeholder = false) {
        Id=id; Name=name; SearchText=searchText; IconPath=iconPath; IsPlaceholder=placeholder;
    }
    public string Id { get; }
    public string Name { get; }
    public string SearchText { get; }
    public string? IconPath { get; }
    public bool IsPlaceholder { get; }
    // 图标在可见行首次绑定时解码；WebP/缺失文件不允许让整个面板失败。
    public Bitmap? Icon {
        get {
            if (!iconLoaded) {
                iconLoaded=true;
                if (!string.IsNullOrEmpty(IconPath))
                    try { icon=new Bitmap(IconPath); } catch { icon=null; }
            }
            return icon;
        }
    }
    [ObservableProperty] private bool isSelected;
    [ObservableProperty] private string badge = "";
    public IRelayCommand? ToggleCommand { get; set; }
    // 优先 6 星、3 星、1 星头像；同星级优先 png，WebP 只在没有 png 时使用。
    internal static string? IconPathFor(string characterPack, Dictionary<string, string[]> iconsById, string id) {
        if (!iconsById.TryGetValue(id, out var variants) || variants.Length==0) return null;
        return Path.Combine(characterPack, variants[0].Replace('/', Path.DirectorySeparatorChar));
    }
}

public sealed class PcrLabyrinthGuildRoster {
    internal PcrLabyrinthGuildRoster(int guildId, string name, IReadOnlyList<PcrLabyrinthCharacter> characters) {
        GuildId=guildId; Name=name; Characters=characters;
    }
    public int GuildId { get; }
    public string Name { get; }
    public IReadOnlyList<PcrLabyrinthCharacter> Characters { get; }
}

// 只读取打包资源；加载失败由调用方转成可见提示，不参与任务启动资格。
public sealed class PcrLabyrinthCharacterCatalog {
    private const string GuildResourceName = "MFAAvalonia.Pcr.LabyrinthGuilds.json";
    private const string CharacterPack = "assets/labyrinth/character-pack";
    private static readonly object Gate = new();
    private static string? repositoryRootOverride;
    private PcrLabyrinthCharacterCatalog(IReadOnlyList<PcrLabyrinthGuildRoster> guilds) { Guilds=guilds; }
    public IReadOnlyList<PcrLabyrinthGuildRoster> Guilds { get; }
    public PcrLabyrinthGuildRoster ForGuild(int guildId) =>
        Guilds.FirstOrDefault(guild=>guild.GuildId==guildId) ?? throw new InvalidOperationException($"缺少公会 {guildId} 的开局名单。");
    // 测试或独立宿主可显式指向数据目录；正常运行时沿用 pcr-repository.txt。
    public static void UseRepositoryRoot(string repositoryRoot) {
        lock (Gate) repositoryRootOverride=Path.GetFullPath(repositoryRoot);
    }
    private static string ResolveRepositoryRoot() =>
        repositoryRootOverride ?? PcrRuntimePaths.ResolveDataRepository(AppContext.BaseDirectory);
    // 每次创建独立目录对象：ToggleCommand 绑定到当前设置实例，多个存档页互不影响。
    public static PcrLabyrinthCharacterCatalog Load() => LoadFrom(ResolveRepositoryRoot());
    public static PcrLabyrinthCharacterCatalog LoadFrom(string repositoryRoot) {
        var pack=Path.Combine(Path.GetFullPath(repositoryRoot), CharacterPack.Replace('/', Path.DirectorySeparatorChar));
        var catalog=ReadObject(Path.Combine(pack, "characters.landosolroster.json"), "角色图鉴");
        var iconIndex=ReadIconIndex(ReadObject(Path.Combine(pack, "icons.json"), "角色头像索引"));
        var guilds=ReadEmbeddedObject(GuildResourceName, "公会开局名单");
        var byId=(catalog["characters"] as JArray ?? throw new InvalidOperationException("角色图鉴缺少 characters。"))
            .OfType<JObject>()
            .Where(row=>(string?)row["id"] is not null)
            .ToDictionary(row=>(string)row["id"]!, row=>row, StringComparer.Ordinal);
        var result=new List<PcrLabyrinthGuildRoster>();
        foreach (var guild in (guilds["guilds"] as JArray ?? throw new InvalidOperationException("公会开局名单缺少 guilds。")).OfType<JObject>()) {
            if (guild["id"]?.Type!=JTokenType.Integer || (string?)guild["name"] is not { Length: >0 } name)
                throw new InvalidOperationException("公会开局名单格式无效。");
            var characters=new List<PcrLabyrinthCharacter>();
            foreach (var person in (guild["persons"] as JArray ?? throw new InvalidOperationException($"{name} 缺少成员名单。")).OfType<JObject>())
                foreach (var token in person["units"] as JArray ?? throw new InvalidOperationException($"{name} 缺少角色 ID。")) {
                    var id=token.Value<string>() ?? "";
                    if (!byId.TryGetValue(id, out var row)) throw new InvalidOperationException($"角色图鉴缺少 ID {id}（{name}）。");
                    var display=(string?)row["officialName"];
                    if (string.IsNullOrWhiteSpace(display)) display=(string?)row["name"];
                    if (string.IsNullOrWhiteSpace(display)) display=id;
                    characters.Add(new PcrLabyrinthCharacter(
                        id, display!, SearchTextFor(id, row, display!), PcrLabyrinthCharacter.IconPathFor(pack, iconIndex, id)));
                }
            result.Add(new PcrLabyrinthGuildRoster((int)guild["id"]!, name, characters));
        }
        if (result.Count==0 || result.Any(guild=>guild.Characters.Count==0))
            throw new InvalidOperationException("公会开局名单为空。");
        var duplicate=result.SelectMany(guild=>guild.Characters).GroupBy(character=>character.Id).FirstOrDefault(group=>group.Count()>1);
        if (duplicate is not null) throw new InvalidOperationException($"角色 {duplicate.Key} 同时出现在多个公会名单中。");
        return new PcrLabyrinthCharacterCatalog(result);
    }
    private static Dictionary<string, string[]> ReadIconIndex(JObject icons) {
        var result=new Dictionary<string, string[]>(StringComparer.Ordinal);
        if (!(icons["characterIcons"] is JArray rows)) return result;
        var grouped=rows.OfType<JObject>()
            .Select(row=>new {
                Owner=(string?)row["ownerId"],
                Variant=(string?)row["variant"] ?? "",
                File=((string?)row["file"])?.Replace('\\', '/'),
                Png=(string?)row["format"]=="png" })
            .Where(row=>!string.IsNullOrEmpty(row.Owner) && !string.IsNullOrEmpty(row.File))
            .GroupBy(row=>row.Owner!);
        foreach (var group in grouped) {
            var ordered=group
                .OrderBy(row=>row.Png ? 0 : 1)
                .ThenBy(row=>row.Variant switch { "61"=>0, "31"=>1, "11"=>2, _=>3 })
                .Select(row=>row.File!)
                .ToArray();
            result[group.Key]=ordered;
        }
        return result;
    }
    private static string SearchTextFor(string id, JObject row, string display) {
        var text=new StringBuilder(id).Append(' ').Append(display);
        foreach (var key in new[] { "name", "officialName" })
            if (row[key]?.Type==JTokenType.String) text.Append(' ').Append((string)row[key]!);
        foreach (var alias in (row["aliases"] as JArray ?? new JArray()).OfType<JValue>())
            if (alias.Type==JTokenType.String) text.Append(' ').Append((string)alias!);
        return Normalize(text.ToString());
    }
    internal static string Normalize(string value) {
        var builder=new StringBuilder(value.Length);
        foreach (var ch in value.ToLowerInvariant())
            if (!char.IsWhiteSpace(ch)) builder.Append(ch);
        return builder.ToString();
    }
    private static JObject ReadObject(string path, string label) {
        if (!File.Exists(path)) throw new InvalidOperationException($"缺少{label}：{path}");
        return JObject.Parse(File.ReadAllText(path, Encoding.UTF8));
    }
    private static JObject ReadEmbeddedObject(string name, string label) {
        using var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream(name)
            ?? throw new InvalidOperationException($"缺少{label}资源。");
        using var reader=new StreamReader(stream, Encoding.UTF8);
        return JObject.Parse(reader.ReadToEnd());
    }
}
