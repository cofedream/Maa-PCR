using System;
using System.IO;
using System.Reflection;
using System.Text;
using MFAAvalonia.Pcr;

internal static class ClanPlanInputChecks
{
    public static void Run(string root)
    {
        const BindingFlags hidden = BindingFlags.Instance | BindingFlags.NonPublic;
        var type = typeof(PcrClanGuide).GetNestedType("PlanInputs", BindingFlags.NonPublic)!;
        object New() => Activator.CreateInstance(type, true)!;
        object? Call(object inputs, string method, params object[] args) => type.GetMethod(method, hidden)!.Invoke(inputs, args);
        var path = Path.Combine(root, "shared-input-check.json");
        File.WriteAllText(path, "{\"value\":1}", new UTF8Encoding(false));
        var inputs = New(); var bytes = Call(inputs, "Read", path); var json = Call(inputs, "Json", path);
        using (var held = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            if (!ReferenceEquals(bytes, Call(inputs, "Read", path)) || !ReferenceEquals(json, Call(inputs, "Json", path)))
                throw new Exception("clan_inputs_reread_or_reparsed");
        Call(inputs, "Verify");
        foreach (var mutation in new[] { "rewrite", "delete", "replace" })
        {
            File.WriteAllText(path, "{\"value\":1}"); inputs = New(); _ = Call(inputs, "Read", path);
            if (mutation == "delete") File.Delete(path);
            else
            {
                if (mutation == "replace") File.Delete(path);
                File.WriteAllText(path, "{\"value\":2}");
            }
            try { Call(inputs, "Verify"); }
            catch (TargetInvocationException ex) when (ex.InnerException is IOException) { continue; }
            throw new Exception("clan_inputs_did_not_reject_" + mutation);
        }
        var fresh = New(); var latest = (byte[])Call(fresh, "Read", path)!;
        if (!Encoding.UTF8.GetString(latest).Contains(":2")) throw new Exception("clan_inputs_stale_across_operations");
        Console.WriteLine("PCR clan shared-input checks passed: one read/parse per source, final changes rejected, fresh next operation");
    }
}
