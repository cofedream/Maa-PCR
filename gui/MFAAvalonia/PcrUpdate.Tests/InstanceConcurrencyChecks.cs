using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using MFAAvalonia.Configuration;

internal static class InstanceConcurrencyChecks
{
    internal static void Run(string root)
    {
        const string profile = "multiple-settings-owners";
        var file = Path.Combine(root, "config", "instances", profile + ".json");
        File.WriteAllText(file, """{"unknown":"2026-01-02T03:04:05+09:00","remove":"old","strict":"old"}""");
        var first = new InstanceConfiguration(profile);
        var second = new InstanceConfiguration(profile);
        var other = new InstanceConfiguration(profile + "-other");
        first.SetValueAndPersist("strict", "new");
        second.SetValue("ordinary", "keep");
        first.RemoveValue("remove");
        second.SetValues(new Dictionary<string, object> { ["batch1"] = "one", ["batch2"] = "two" });
        other.SetValue("ordinary", "separate");
        var failures = 0;
        first.PersistenceFailed += _ => failures++;
        using (var held = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            first.SetValue("draft", "pending");
            try
            {
                first.SetValueAndPersist("strict", "rejected");
                throw new InvalidOperationException("strict instance write succeeded despite an exclusive writer lock");
            }
            catch (IOException) { }
        }
        if (failures != 1 || first.GetValue("draft", "") != "pending" || first.GetValue("strict", "") != "new")
            throw new InvalidOperationException("ordinary and strict instance saves lost their failure semantics");

        // The other owner changes the file between the failed edit and its retry.
        second.SetValueAndPersist("later", "preserve");
        first.SetValueAndPersist("strict", "retry");
        Parallel.Invoke(() => first.SetValue("thread1", "first"), () => first.SetValueAndPersist("thread2", "second"));
        // This key was added by second after first was constructed.
        first.RemoveValue("batch1");

        using var persisted = JsonDocument.Parse(File.ReadAllText(file));
        var data = persisted.RootElement;
        if (data.GetProperty("unknown").GetString() != "2026-01-02T03:04:05+09:00"
            || data.GetProperty("ordinary").GetString() != "keep" || data.GetProperty("batch2").GetString() != "two"
            || data.GetProperty("strict").GetString() != "retry" || data.GetProperty("later").GetString() != "preserve"
            || data.GetProperty("draft").GetString() != "pending" || data.GetProperty("thread1").GetString() != "first"
            || data.GetProperty("thread2").GetString() != "second" || data.TryGetProperty("remove", out _)
            || data.TryGetProperty("batch1", out _) || other.GetValue("ordinary", "") != "separate")
            throw new InvalidOperationException("instance changes from another owner were overwritten");
    }
}
