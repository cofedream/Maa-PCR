using System;
using System.IO;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.Pcr;

// A local continuity marker for the currently supported single MuMu ADB target.
// Creation time is NOT a globally unique clone/import identity. No Extras values
// are synthesized, and this marker never chooses a different ledger directory.
internal static class PcrMuMuIdentity
{
    internal static string InstallationFor(JObject connection)
    {
        var adb = Path.GetFullPath((string?)connection["adb_path"] ?? "").Replace('\\', '/');
        const string suffix = "/nx_device/12.0/shell/adb.exe";
        if (!adb.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("schedule_manager_installation_mapping_invalid");
        return adb[..^suffix.Length];
    }

    internal static bool HasManagerSource(JObject connection)
    {
        var adb = (string?)connection["adb_path"];
        return adb != null && adb.Replace('\\', '/').EndsWith("/nx_device/12.0/shell/adb.exe", StringComparison.OrdinalIgnoreCase)
            && ((string?)connection["serial"])?.StartsWith("127.0.0.1:", StringComparison.Ordinal) == true;
    }

    internal static string ManagerFor(JObject connection)
    {
        var manager = Path.GetFullPath(Path.Combine(InstallationFor(connection), "nx_main", "MuMuManager.exe"));
        if (!File.Exists(manager)) throw new InvalidOperationException("schedule_manager_missing_in_selected_installation");
        return manager;
    }

    private static JObject Read(string text)
    {
        if (text.Length > 65536) throw new InvalidOperationException("schedule_manager_response_too_large");
        try { return JObject.Parse(text, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error }); }
        catch (Exception ex) { throw new InvalidOperationException("schedule_manager_response_invalid_or_duplicate", ex); }
    }

    private static int Index(JToken? value)
    {
        if (value?.Type != JTokenType.String || !int.TryParse((string?)value,
            NumberStyles.None, CultureInfo.InvariantCulture, out var index) || index < 0)
            throw new InvalidOperationException("schedule_manager_instance_index_invalid");
        return index;
    }

    // Same read-only inventory command and object formats as Maa's MuMu Finder.
    // Use the selected endpoint, never infer an instance from a port formula.
    internal static int FindIndex(string inventory, JObject connection)
    {
        var response = Read(inventory);
        var rows = response["index"] != null ? new[] { response } : response.Properties()
            .Select(p => p.Value).OfType<JObject>().ToArray();
        var matches = rows.Where(row => row["adb_host_ip"]?.Type == JTokenType.String
            && (string?)row["adb_host_ip"] == "127.0.0.1"
            && row["adb_port"]?.Type == JTokenType.Integer
            && long.TryParse(row["adb_port"]!.ToString(), out var port) && port is >= 1 and <= 65535
            && (string?)connection["serial"] == "127.0.0.1:" + port).ToArray();
        if (matches.Length != 1)
            throw new InvalidOperationException("所选 MuMu 地址未匹配到唯一实例，请重新搜索并选择设备。[schedule_manager_selected_instance_not_unique]");
        return Index(matches[0]["index"]);
    }

    internal static string[] Arguments(PcrManagerRead command, int? index)
    {
        if (command == PcrManagerRead.Instances && index == null)
            return new[] { "info", "--vmindex", "all" };
        if (index == null || index < 0) throw new InvalidOperationException("schedule_manager_instance_index_invalid");
        var selected = index.Value.ToString(CultureInfo.InvariantCulture);
        return command switch {
            PcrManagerRead.Info => new[] { "info", "--vmindex", selected },
            PcrManagerRead.AppInfo => new[] { "control", "--vmindex", selected, "app", "info", "--package", PcrMuMuDisplay.Package },
            _ => throw new InvalidOperationException("schedule_manager_read_command_invalid")
        };
    }

    internal static JObject Parse(string info, string app, JObject connection)
    {
        var row = Read(info); var package = Read(app);
        long Integer(string key)
        {
            if (row[key]?.Type != JTokenType.Integer || !long.TryParse(row[key]!.ToString(), out var n))
                throw new InvalidOperationException("schedule_manager_missing_or_invalid:" + key);
            return n;
        }
        if (Integer("error_code") != 0 || Integer("launch_err_code") != 0)
            throw new InvalidOperationException("schedule_manager_vendor_error");
        if (package["error_code"] != null && (package["error_code"]?.Type != JTokenType.Integer
            || package["error_code"]!.ToString() != "0"))
            throw new InvalidOperationException("schedule_manager_app_vendor_error");
        if ((string?)row["info_source"] != "rpc" || row["is_android_started"]?.Type != JTokenType.Boolean
            || (bool?)row["is_android_started"] != true || row["is_process_started"]?.Type != JTokenType.Boolean
            || (bool?)row["is_process_started"] != true || (string?)package["state"] != "running")
            throw new InvalidOperationException("schedule_manager_target_not_running");
        var index = Index(row["index"]);
        var port = Integer("adb_port");
        if (port is < 1 or > 65535 || (string?)row["adb_host_ip"] != "127.0.0.1"
            || (string?)connection["serial"] != "127.0.0.1:" + port)
            throw new InvalidOperationException("schedule_manager_endpoint_mismatch");
        var created = Integer("created_timestamp");
        if (created <= 0) throw new InvalidOperationException("schedule_manager_missing_or_invalid:created_timestamp");
        return new JObject { ["source"] = "mumu_manager_local_v1", ["installation"] = InstallationFor(connection),
            ["index"] = index, ["created_timestamp"] = created, ["app_package"] = PcrMuMuDisplay.Package };
    }

    internal static bool SameLegacy(JObject legacy, JObject observed) =>
        legacy["index"]?.Type == JTokenType.Integer && (int?)legacy["index"] == (int?)observed["index"]
        && legacy["app_cloned_index"]?.Type == JTokenType.Integer && (int?)legacy["app_cloned_index"] == 0
        && (string?)legacy["app_package"] == (string?)observed["app_package"]
        && string.Equals(Path.GetFullPath((string?)legacy["path"] ?? ""),
            Path.GetFullPath((string)observed["installation"]!), StringComparison.OrdinalIgnoreCase);
}
