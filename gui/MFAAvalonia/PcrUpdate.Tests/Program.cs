using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using MFAAvalonia.Pcr;
using MFAAvalonia.ViewModels.UsersControls.Settings;
using Newtonsoft.Json.Linq;
using SukiUI.MessageBox;

if (args.Length == 2 && Path.IsPathFullyQualified(args[0]) && args[1] == "--ocr-lock-crash")
{
    using var held = new FileStream(Path.Combine(args[0], ".quota-lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
    Environment.Exit(17); // Skip managed cleanup while the test child owns the OS lock.
}

if (args.Length is not (1 or 2) || !Path.IsPathFullyQualified(args[0])
    || args.Length == 2 && args[1] != "--controls-only")
{
    Console.Error.WriteLine("PCR GUI update checks: an absolute evidence directory is required.");
    return 2;
}
try
{
if (args.Length == 2)
{
    UpdateViewChecks.Run(args[0]);
    return 0;
}
var count = 0;
AppPathsChecks.Run();
void Require(bool value, string test)
{
    if (!value) throw new InvalidOperationException(test);
    count++;
}
void Rejected(string json)
{
    try { PcrSoftwareUpdateService.RequireCheckResponse(JObject.Parse(json)); }
    catch (InvalidOperationException) { count++; return; }
    throw new InvalidOperationException("invalid_update_response_accepted");
}
Require(!PcrSoftwareUpdateService.InstallConfirmed(null), "closed_confirmation_cancels_without_null_reference");
Require(PcrSoftwareUpdateService.InstallConfirmed(SukiMessageBoxResult.Yes), "explicit_yes_confirms_install");
Require(!PcrSoftwareUpdateService.InstallConfirmed(SukiMessageBoxResult.No), "no_cancels_install");
Require(!PcrSoftwareUpdateService.InstallConfirmed(SukiMessageBoxResult.Close), "close_cancels_install");
Require(!PcrSoftwareUpdateService.InstallConfirmed("Yes"), "untyped_confirmation_is_not_authorization");
var configureProxy = typeof(PcrSoftwareUpdateService).GetMethod("ConfigureProxy", BindingFlags.Static | BindingFlags.NonPublic)!;
foreach (var command in new[] { "inspect", "launch" })
{
    var process = new ProcessStartInfo();
    process.Environment["HTTPS_PROXY"] = "unchanged";
    configureProxy.Invoke(null, new object[] { process, command, "broken-offline-proxy", VersionUpdateSettingsUserControlModel.UpdateProxyType.Socks5 });
    Require(process.Environment["HTTPS_PROXY"] == "unchanged", command + "_ignores_online_proxy_settings");
}
foreach (var command in new[] { "check", "download" })
{
    var process = new ProcessStartInfo();
    configureProxy.Invoke(null, new object[] { process, command, "127.0.0.1:7890", VersionUpdateSettingsUserControlModel.UpdateProxyType.Http });
    Require(process.Environment["HTTPS_PROXY"] == "http://127.0.0.1:7890"
        && process.Environment["HTTP_PROXY"] == "http://127.0.0.1:7890", command + "_honors_http_proxy");
    try
    {
        configureProxy.Invoke(null, new object[] { process, command, "socks5://127.0.0.1:7890", VersionUpdateSettingsUserControlModel.UpdateProxyType.Socks5 });
        throw new InvalidOperationException("unsupported_online_proxy_accepted");
    }
    catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException { Message: "unsupported_proxy_use_http_https" }) { count++; }
}
var inspected = new JObject {
    ["status"] = "validated", ["current_revision"] = 52, ["current_version"] = "v0.1.8",
    ["revision"] = 53, ["version"] = "v0.1.8-r53-candidate", ["archive_kind"] = "delta",
    ["manifest_sha256"] = new string('a', 64), ["archive_sha256"] = new string('b', 64) };
Require(PcrSoftwareUpdateService.LocalPackageSummary(inspected).Contains("增量补丁")
    && PcrSoftwareUpdateService.LocalPackageSummary(inspected).Contains("R52 → v0.1.8-r53-candidate / R53"), "verified_local_target_before_confirmation");
inspected["archive_kind"] = "full";
Require(PcrSoftwareUpdateService.LocalPackageSummary(inspected).Contains("完整运行包"), "verified_full_package_summary");
foreach (var field in new[] { "status", "current_revision", "current_version", "revision", "version", "archive_kind", "manifest_sha256", "archive_sha256" })
{
    var invalid = (JObject)inspected.DeepClone();
    invalid.Remove(field);
    try { PcrSoftwareUpdateService.LocalPackageSummary(invalid); }
    catch (InvalidOperationException) { count++; continue; }
    throw new InvalidOperationException("invalid_local_summary_accepted:" + field);
}
Require(PcrSoftwareUpdateService.FailureMessage("archive_manifest_missing").Contains("交付 ZIP"), "delivery_wrapper_guidance");
Require(PcrSoftwareUpdateService.FailureMessage("update_not_newer").Contains("专用替换工具"), "same_revision_test_package_guidance");
Require(PcrSoftwareUpdateService.FailureMessage("patch_base_or_target_mismatch").Contains("不匹配"), "wrong_patch_guidance");
Require(PcrSoftwareUpdateService.FailureMessage("unexpected_error").Contains("unexpected_error"), "unknown_error_keeps_diagnostic_code");
var operation = typeof(PcrSoftwareUpdateService).GetField("operation", BindingFlags.Static | BindingFlags.NonPublic)!;
operation.SetValue(null, 1);
try
{
    await PcrSoftwareUpdateService.InstallOnlineAsync();
    await PcrSoftwareUpdateService.RecoverAsync();
    Require((int)operation.GetValue(null)! == 1, "busy_update_does_not_open_another_confirmation");
}
finally { operation.SetValue(null, 0); }
PcrSoftwareUpdateService.RequireCheckResponse(JObject.Parse("{\"status\":\"up_to_date\",\"current_revision\":39}"));
count++;
PcrSoftwareUpdateService.RequireCheckResponse(JObject.Parse("{\"status\":\"update_available\",\"current_revision\":39,\"revision\":40,\"version\":\"v0.1.1\",\"asset\":{}}"));
count++;
Rejected("{\"status\":\"up_to_date\",\"current_revision\":\"39\"}");
Rejected("{\"status\":\"update_available\",\"current_revision\":39,\"revision\":38,\"version\":\"v0.1.1\",\"asset\":{}}");
Rejected("{\"status\":\"update_available\",\"current_revision\":39,\"revision\":39,\"version\":\"v0.1.1\",\"asset\":{}}");
Rejected("{\"status\":\"update_available\",\"current_revision\":39,\"revision\":40,\"version\":\"\",\"asset\":{}}");
Rejected("{\"status\":\"update_available\",\"current_revision\":39,\"revision\":40,\"version\":\"v0.1.1\"}");
Rejected("{\"status\":\"failed\",\"current_revision\":39}");
Require(PcrSoftwareUpdateService.DownloadProgress(JObject.Parse("{\"phase\":\"download\",\"bytes\":50,\"total\":100}")) == 50,
    "python_download_progress_contract");
Require(PcrSoftwareUpdateService.DownloadProgress(JObject.Parse("{\"stage\":\"download\",\"bytes\":50,\"total\":100}")) == null,
    "unknown_progress_message_ignored");
Require(PcrSoftwareUpdateService.DownloadProgress(JObject.Parse("{\"phase\":\"download\",\"bytes\":\"50\",\"total\":100}")) == null,
    "non_integer_progress_ignored");
Require(PcrSoftwareUpdateService.DownloadProgress(JObject.Parse("{\"phase\":\"download\",\"bytes\":101,\"total\":100}")) == null,
    "out_of_range_progress_ignored");
Require(PcrSoftwareUpdateService.DownloadProgress(JObject.Parse("{\"phase\":\"download\",\"bytes\":0,\"total\":0}")) == null,
    "zero_total_progress_ignored");
var testRoot = Path.Combine(args[0], "gui-update-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(testRoot);
Require(PcrSoftwareUpdateService.InstalledVersion(testRoot) == "Maa-PCR（开发模式）", "missing_manifest_is_not_a_release");
File.WriteAllText(Path.Combine(testRoot, "package-manifest.json"), "{\"update\":{\"revision\":39,\"version\":\"v0.1.0\"}}");
Require(PcrSoftwareUpdateService.InstalledVersion(testRoot) == "v0.1.0 / R39", "installed_business_version");
File.WriteAllText(Path.Combine(testRoot, "package-manifest.json"), "{\"update\":{\"revision\":53,\"version\":\"v0.1.9\"}}");
Require(PcrSoftwareUpdateService.InstalledVersion(testRoot) == "v0.1.9 / R53", "updated_manifest_replaces_display_version");
var displayManifest = Path.Combine(AppContext.BaseDirectory, "package-manifest.json");
if (Directory.Exists(displayManifest)) throw new InvalidOperationException("display_manifest_fixture_is_directory");
var savedDisplayManifest = File.Exists(displayManifest) ? File.ReadAllBytes(displayManifest) : null;
try
{
    var versionRoot = (MFAAvalonia.ViewModels.Windows.RootViewModel)System.Runtime.CompilerServices.RuntimeHelpers.GetUninitializedObject(
        typeof(MFAAvalonia.ViewModels.Windows.RootViewModel));
    var versionSamples = new List<(int Revision, string Version)> { (53, "v0.1.9"), (57, "v0.1.12") };
    if (savedDisplayManifest is not null)
    {
        var installed = JObject.Parse(Encoding.UTF8.GetString(savedDisplayManifest))["update"]!;
        versionSamples.Add((installed["revision"]!.Value<int>(), installed["version"]!.Value<string>()!));
    }
    foreach (var sample in versionSamples)
    {
        File.WriteAllText(displayManifest, new JObject { ["update"] = new JObject {
            ["revision"] = sample.Revision, ["version"] = sample.Version } }.ToString());
        var expectedVersion = $"{sample.Version} / R{sample.Revision}";
        Require(MFAAvalonia.ViewModels.Windows.RootViewModel.Version == expectedVersion,
            "pcr_version_reads_replaced_manifest_R" + sample.Revision);
        versionRoot.ShowResourceVersion("0.1.3-alpha");
        Require(versionRoot.ResourceVersion == expectedVersion,
            "pcr_header_ignores_legacy_interface_version_R" + sample.Revision);
        versionRoot.ShowResourceVersion("debug");
        Require(versionRoot.ResourceVersion == expectedVersion,
            "pcr_header_keeps_manifest_identity_on_resource_reload_R" + sample.Revision);
        var title = (string)new MFAAvalonia.Helper.Converters.TitleConverter().Convert(
            new object?[] { null, false, "Maa-PCR", MFAAvalonia.ViewModels.Windows.RootViewModel.Version,
                "PCR Alpha", versionRoot.ResourceVersion, true }, typeof(string), null, System.Globalization.CultureInfo.InvariantCulture)!;
        Require(title.Contains(expectedVersion) && !title.Contains("0.1.3-alpha")
            && (sample.Revision != 57 || !title.Contains("v0.1.9")),
            "pcr_window_title_uses_replaced_manifest_R" + sample.Revision);
        Console.WriteLine($"PCR installed version display verified: {expectedVersion}");
    }
}
finally
{
    if (savedDisplayManifest is null) File.Delete(displayManifest);
    else File.WriteAllBytes(displayManifest, savedDisplayManifest);
}
Require(savedDisplayManifest is null ? !File.Exists(displayManifest)
    : File.ReadAllBytes(displayManifest).AsSpan().SequenceEqual(savedDisplayManifest),
    "display_manifest_fixture_restored_byte_for_byte");
File.WriteAllText(Path.Combine(testRoot, "package-manifest.json"), "{\"update\":{\"revision\":\"39\",\"version\":\"v2.16.6\"}}");
Require(PcrSoftwareUpdateService.InstalledVersion(testRoot) == "Maa-PCR（版本清单无效）", "gui_semver_is_not_business_revision");
var recoveryPlayer = Path.Combine(testRoot, "recovery-player");
var markerRoot = Path.Combine(recoveryPlayer, ".runtime", "player-maintenance");
var beforeRoot = Path.Combine(markerRoot, "update-" + new string('1', 32), "before");
Directory.CreateDirectory(beforeRoot);
var oldManifest = Encoding.UTF8.GetBytes("{\"update\":{\"revision\":39,\"version\":\"v0.1.0\"}}");
File.WriteAllBytes(Path.Combine(beforeRoot, "package-manifest.json"), oldManifest);
var baseHash = Convert.ToHexString(SHA256.HashData(oldManifest)).ToLowerInvariant();
File.WriteAllText(Path.Combine(markerRoot, "update-in-progress.json"), new JObject {
    ["workspace"] = "update-" + new string('1', 32), ["old_manifest_sha256"] = baseHash,
    ["new_manifest_sha256"] = new string('2', 64) }.ToString());
var workspace = Path.Combine(testRoot, "Maa-PCR-update", "update-" + new string('3', 32));
var files = new JArray();
foreach (var name in new[] { "runtime/python/python.exe", "scripts/player-update.py", "scripts/player-maintenance.py",
             "src/pcr_maa/__init__.py", "src/pcr_maa/player_update.py", "src/pcr_maa/player_maintenance.py" })
{
    var path = Path.Combine(workspace, name);
    Directory.CreateDirectory(Path.GetDirectoryName(path)!);
    var bytes = Encoding.UTF8.GetBytes("inert-fixture:" + name);
    File.WriteAllBytes(path, bytes);
    files.Add(new JObject { ["path"] = name, ["size"] = bytes.Length,
        ["sha256"] = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant() });
}
var receipt = new JObject { ["schema_version"] = 1, ["player"] = recoveryPlayer, ["workspace"] = workspace,
    ["python"] = "runtime/python/python.exe", ["script"] = "scripts/player-update.py", ["files"] = files,
    ["base_revision"] = 39, ["base_manifest_sha256"] = baseHash };
var receiptPath = Path.Combine(markerRoot, "external-updater.json");
File.WriteAllText(receiptPath, receipt.ToString());
var bootstrap = PcrSoftwareUpdateService.RecoveryBootstrap(recoveryPlayer);
Require(bootstrap.Python == Path.Combine(workspace, "runtime", "python", "python.exe")
    && !Directory.Exists(Path.Combine(recoveryPlayer, "runtime")), "recovery_does_not_require_installed_python");
void RecoveryRejected(JObject invalid)
{
    File.WriteAllText(receiptPath, invalid.ToString());
    try { PcrSoftwareUpdateService.RecoveryBootstrap(recoveryPlayer); }
    catch (InvalidOperationException) { count++; return; }
    throw new InvalidOperationException("invalid_recovery_bootstrap_accepted");
}
var wrongBase = (JObject)receipt.DeepClone();
wrongBase["base_revision"] = 38;
RecoveryRejected(wrongBase);
var wrongHash = (JObject)receipt.DeepClone();
wrongHash["base_manifest_sha256"] = new string('0', 64);
RecoveryRejected(wrongHash);
var traversal = (JObject)receipt.DeepClone();
traversal["files"]![0]!["path"] = "../escape.exe";
RecoveryRejected(traversal);
var duplicate = (JObject)receipt.DeepClone();
((JArray)duplicate["files"]!).Add(duplicate["files"]![0]!.DeepClone());
RecoveryRejected(duplicate);
var omitted = (JObject)receipt.DeepClone();
((JArray)omitted["files"]!).RemoveAt(5);
RecoveryRejected(omitted);
File.AppendAllText(Path.Combine(workspace, "runtime", "python", "python.exe"), "corrupt");
RecoveryRejected(receipt);
var assembly = typeof(PcrAlphaOwner).Assembly;
var gate = assembly.GetType("MFAAvalonia.Pcr.PcrProfileRunGate", true)!;
var pending = gate.GetField("UpdatePending", BindingFlags.Static | BindingFlags.NonPublic)!;
pending.SetValue(null, true);
try
{
    await PcrAlphaOwner.RequestStartAsync(null!);
    Require(PcrAlphaOwner.SessionCreations == 0 && PcrAlphaOwner.AgentCreations == 0
        && PcrAlphaOwner.Grants == 0, "daily_start_blocked_during_update");
    var labyrinth = assembly.GetType("MFAAvalonia.Pcr.PcrLabyrinthOwner", true)!;
    var start = labyrinth.GetMethod("RequestStartAsync", BindingFlags.Static | BindingFlags.NonPublic)!;
    await (Task)start.Invoke(null, new object?[] { null })!;
    Require((bool)gate.GetProperty("Busy", BindingFlags.Static | BindingFlags.NonPublic)!.GetValue(null)!,
        "labyrinth_start_blocked_during_update");
}
finally { pending.SetValue(null, false); }
Console.WriteLine($"PCR GUI update checks passed: {count}; game inputs=0");
await DependencyChecks.RunAsync(args[0]);
OcrDiagnosticsChecks.Run(args[0]);
PersistenceRecoveryChecks.Run(args[0]);
return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine("PCR GUI update checks failed: " + error);
    return 1;
}
