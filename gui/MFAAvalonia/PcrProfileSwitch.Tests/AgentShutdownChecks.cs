using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using MFAAvalonia.Pcr;
using MFAAvalonia.Extensions.MaaFW;
using Newtonsoft.Json.Linq;

internal static class AgentShutdownChecks
{
    internal static void Run(string[] args)
    {
        const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.Static | BindingFlags.NonPublic;
        var assembly = typeof(PcrAlphaOwner).Assembly;
        var type = assembly.GetType("MFAAvalonia.Pcr.PcrAgentShutdown", true)!;
        var count = 0;
        void Require(bool value, string check)
        { if (!value) throw new InvalidOperationException(check); count++; }
        object New()
        {
            var value = Activator.CreateInstance(type, nonPublic: true)!;
            type.GetField("grace", Hidden)!.SetValue(value, TimeSpan.FromMilliseconds(40));
            type.GetField("afterExit", Hidden)!.SetValue(value, TimeSpan.FromMilliseconds(80));
            return value;
        }
        bool Run(object value, Action operation, Action terminate)
            => (bool)type.GetMethod("TryRun", Hidden)!.Invoke(value, [operation, terminate])!;
        bool Flag(object value, string name) => (bool)type.GetProperty(name, Hidden)!.GetValue(value)!;
        var normal = New();
        var operations = 0; var kills = 0;
        Require(Run(normal, () => operations++, () => kills++), "normal_shutdown_completes");
        Require(operations == 1 && kills == 0 && !Flag(normal, "Forced") && Flag(normal, "Quiescent"), "normal_shutdown_never_kills");

        foreach (var kind in new[] { "stop_wait", "link_stop" })
        {
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            var forced = New();
            var watch = Stopwatch.StartNew();
            Require(Run(forced, () => { entered.Set(); release.Wait(); }, () => { kills++; release.Set(); }), kind + "_released_after_private_termination");
            Require(entered.IsSet && Flag(forced, "Forced") && Flag(forced, "Quiescent")
                && watch.ElapsedMilliseconds < 2000, kind + "_is_bounded_and_original_call_returned");
        }
        foreach (var kind in new[] { "stop_never_returns", "link_never_returns", "termination_failure" })
        {
            using var release = new ManualResetEventSlim();
            var blocked = New();
            var dispatched = 0;
            Require(!Run(blocked, () => { Interlocked.Increment(ref dispatched); release.Wait(); }, () => {
                if (kind == "termination_failure") throw new InvalidOperationException("injected_job_failure");
            }), kind + "_remains_unconfirmed");
            Require(!Flag(blocked, "Quiescent") && Flag(blocked, "Forced"), kind + "_retains_native_owner");
            Require(!Run(blocked, () => dispatched++, () => kills++) && dispatched == 1, kind + "_refuses_another_dispatch");
            CheckRetainedOwner(blocked);
            release.Set();
        }
        var fault = New();
        var beforeFault = kills;
        Require(!Run(fault, () => throw new IOException("injected_native_error"), () => kills++)
            && !Flag(fault, "Quiescent"), "native_error_is_not_success_or_release");
        Require(kills == beforeFault + 1 && Flag(fault, "Forced"), "immediate_native_failure_still_cleans_private_child");
        var cleanup = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        CheckRetainedOwner(New(), cleanup.Task);
        cleanup.SetResult();
        CheckRetainedOwner(New(), cleanupUnconfirmed: true);

        if (OperatingSystem.IsWindows()) CheckOwnedTcp(args);
        Console.WriteLine($"PCR Agent shutdown checks passed: {count}; synthetic Stop/LinkStop faults, no device/game input");

        void CheckOwnedTcp(string[] arguments)
        {
            var childType = assembly.GetType("MFAAvalonia.Pcr.PcrWindowsChild", true)!;
            var connection = assembly.GetType("MFAAvalonia.Pcr.PcrAgentConnection", true)!;
            var read = connection.GetMethod("EstablishedPort", Hidden)!;
            var query = connection.GetField("readTable", Hidden)!;
            var originalQuery = query.GetValue(null);
            try
            {
                tableReads = 0;
                query.SetValue(null, Delegate.CreateDelegate(query.FieldType,
                    typeof(AgentShutdownChecks).GetMethod(nameof(GrowingTable), Hidden)!));
                try { read.Invoke(null, [123, 456]); }
                catch (TargetInvocationException error) when (error.InnerException is InvalidOperationException) { }
                Require(tableReads == 3, "tcp_table_growth_reallocates_before_classifying_connection");
                query.SetValue(null, Delegate.CreateDelegate(query.FieldType,
                    typeof(AgentShutdownChecks).GetMethod(nameof(ReusedPidTable), Hidden)!));
                var scoped = connection.GetMethod("EstablishedConnection", Hidden)!;
                foreach (var members in new[] { Array.Empty<int>(), new[] { 124 } })
                {
                    try { scoped.Invoke(null, [members, 456]); throw new Exception("exited_or_foreign_pid_accepted"); }
                    catch (TargetInvocationException error) when (error.InnerException is InvalidOperationException) { count++; }
                }
            }
            finally { query.SetValue(null, originalQuery); }
            using var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            var port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var child = Activator.CreateInstance(childType, nonPublic: true)!;
            try
            {
                var script = "import socket,sys; s=socket.create_connection(('127.0.0.1',int(sys.argv[1]))); sys.stdin.readline(); s.close(); s=socket.create_connection(('127.0.0.1',int(sys.argv[1]))); sys.stdin.readline(); extra=socket.create_connection(('127.0.0.1',int(sys.argv[1]))); sys.stdin.readline()";
                childType.GetMethod("Start", Hidden)!.Invoke(child, [Path.GetFullPath(arguments[2]), Path.GetFullPath(arguments[1]), new[] { "-c", script, port.ToString() }]);
                using var first = listener.AcceptTcpClientAsync().WaitAsync(TimeSpan.FromSeconds(5)).GetAwaiter().GetResult();
                var pid = (int)childType.GetProperty("Id", Hidden)!.GetValue(child)!;
                var members = (int[])childType.GetProperty("ProcessIds", Hidden)!.GetValue(child)!;
                Require(Array.IndexOf(members, pid) >= 0 && Array.IndexOf(members, Environment.ProcessId) < 0,
                    "tcp_process_ids_come_from_private_job_not_arbitrary_user_processes");
                int Read(int process, int server) => (int)read.Invoke(null, [process, server])!;
                var original = Read(pid, port);
                Require(original == ((IPEndPoint)first.Client.RemoteEndPoint!).Port, "tcp_snapshot_binds_owned_pid_and_both_endpoints");
                foreach (var values in new[] { (Environment.ProcessId, port), (pid, port + 1) })
                {
                    try { Read(values.Item1, values.Item2); throw new InvalidOperationException("wrong_tcp_identity_accepted"); }
                    catch (TargetInvocationException error) when (error.InnerException is InvalidOperationException) { count++; }
                }
                childType.GetMethod("Send", Hidden)!.Invoke(child, ["reconnect"]);
                using var second = listener.AcceptTcpClientAsync().WaitAsync(TimeSpan.FromSeconds(5)).GetAwaiter().GetResult();
                var changed = Read(pid, port);
                Require(changed != original, "tcp_reconnect_changes_binding_even_if_disconnect_poll_was_missed");
                using var foreign = new TcpClient(); foreign.Connect(IPAddress.Loopback, port);
                using var foreignPeer = listener.AcceptTcpClientAsync().WaitAsync(TimeSpan.FromSeconds(5)).GetAwaiter().GetResult();
                Require(Read(pid, port) == changed, "same_port_connection_outside_private_job_is_not_the_agent");
                childType.GetMethod("Send", Hidden)!.Invoke(child, ["second-socket"]);
                using var extra = listener.AcceptTcpClientAsync().WaitAsync(TimeSpan.FromSeconds(5)).GetAwaiter().GetResult();
                try { Read(pid, port); throw new InvalidOperationException("ambiguous_owned_sockets_accepted"); }
                catch (TargetInvocationException error) when (error.InnerException is InvalidOperationException) { count++; }
                extra.Dispose();
                second.Dispose();
                var ended = Stopwatch.StartNew();
                while (true)
                {
                    try { Read(pid, port); }
                    catch (TargetInvocationException error) when (error.InnerException is InvalidOperationException) { break; }
                    if (ended.ElapsedMilliseconds >= 2000) throw new InvalidOperationException("tcp_disconnect_not_observed");
                    Thread.Sleep(10);
                }
                Require(ended.ElapsedMilliseconds < 2000, "tcp_disconnect_observed_without_sdk_or_preview");
            }
            finally { ((IDisposable)child).Dispose(); }
        }
        void CheckRetainedOwner(object blocked, Task? cleanup = null, bool cleanupUnconfirmed = false)
        {
            var owner = typeof(PcrAlphaOwner);
            var current = owner.GetField("current", Hidden)!;
            var sessionType = owner.GetNestedType("Session", BindingFlags.NonPublic)!;
            var childType = owner.GetNestedType("ChildSession", BindingFlags.NonPublic)!;
            var configType = owner.GetNestedType("ValidatedConfig", BindingFlags.NonPublic)!;
            object Construct(Type target, params object?[] values) => Activator.CreateInstance(target,
                BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public, null, values, null)!;
            var processor = (MaaProcessor)RuntimeHelpers.GetUninitializedObject(typeof(MaaProcessor));
            var session = Construct(sessionType, processor, new JObject(), Construct(configType, "{}"), new PcrStartConsent("", "", [], false));
            var child = RuntimeHelpers.GetUninitializedObject(childType);
            childType.GetField("shutdown", Hidden)!.SetValue(child, blocked);
            if (cleanup != null)
            {
                childType.GetField("faultStarted", Hidden)!.SetValue(child, 1);
                childType.GetField("faultCleanup", Hidden)!.SetValue(child, cleanup);
            }
            sessionType.GetField("Child")!.SetValue(session, child);
            sessionType.GetField("Revoked")!.SetValue(session, !cleanupUnconfirmed);
            current.SetValue(null, session);
            try
            {
                if (cleanupUnconfirmed)
                {
                    owner.GetMethod("MarkCleanupUnconfirmed", Hidden)!.Invoke(null, [processor]);
                    Require((bool)sessionType.GetField("Revoked")!.GetValue(session)!
                        && ((CancellationTokenSource)sessionType.GetField("Cancel")!.GetValue(session)!).IsCancellationRequested,
                        "cleanup_failure_revokes_authority_before_another_gui_stop");
                    try { PcrAlphaOwner.Require(processor); throw new Exception("cleanup_failure_authorized_work"); }
                    catch (InvalidOperationException) { count++; }
                }
                Require(!PcrAlphaOwner.ClaimStart(processor), "pending_native_call_cannot_start_again");
                for (var attempt = 0; attempt < (cleanupUnconfirmed ? 2 : 1); attempt++)
                {
                    try { PcrAlphaOwner.Release(processor); throw new Exception("pending_owner_released"); }
                    catch (InvalidOperationException) { count++; }
                    Require(ReferenceEquals(current.GetValue(null), session), "repeated_empty_cleanup_cannot_release_unconfirmed_owner");
                }
                Require(ReferenceEquals(current.GetValue(null), session), "pending_native_call_retains_original_owner");
            }
            finally
            {
                current.SetValue(null, null);
                ((CancellationTokenSource)sessionType.GetField("Cancel")!.GetValue(session)!).Dispose();
            }
        }
    }
    private static int tableReads;
    private static uint GrowingTable(IntPtr table, ref uint size, bool order, int family, int tableClass, uint reserved)
    {
        tableReads++;
        if (table == IntPtr.Zero) { size = 28; return 122; }
        if (tableReads == 2) { size = 52; return 122; }
        Marshal.WriteInt32(table, 0);
        return 0;
    }
    private static uint ReusedPidTable(IntPtr table, ref uint size, bool order, int family, int tableClass, uint reserved)
    {
        size = 28;
        if (table == IntPtr.Zero) return 122;
        var values = new[] { 1, 5, 0x0100007f, 0x3930, 0x0100007f, 0xc801, 123 };
        for (var index = 0; index < values.Length; index++) Marshal.WriteInt32(table, index * 4, values[index]);
        return 0;
    }
}
