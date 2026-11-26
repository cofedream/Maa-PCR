using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MFAAvalonia;

internal static class LaunchForwardingChecks
{
    public const string Argument = "--instance-forwarding-probe";

    public static int Run(string[] args)
    {
        try
        {
            if (args.Length == 0) return RunChecks();
            if (args[0] == "job") return Console.ReadLine() == "stop" ? 0 : 1;
            string key = args[1];
            Require(key.StartsWith("LaunchProbe-", StringComparison.Ordinal)
                && Guid.TryParseExact(key["LaunchProbe-".Length..], "N", out _), "invalid probe key");
            if (args[0] == "activate") return AppRuntime.TryForwardActivation(key) ? 0 : 1;
            string[] launchArgs = args[0] == "schedule" ? ["--autostart", "-i", "probe"] : [];
            AppRuntime.Initialize(launchArgs, key);
            try
            {
                return args[0] switch
                {
                    "owner" => RunOwner(),
                    "schedule" => !AppRuntime.IsNewInstance && AppRuntime.TryForwardLaunchCommand() ? 0 : 1,
                    "reopen" => AppRuntime.IsNewInstance ? 0 : 1,
                    _ => throw new ArgumentException(args[0]),
                };
            }
            finally
            {
                AppRuntime.ReleaseMutex();
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static int RunOwner()
    {
        Require(AppRuntime.IsNewInstance, "test owner failed to acquire the test mutex");
        using var job = StartChild("job");
        int handled = 0;
        AppRuntime.RegisterLaunchCommandHandler(async command =>
        {
            // Match the production UI dispatcher returning an unfinished Task.
            await Task.Delay(TimeSpan.FromSeconds(4));
            Interlocked.Increment(ref handled);
            Console.WriteLine($"{command.AutoStart}/{command.QuitAfterRun}/{command.ForceStart}/{command.InstanceSelector}");
            return true;
        });
        try
        {
            Console.WriteLine("ready");
            Require(Console.ReadLine() == "stop", "missing owner stop");
            Require(!job.HasExited && Volatile.Read(ref handled) == 2, "job lost or dispatch count changed");
            return 0;
        }
        finally
        {
            if (!job.HasExited) job.StandardInput.WriteLine("stop");
            CheckExit(job);
        }
    }

    private static int RunChecks()
    {
        string key = $"LaunchProbe-{Guid.NewGuid():N}";
        using var owner = StartChild("owner", key);
        try
        {
            Require(Read(owner) == "ready", "owner startup");
            using var plain = StartChild("activate", key);
            CheckExit(plain);
            using var scheduled = StartChild("schedule", key);
            CheckExit(scheduled);
            string[] expected = ["False/False/False/", "True/False/False/probe"];
            string[] received = [Read(owner), Read(owner)];
            Require(received.Order().SequenceEqual(expected.Order()), "commands lost, changed or duplicated");
            owner.StandardInput.WriteLine("stop");
            CheckExit(owner);
        }
        finally
        {
            // Closing input also releases the simulated job on exceptional exit.
            owner.StandardInput.Close();
            if (!owner.WaitForExit(5000)) owner.Kill(entireProcessTree: true);
        }
        using var next = StartChild("reopen", key);
        CheckExit(next);
        Console.WriteLine("PASS: busy owner and job survived, both commands dispatched once, reopen acquired mutex");
        return 0;
    }

    private static Process StartChild(params string[] args)
    {
        string executable = Environment.ProcessPath!;
        var start = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
        };
        if (string.Equals(Path.GetFileNameWithoutExtension(executable), "dotnet", StringComparison.OrdinalIgnoreCase))
            start.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
        start.ArgumentList.Add(Argument);
        foreach (var arg in args) start.ArgumentList.Add(arg);
        return Process.Start(start) ?? throw new InvalidOperationException("Could not start probe process");
    }

    private static string Read(Process process) => process.StandardOutput.ReadLineAsync()
        .WaitAsync(TimeSpan.FromSeconds(12)).GetAwaiter().GetResult() ?? throw new EndOfStreamException();

    private static void CheckExit(Process process)
    {
        if (!process.WaitForExit(10000))
        {
            process.Kill(entireProcessTree: true);
            throw new TimeoutException($"probe {process.Id} timed out");
        }
        Require(process.ExitCode == 0, $"probe {process.Id} exited {process.ExitCode}");
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
