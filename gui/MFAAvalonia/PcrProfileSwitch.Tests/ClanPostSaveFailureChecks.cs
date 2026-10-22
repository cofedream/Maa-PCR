using System;
using System.IO;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using MFAAvalonia.Pcr;
using Newtonsoft.Json.Linq;

internal static class ClanPostSaveFailureChecks
{
    public static void Run(string sourceRoot, string directory)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("This ACL fault check requires Windows.");
        ClanWorkflowChecks.Run(sourceRoot, directory);
        var root = Path.Combine(Path.GetFullPath(directory), "clan-workflow"); var config = Path.Combine(root, "config");
        var guide = Path.Combine(config, "agent-guide-review.json"); var original = PcrClanGuide.Read(guide);
        var daily = PcrClanDailyPlan.Read(PcrClanDailyPlan.PathFor(config), out _);
        var target = PcrClanDailyPlan.Load(daily, 1, guide, config, sourceRoot, DateTimeOffset.Now, out _);
        var faultDirectory = Path.Combine(root, "write-only-fault"); Directory.CreateDirectory(faultDirectory);
        var faultGuide = Path.Combine(faultDirectory, "agent-guide-review.json"); var probe = Path.Combine(faultDirectory, "proof.txt");
        var info = new DirectoryInfo(faultDirectory); var originalAcl = info.GetAccessControl(AccessControlSections.Access);
        var originalSddl = originalAcl.GetSecurityDescriptorSddlForm(AccessControlSections.Access);
        var currentSid = WindowsIdentity.GetCurrent().User ?? throw new InvalidOperationException("Current Windows SID unavailable.");
        var view = new PcrClanGuideView(sourceRoot, faultGuide, config); view.Display(original);
        var count = 0; var restored = false; string? errorType = null;
        void Check(bool value, string label) { if (!value) throw new InvalidOperationException("clan_postsave_" + label); count++; }
        void Denied(Action action, string label)
        {
            try { action(); }
            catch (Exception ex) when (ex.GetBaseException() is UnauthorizedAccessException) { errorType = ex.GetBaseException().GetType().Name; count++; return; }
            throw new InvalidOperationException("clan_postsave_expected_access_denied_" + label);
        }
        var before = view.Capture().SelectedJobId;
        Check(view.HasCurrentConfirmation, "visible_old_selection_initially_confirmed");
        try
        {
            var acl = info.GetAccessControl(AccessControlSections.Access);
            acl.AddAccessRule(new FileSystemAccessRule(currentSid, FileSystemRights.ReadData, InheritanceFlags.ObjectInherit, PropagationFlags.InheritOnly, AccessControlType.Deny));
            info.SetAccessControl(acl);
            File.WriteAllText(probe, "write succeeded; read must fail", new UTF8Encoding(false));
            Check(File.Exists(probe), "folder_allows_new_file_write");
            Denied(() => File.ReadAllBytes(probe), "proof_new_file_read");
            var load = typeof(PcrClanGuideView).GetMethod("LoadReviewed", BindingFlags.NonPublic | BindingFlags.Instance)!;
            Denied(() => load.Invoke(view, new object?[] { target, null }), "actual_loadreviewed_postsave_read");
            Check(File.Exists(faultGuide), "real_save_created_destination_before_read_failure");
            Denied(() => PcrClanGuide.Read(faultGuide), "destination_cannot_be_read");
            Check(view.Capture().SelectedJobId == before && view.Capture().ConfirmedFor == null && !view.HasCurrentConfirmation
                && view.ExecutionConfirmationForStart == null, "old_visible_selection_cannot_authorize_start");
            Check(view.StatusText.Contains("旧显示已取消确认"), "visible_failure_explains_confirmation_revocation");
            var rejectedSave = false; try { view.ConfirmAndSave(); } catch (InvalidOperationException) { rejectedSave = true; }
            Check(rejectedSave, "unloaded_editor_cannot_overwrite_failed_destination");
        }
        finally
        {
            var restoreAcl = new DirectorySecurity(); restoreAcl.SetSecurityDescriptorSddlForm(originalSddl, AccessControlSections.Access); info.SetAccessControl(restoreAcl);
            foreach (var path in Directory.EnumerateFiles(faultDirectory))
            {
                var acl = new FileInfo(path).GetAccessControl(AccessControlSections.Access);
                acl.SetAccessRuleProtection(true, false);
                acl.AddAccessRule(new FileSystemAccessRule(currentSid, FileSystemRights.FullControl, AccessControlType.Allow));
                new FileInfo(path).SetAccessControl(acl);
            }
            restored = info.GetAccessControl(AccessControlSections.Access).GetSecurityDescriptorSddlForm(AccessControlSections.Access)
                == originalSddl;
            File.WriteAllText(Path.Combine(root, "acl-restoration.json"), new JObject { ["restored"] = restored, ["original_access_sddl"] = originalSddl,
                ["restored_access_sddl"] = info.GetAccessControl(AccessControlSections.Access).GetSecurityDescriptorSddlForm(AccessControlSections.Access), ["directory"] = faultDirectory }.ToString(), new UTF8Encoding(false));
        }
        Check(restored && File.ReadAllText(probe, Encoding.UTF8).StartsWith("write succeeded"), "original_folder_acl_restored_and_files_readable");
        var readBack = PcrClanGuide.Read(faultGuide);
        Check(readBack.SelectedJobId == target.SelectedJobId && readBack.ConfirmedFor == target.ConfirmedFor && readBack.SavedJobs.Count == 3, "actual_saved_target_preserved_without_blind_rollback");
        view.Reload(); Check(view.HasCurrentConfirmation && view.Capture().SelectedJobId == target.SelectedJobId, "explicit_reload_recovers_actual_saved_target");
        Check(PcrClanGuide.Read(guide).SelectedJobId == original.SelectedJobId, "source_guide_unchanged");
        File.WriteAllText(Path.Combine(root, "postsave-checks.json"), new JObject { ["checks"] = count, ["fault"] = "inherited deny ReadData on new files in this isolated folder only",
            ["real_method"] = "PcrClanGuideView.LoadReviewed", ["error_type"] = errorType, ["acl_restored"] = restored, ["game_input"] = false,
            ["source_guide"] = guide, ["fault_destination"] = faultGuide, ["saved_target"] = target.SelectedJobId }.ToString(), new UTF8Encoding(false));
        Console.WriteLine($"PCR clan post-save ACL checks passed: {count}; actual save/read failure, confirmation revoked, ACL restored, game input=0");
    }
}
