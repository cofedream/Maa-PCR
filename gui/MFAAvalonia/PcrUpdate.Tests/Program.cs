using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;
using SukiUI.MessageBox;

if (args.Length != 1 || !Path.IsPathFullyQualified(args[0]))
{
    Console.Error.WriteLine("PCR GUI update checks: an absolute evidence directory is required.");
    return 2;
}
try
{
var count = 0;
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
return 0;
}
catch (Exception error)
{
    Console.Error.WriteLine("PCR GUI update checks failed: " + error);
    return 1;
}
